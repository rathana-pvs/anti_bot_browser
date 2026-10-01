"""Long-lived, loopback-only EasyOCR service shared by automation processes."""

import argparse
import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np


class OcrRuntime:
    CACHE_LIMIT = 128

    def __init__(self, requested_device: str, threads: int):
        self.requested_device = requested_device
        self.threads = threads
        self.device = "initializing"
        self.reader = None
        self.error = None
        self.ready = threading.Event()
        self.inference_lock = threading.Lock()
        self.cache_lock = threading.Lock()
        self.cache: OrderedDict[str, list] = OrderedDict()
        threading.Thread(target=self._initialize, name="ocr-model-loader", daemon=True).start()

    def _initialize(self) -> None:
        try:
            import easyocr
            import torch

            use_cuda = self.requested_device in {"auto", "cuda", "gpu"} and torch.cuda.is_available()
            self.device = "cuda" if use_cuda else "cpu"
            if not use_cuda:
                torch.set_num_threads(max(1, min(4, self.threads)))
            model_dir = Path(
                os.environ.get(
                    "AUTOMATION_OCR_MODEL_DIR",
                    str(Path(__file__).resolve().parent / "models" / "easyocr"),
                )
            )
            self.reader = easyocr.Reader(
                ["en"],
                gpu=use_cuda,
                verbose=False,
                model_storage_directory=str(model_dir),
                download_enabled=False,
            )
            self.reader.readtext(np.zeros((64, 128, 3), dtype=np.uint8))
            print(f"Shared OCR model ready on {self.device}.", flush=True)
        except Exception as exc:
            self.error = str(exc)
            self.device = "unavailable"
            print(f"Shared OCR model failed to initialize: {exc}", flush=True)
        finally:
            self.ready.set()

    def status(self) -> dict:
        return {
            "ready": self.ready.is_set() and self.reader is not None,
            "device": self.device,
            "error": self.error,
            "cache_entries": len(self.cache),
        }

    def read(self, encoded_image: bytes, languages: tuple[str, ...]) -> dict:
        if not self.ready.wait(timeout=120.0):
            raise RuntimeError("OCR model initialization timed out")
        if self.reader is None:
            raise RuntimeError(self.error or "OCR model is unavailable")
        if languages != ("en",):
            raise RuntimeError("the shared OCR worker currently supports English only")

        cache_key = hashlib.blake2b(
            encoded_image + b"\0" + ",".join(languages).encode("utf-8"),
            digest_size=16,
        ).hexdigest()
        with self.cache_lock:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.cache.move_to_end(cache_key)
                return {"items": cached, "device": self.device, "cache_hit": True, "inference_ms": 0.0}

        image = cv2.imdecode(np.frombuffer(encoded_image, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise ValueError("invalid OCR image")

        started = time.perf_counter()
        with self.inference_lock:
            # Another queued request may have populated the cache while this
            # request waited for the GPU.
            with self.cache_lock:
                cached = self.cache.get(cache_key)
                if cached is not None:
                    self.cache.move_to_end(cache_key)
                    return {"items": cached, "device": self.device, "cache_hit": True, "inference_ms": 0.0}
            results = self.reader.readtext(image, detail=1, paragraph=False)
        duration_ms = (time.perf_counter() - started) * 1000.0
        items = [
            {
                "box": [[float(point[0]), float(point[1])] for point in box],
                "text": str(text),
                "confidence": float(confidence),
            }
            for box, text, confidence in results
        ]
        with self.cache_lock:
            self.cache[cache_key] = items
            self.cache.move_to_end(cache_key)
            while len(self.cache) > self.CACHE_LIMIT:
                self.cache.popitem(last=False)
        return {
            "items": items,
            "device": self.device,
            "cache_hit": False,
            "inference_ms": round(duration_ms, 2),
        }


class OcrRequestHandler(BaseHTTPRequestHandler):
    server_version = "SharedOCR/1"

    def _authorized(self) -> bool:
        expected = self.server.auth_token
        return not expected or self.headers.get("Authorization") == f"Bearer {expected}"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        self._json(200, self.server.runtime.status())

    def do_POST(self) -> None:
        if self.path != "/ocr":
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 16 * 1024 * 1024:
                raise ValueError("OCR request must contain at most 16 MiB")
            image = self.rfile.read(content_length)
            languages = tuple(
                value.strip() for value in self.headers.get("X-OCR-Languages", "en").split(",") if value.strip()
            ) or ("en",)
            self._json(200, self.server.runtime.read(image, languages))
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(503, {"error": str(exc)})

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Shared EasyOCR worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", default="")
    parser.add_argument("--device", default=os.environ.get("AUTOMATION_OCR_DEVICE", "auto"))
    parser.add_argument("--threads", type=int, default=int(os.environ.get("AUTOMATION_OCR_THREADS", "4")))
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), OcrRequestHandler)
    server.auth_token = args.token
    server.runtime = OcrRuntime(args.device.strip().casefold(), args.threads)
    print(f"Shared OCR worker listening on {args.host}:{args.port}; requested device={args.device}", flush=True)
    server.serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    main()

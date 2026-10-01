"""Client for the manager-owned shared OCR worker."""

import json
import os
from urllib import error, request

import cv2
import numpy as np


class SharedOcrUnavailable(RuntimeError):
    """Raised when the shared OCR worker cannot serve a request."""


def shared_worker_configured() -> bool:
    return bool(os.environ.get("AUTOMATION_OCR_WORKER_URL", "").strip())


def read_text_with_shared_worker(
    image: np.ndarray,
    languages: tuple[str, ...] = ("en",),
    timeout_seconds: float = 120.0,
) -> dict:
    worker_url = os.environ.get("AUTOMATION_OCR_WORKER_URL", "").strip()
    if not worker_url:
        raise SharedOcrUnavailable("shared OCR worker is not configured")

    encoded_ok, encoded = cv2.imencode(".png", image)
    if not encoded_ok:
        raise SharedOcrUnavailable("could not encode OCR image")

    token = os.environ.get("AUTOMATION_OCR_WORKER_TOKEN", "")
    headers = {
        "Content-Type": "image/png",
        "X-OCR-Languages": ",".join(languages),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = request.Request(
        f"{worker_url.rstrip('/')}/ocr",
        data=encoded.tobytes(),
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise SharedOcrUnavailable(str(exc)) from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise SharedOcrUnavailable("shared OCR worker returned an invalid response")
    return payload

#!/usr/bin/env python3
"""Benchmark local EasyOCR inference under a fixed PyTorch thread count."""

import argparse
import json
import os
import statistics
import time
from pathlib import Path

import cv2
import numpy as np
import torch


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


REGIONS = {
    "modal_header_alerts": (0.18, 0.03, 0.82, 0.30),
    "browser_dialog": (0.20, 0.05, 0.80, 0.48),
    "session_gate": (0.20, 0.16, 0.80, 0.78),
}


def prepare(image: np.ndarray, region: str) -> np.ndarray:
    height, width = image.shape[:2]
    if region in REGIONS:
        x1, y1, x2, y2 = REGIONS[region]
        image = image[int(height * y1):int(height * y2), int(width * x1):int(width * x2)]
    max_dimension = max(image.shape[:2])
    if max_dimension > 1280:
        scale = 1280.0 / max_dimension
        image = cv2.resize(
            image,
            (int(image.shape[1] * scale), int(image.shape[0] * scale)),
            interpolation=cv2.INTER_AREA,
        )
    return image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, choices=(1, 2, 4), required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument(
        "--regions",
        nargs="+",
        default=["modal_header_alerts", "full_screen"],
        choices=(*REGIONS.keys(), "full_screen"),
    )
    parser.add_argument("images", nargs="+")
    args = parser.parse_args()

    torch.set_num_threads(args.threads)
    import easyocr

    model_dir = Path(__file__).resolve().parent / "models" / "easyocr"
    started = time.perf_counter()
    reader = easyocr.Reader(
        ["en"],
        gpu=False,
        verbose=False,
        model_storage_directory=str(model_dir),
        download_enabled=False,
    )
    reader.readtext(np.zeros((64, 128, 3), dtype=np.uint8))
    initialization_ms = (time.perf_counter() - started) * 1000.0

    samples = []
    for image_path in args.images:
        image = cv2.imread(image_path)
        if image is None:
            raise SystemExit(f"Could not read benchmark image: {image_path}")
        for region in args.regions:
            prepared = prepare(image, region)
            for repeat in range(args.repeats):
                started = time.perf_counter()
                results = reader.readtext(prepared, detail=1, paragraph=False)
                duration_ms = (time.perf_counter() - started) * 1000.0
                samples.append({
                    "image": os.path.abspath(image_path),
                    "region": region,
                    "repeat": repeat + 1,
                    "duration_ms": round(duration_ms, 2),
                    "candidate_count": len(results),
                })

    durations = [sample["duration_ms"] for sample in samples]
    print(json.dumps({
        "threads": args.threads,
        "initialization_ms": round(initialization_ms, 2),
        "inference_count": len(samples),
        "median_inference_ms": round(statistics.median(durations), 2),
        "p95_inference_ms": round(percentile(durations, 0.95), 2),
        "mean_inference_ms": round(statistics.mean(durations), 2),
        "samples": samples,
    }, indent=2))


if __name__ == "__main__":
    main()

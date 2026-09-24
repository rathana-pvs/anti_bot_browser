"""Execution evidence capture for visual automation tasks."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import cv2
import numpy as np


class EvidenceRecorder:
    """Persist screenshots and metadata for a single automation execution."""

    def __init__(self, profile_id: str, task_name: str):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        safe_task = re.sub(r"[^a-zA-Z0-9_-]+", "_", task_name).strip("_") or "task"
        root = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "profiles",
                profile_id,
                "automation_evidence",
            )
        )
        self.run_id = f"{stamp}_{safe_task}"
        self.directory = os.path.join(root, self.run_id)
        os.makedirs(self.directory, exist_ok=True)
        self._sequence = 0

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "capture"

    def capture(
        self,
        label: str,
        image: np.ndarray,
        metadata: dict | None = None,
    ) -> str:
        """Save a numbered PNG and optional JSON metadata alongside it."""
        self._sequence += 1
        stem = f"{self._sequence:02d}_{self._safe_name(label)}"
        image_path = os.path.join(self.directory, f"{stem}.png")
        if image is None or not cv2.imwrite(image_path, image):
            raise RuntimeError(f"Failed to write evidence screenshot: {image_path}")

        if metadata is not None:
            metadata_path = os.path.join(self.directory, f"{stem}.json")
            payload = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "label": label,
                **metadata,
            }
            with open(metadata_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)

        return image_path

    def write_result(self, status: str, error: str | None = None, **extra) -> str:
        """Write the final machine-readable result for this execution."""
        result_path = os.path.join(self.directory, "result.json")
        payload = {
            "run_id": self.run_id,
            "status": status,
            "error": error,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        return result_path

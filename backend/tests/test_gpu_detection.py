import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.modules.setdefault(
    "psutil",
    SimpleNamespace(virtual_memory=lambda: SimpleNamespace(total=48_399_773_696)),
)

from backend import config


class GpuDetectionTests(unittest.TestCase):
    def test_reports_actual_memory_instead_of_a_coarse_bucket(self):
        self.assertEqual(config.TOTAL_MEMORY_GB, 45.1)

    def test_reports_nvidia_when_cuda_runtime_is_unavailable(self):
        torch = SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: False,
                get_device_name=lambda _index: None,
            )
        )
        with patch.object(config, "detect_nvidia_gpu", return_value=(True, "NVIDIA RTX Test")), \
             patch.dict(sys.modules, {"torch": torch}):
            runtime = config.detect_ocr_runtime()

        self.assertEqual(runtime["device"], "cpu")
        self.assertTrue(runtime["nvidia_detected"])
        self.assertFalse(runtime["cuda_runtime_available"])
        self.assertEqual(runtime["gpu_name"], "NVIDIA RTX Test")
        self.assertIn("CUDA runtime is unavailable", runtime["fallback_reason"])

    def test_reports_cuda_device_when_pytorch_can_use_it(self):
        torch = SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: True,
                get_device_name=lambda _index: "NVIDIA RTX CUDA Test",
            )
        )
        with patch.object(config, "detect_nvidia_gpu", return_value=(True, "NVIDIA RTX Test")), \
             patch.dict(sys.modules, {"torch": torch}):
            runtime = config.detect_ocr_runtime()

        self.assertEqual(runtime["device"], "cuda")
        self.assertTrue(runtime["nvidia_detected"])
        self.assertTrue(runtime["cuda_runtime_available"])
        self.assertEqual(runtime["gpu_name"], "NVIDIA RTX CUDA Test")
        self.assertIsNone(runtime["fallback_reason"])


if __name__ == "__main__":
    unittest.main()

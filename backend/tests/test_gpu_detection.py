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
    def test_probes_automation_interpreter(self):
        with patch.object(config.subprocess, "check_output", return_value='[true, "Test GPU"]\n') as probe:
            self.assertEqual(config.probe_ocr_cuda(), (True, "Test GPU"))
        self.assertEqual(probe.call_args.args[0][:2], [str(config.AUTOMATION_PYTHON), "-c"])
        self.assertEqual(probe.call_args.kwargs["timeout"], 30)

    def test_reports_actual_memory_instead_of_a_coarse_bucket(self):
        with patch.object(
            config.psutil,
            "virtual_memory",
            return_value=SimpleNamespace(total=48_399_773_696),
        ):
            self.assertEqual(config.detect_total_memory_gb(), 45.1)

    def test_reports_nvidia_when_cuda_runtime_is_unavailable(self):
        torch = SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: False,
                get_device_name=lambda _index: None,
            )
        )
        with patch.object(config, "detect_nvidia_gpu", return_value=(True, "NVIDIA RTX Test")), \
             patch.object(config, "probe_ocr_cuda", return_value=(False, None)):
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
             patch.object(config, "probe_ocr_cuda", return_value=(True, "NVIDIA RTX CUDA Test")):
            runtime = config.detect_ocr_runtime()

        self.assertEqual(runtime["device"], "cuda")
        self.assertTrue(runtime["nvidia_detected"])
        self.assertTrue(runtime["cuda_runtime_available"])
        self.assertEqual(runtime["gpu_name"], "NVIDIA RTX CUDA Test")
        self.assertIsNone(runtime["fallback_reason"])


if __name__ == "__main__":
    unittest.main()

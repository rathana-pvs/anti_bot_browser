import subprocess
import unittest
from unittest.mock import call, patch

import bootstrap_torch_runtime as bootstrap


class TorchBootstrapTests(unittest.TestCase):
    @patch.object(bootstrap.subprocess, "run")
    def test_probe_runtime_uses_fresh_interpreter(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"torch_version":"2.14.0+cu130","vision_version":"0.29.0+cu130","cuda_available":true,"gpu_name":"RTX"}\n',
            stderr="",
        )

        self.assertTrue(bootstrap.runtime_is_ready(bootstrap.CUDA_TAG))
        self.assertEqual(run.call_args.args[0][0], bootstrap.sys.executable)
        self.assertEqual(run.call_args.args[0][1], "-c")

    @patch.object(bootstrap, "write_status")
    @patch.object(bootstrap, "install")
    @patch.object(bootstrap, "runtime_is_ready")
    @patch.object(bootstrap, "nvidia_smi", return_value="/usr/lib/wsl/lib/nvidia-smi")
    def test_installs_cuda_when_nvidia_is_visible(self, _smi, runtime_ready, install, write_status):
        runtime_ready.side_effect = [False, True]

        self.assertEqual(bootstrap.main(), 0)

        install.assert_called_once_with(bootstrap.CUDA_TAG)
        self.assertEqual(runtime_ready.call_args_list, [call(bootstrap.CUDA_TAG), call(bootstrap.CUDA_TAG)])
        write_status.assert_called_once_with("cuda", True)

    @patch.object(bootstrap, "write_status")
    @patch.object(bootstrap, "install")
    @patch.object(bootstrap, "runtime_is_ready")
    @patch.object(bootstrap, "nvidia_smi", return_value="/usr/lib/wsl/lib/nvidia-smi")
    def test_falls_back_to_cpu_when_cuda_cannot_initialize(self, _smi, runtime_ready, install, write_status):
        runtime_ready.side_effect = [False, False, True]

        self.assertEqual(bootstrap.main(), 0)

        self.assertEqual(install.call_args_list, [call(bootstrap.CUDA_TAG), call(bootstrap.CPU_TAG)])
        write_status.assert_called_once_with(
            "cpu",
            True,
            "CUDA installation or initialization failed; OCR will use CPU.",
        )

    @patch.object(bootstrap, "write_status")
    @patch.object(bootstrap, "install")
    @patch.object(bootstrap, "runtime_is_ready", side_effect=[False, True])
    @patch.object(bootstrap, "nvidia_smi", return_value="/usr/lib/wsl/lib/nvidia-smi")
    def test_falls_back_when_cuda_package_install_fails(self, _smi, _runtime_ready, install, write_status):
        install.side_effect = [subprocess.CalledProcessError(1, "pip"), None]

        self.assertEqual(bootstrap.main(), 0)

        self.assertEqual(install.call_args_list, [call(bootstrap.CUDA_TAG), call(bootstrap.CPU_TAG)])
        write_status.assert_called_once_with(
            "cpu",
            True,
            "CUDA installation or initialization failed; OCR will use CPU.",
        )


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
from unittest.mock import Mock, patch

from backend.services import ocr_worker_service


def test_worker_uses_parent_watchdog_and_keeps_token_out_of_command_line(tmp_path):
    process = Mock(pid=4321)
    process.poll.return_value = None
    settings = {"limits": {"max_total_automation_tasks": 2}}

    with patch.dict(os.environ, {}, clear=False), \
         patch.object(ocr_worker_service, "DATA_DIR", tmp_path), \
         patch.object(ocr_worker_service, "ROOT_DIR", Path("/runtime")), \
         patch.object(ocr_worker_service, "AUTOMATION_PYTHON", Path("/runtime/python")), \
         patch.object(ocr_worker_service, "_available_loopback_port", return_value=4567), \
         patch.object(ocr_worker_service, "_stop_legacy_workers", return_value=0), \
         patch.object(ocr_worker_service.settings_manager, "get_snapshot", return_value=settings), \
         patch.object(ocr_worker_service, "recommended_ocr_threads", return_value=4), \
         patch.object(ocr_worker_service.secrets, "token_urlsafe", return_value="private-token"), \
         patch.object(ocr_worker_service.subprocess, "Popen", return_value=process) as popen:
        result = ocr_worker_service.start_shared_ocr_worker()

        command = popen.call_args.args[0]
        child_env = popen.call_args.kwargs["env"]
        assert result["pid"] == 4321
        assert command[command.index("--parent-pid") + 1] == str(os.getpid())
        assert "--token" not in command
        assert "private-token" not in command
        assert child_env["AUTOMATION_OCR_WORKER_TOKEN"] == "private-token"
        assert child_env["AUTOMAT_FB_ROOT"] == "/runtime"

    if ocr_worker_service._log_handle is not None:
        ocr_worker_service._log_handle.close()
    ocr_worker_service._log_handle = None
    ocr_worker_service._process = None


def test_legacy_cleanup_terminates_only_workers_without_watchdogs():
    legacy = Mock()
    legacy.info = {"cmdline": ["python", "/runtime/automation/ocr_worker.py", "--port", "1001"]}
    owned = Mock()
    owned.info = {
        "cmdline": [
            "python",
            "/runtime/automation/ocr_worker.py",
            "--port",
            "1002",
            "--parent-pid",
            "55",
        ]
    }

    with patch.object(ocr_worker_service.psutil, "process_iter", return_value=[legacy, owned]), \
         patch.object(ocr_worker_service.psutil, "wait_procs", return_value=([legacy], [])):
        stopped = ocr_worker_service._stop_legacy_workers(Path("/runtime/automation/ocr_worker.py"))

    assert stopped == 1
    legacy.terminate.assert_called_once_with()
    owned.terminate.assert_not_called()

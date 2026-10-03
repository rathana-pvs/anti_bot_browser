import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

import numpy as np
import yaml

from engine.brain_runtime import (
    BrainRegistry,
    BrainResolutionError,
    BrainValidationError,
    CapabilityResult,
    WorkflowInterpreter,
    load_brain_package,
)
from engine.screen_state import ScreenState, StateObservation
from tasks.base_task import BaseTask
from brain_cli import install_archive, list_brains


AUTOMATION_ROOT = Path(__file__).resolve().parents[1]


class BrainRuntimeTests(unittest.TestCase):
    def test_bundled_package_is_valid_and_resolvable(self):
        registry = BrainRegistry(AUTOMATION_ROOT / "brains")
        package = registry.resolve("facebook_post")
        self.assertEqual(package.brain_id, "facebook_post")
        self.assertEqual(package.version, "1.0.0")
        self.assertEqual(len(package.digest), 64)

    def test_registry_rejects_path_traversal_version(self):
        registry = BrainRegistry(AUTOMATION_ROOT / "brains")
        with self.assertRaises(BrainResolutionError):
            registry.resolve("facebook_post", "../../tasks")

    def test_unknown_capability_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_package(root, capability="shell")
            with self.assertRaisesRegex(BrainValidationError, "unsupported capability"):
                load_brain_package(root, expected_id="facebook_post")

    def test_interpreter_executes_only_allow_listed_capabilities(self):
        registry = BrainRegistry(AUTOMATION_ROOT / "brains")
        package = registry.resolve("facebook_post")
        provider = FakeProvider()
        result = WorkflowInterpreter(package, provider).run({"caption": "hello"})
        self.assertEqual(result.terminal_state, "completed")
        self.assertIn("request_publish", provider.calls)
        self.assertEqual(provider.calls.count("request_publish"), 1)

    def test_interpreter_bounds_a_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_package(root, success_target="start", max_steps=3)
            package = load_brain_package(root, expected_id="facebook_post")
            result = WorkflowInterpreter(package, FakeProvider()).run()
            self.assertEqual(result.terminal_state, "operator_review")
            self.assertEqual(result.steps, 3)

    def test_local_zip_upload_is_validated_and_installed_immutably(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            package_dir = workspace / "package"
            package_dir.mkdir()
            self._write_package(package_dir, version="1.2.3")
            archive = workspace / "brain.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
                handle.write(package_dir / "manifest.json", "manifest.json")
                handle.write(package_dir / "workflow.yaml", "workflow.yaml")

            brains_root = workspace / "brains"
            result = install_archive(brains_root, archive)
            self.assertTrue(result["success"])
            self.assertEqual(result["installed"]["version"], "1.2.3")
            installed = BrainRegistry(brains_root).resolve("facebook_post", "1.2.3")
            self.assertEqual(installed.version, "1.2.3")
            listing = list_brains(brains_root)
            version = listing["brains"][0]["versions"][0]
            self.assertEqual(version["install_source"], "local_upload")
            with self.assertRaisesRegex(BrainValidationError, "already installed"):
                install_archive(brains_root, archive)

    def test_local_upload_rejects_archive_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            archive = workspace / "evil.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../escape.json", "{}")
            with self.assertRaisesRegex(BrainValidationError, "unsafe path"):
                install_archive(workspace / "brains", archive)

    @staticmethod
    def _write_package(
        root: Path,
        *,
        capability: str = "observe_screen",
        success_target: str = "completed",
        max_steps: int = 5,
        version: str = "1.0.0",
    ) -> None:
        manifest = {
            "manifest_version": 1,
            "brain_api_version": 1,
            "id": "facebook_post",
            "version": version,
            "platform": "facebook",
            "task_type": "post",
            "min_engine_version": "1.0.0",
        }
        workflow = {
            "schema_version": 1,
            "brain_api_version": 1,
            "workflow_id": "facebook_post",
            "limits": {
                "max_steps": max_steps,
                "max_runtime_seconds": 30,
                "max_recovery_attempts": 1,
            },
            "initial_state": "start",
            "states": {
                "start": {
                    "actions": [{"capability": capability}],
                    "on_success": success_target,
                    "on_failure": "failed_safe",
                }
            },
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (root / "workflow.yaml").write_text(yaml.safe_dump(workflow), encoding="utf-8")


class FakeProvider:
    def __init__(self):
        self.calls = []

    def evaluate(self, requirement, context):
        return True

    def call(self, capability, parameters, context):
        self.calls.append(capability)
        return CapabilityResult("success")


class PublishGateTests(unittest.TestCase):
    def make_task(self, state=ScreenState.POST_ENABLED):
        task = BaseTask.__new__(BaseTask)
        task.current_stage = "ready_to_publish"
        task._publish_gate_consumed = False
        task.client = Mock()
        task.client.screenshot.return_value = np.zeros((100, 200, 3), dtype=np.uint8)
        task.recognizer = Mock()
        task.recognizer.observe.return_value = StateObservation(state, 0.95, ["enabled action"])
        task.human = Mock()
        task.log = Mock()
        task.capture_evidence = Mock()
        task.set_stage = Mock(side_effect=lambda stage, **_: setattr(task, "current_stage", stage))
        return task

    def test_gate_sends_exactly_one_click(self):
        task = self.make_task()
        self.assertTrue(task.execute_publish_gate((100, 80)))
        task.human.click.assert_called_once_with(100, 80)
        task.current_stage = "ready_to_publish"
        self.assertFalse(task.execute_publish_gate((100, 80)))
        task.human.click.assert_called_once()

    def test_gate_rejects_unsafe_screen_state(self):
        task = self.make_task(ScreenState.ERROR_DIALOG)
        self.assertFalse(task.execute_publish_gate((100, 80)))
        task.human.click.assert_not_called()

    def test_gate_rejects_out_of_viewport_target(self):
        task = self.make_task()
        self.assertFalse(task.execute_publish_gate((500, 80)))
        task.human.click.assert_not_called()

    def test_gate_supports_reel_publication_observation(self):
        task = self.make_task()
        task.LEFT_PUBLICATION_REGION = (0.0, 0.5, 0.4, 1.0)
        task.recognizer.observe_publication_gate.return_value = StateObservation(
            ScreenState.POST_ENABLED, 0.96, ["reel publish action"]
        )

        self.assertTrue(
            task.execute_publish_gate((100, 80), publish_kind="reel")
        )
        task.recognizer.observe_publication_gate.assert_called_once()
        task.human.click.assert_called_once_with(100, 80)


if __name__ == "__main__":
    unittest.main()

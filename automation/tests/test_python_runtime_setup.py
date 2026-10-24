"""Private interpreter migration must preserve user data and reject Python 3.14."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import bootstrap_python_runtime as bootstrap


class PythonRuntimeSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.python = self.root / 'automation/venv/bin/python'
        self.python.parent.mkdir(parents=True)
        self.python.write_text('python3.14')
        self.marker = self.root / 'data/runtime-installed-version'
        self.marker.parent.mkdir()
        self.marker.write_text('1.1.4')
        self.profile = self.root / 'profiles/profile/chrome_data/session'
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text('preserved session')
        self.host = Path('/test/python3.12')

    def compatible(self, python):
        return python == self.host or (python.is_file() and python.read_text() == 'python3.12')

    def create_environment(self, command, **kwargs):
        self.assertEqual(command[:4], [str(self.host), '-I', '-m', 'venv'])
        self.python.parent.mkdir(parents=True)
        self.python.write_text('python3.12')
        return Mock(returncode=0)

    def test_compatible_environment_is_reused_without_download_or_backup(self):
        self.python.write_text('python3.12')
        with patch.object(bootstrap, 'compatible', self.compatible), \
                patch.object(bootstrap, 'managed_python') as download, \
                patch.object(bootstrap.subprocess, 'run') as run:
            self.assertEqual(bootstrap.prepare(self.root), self.python)
        download.assert_not_called()
        run.assert_not_called()
        self.assertEqual(self.marker.read_text(), '1.1.4')

    def test_python314_environment_is_backed_up_and_replaced_with_python312(self):
        with patch.object(bootstrap, 'compatible', self.compatible), \
                patch.object(bootstrap.shutil, 'which', return_value=str(self.host)), \
                patch.object(bootstrap.subprocess, 'run', side_effect=self.create_environment):
            bootstrap.prepare(self.root)
        backups = list((self.root / 'data/python-runtime/backups').iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / 'bin/python').read_text(), 'python3.14')
        self.assertEqual(self.python.read_text(), 'python3.12')
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.profile.read_text(), 'preserved session')

    def test_missing_compatible_host_downloads_before_moving_existing_environment(self):
        def download(root):
            self.assertEqual(root, self.root)
            self.assertEqual(self.python.read_text(), 'python3.14')
            return self.host
        with patch.object(bootstrap, 'compatible', self.compatible), \
                patch.object(bootstrap.shutil, 'which', return_value='/test/python3.14'), \
                patch.object(bootstrap, 'managed_python', side_effect=download) as managed, \
                patch.object(bootstrap.subprocess, 'run', side_effect=self.create_environment):
            bootstrap.prepare(self.root)
        managed.assert_called_once_with(self.root)
        self.assertEqual(self.python.read_text(), 'python3.12')

    def test_failed_download_preserves_existing_environment_and_completion_marker(self):
        with patch.object(bootstrap, 'compatible', self.compatible), \
                patch.object(bootstrap.shutil, 'which', return_value=None), \
                patch.object(bootstrap, 'managed_python', side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                bootstrap.prepare(self.root)
        self.assertEqual(self.python.read_text(), 'python3.14')
        self.assertEqual(self.marker.read_text(), '1.1.4')
        self.assertEqual(self.profile.read_text(), 'preserved session')

    def test_failed_creation_restores_existing_environment(self):
        def fail(command, **kwargs):
            self.python.parent.mkdir(parents=True)
            self.python.write_text('partial environment')
            raise subprocess.CalledProcessError(1, command)
        with patch.object(bootstrap, 'compatible', self.compatible), \
                patch.object(bootstrap.shutil, 'which', return_value=str(self.host)), \
                patch.object(bootstrap.subprocess, 'run', side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                bootstrap.prepare(self.root)
        self.assertEqual(self.python.read_text(), 'python3.14')
        self.assertEqual(self.marker.read_text(), '1.1.4')

    def test_managed_download_is_private_and_does_not_install_global_python_links(self):
        tool = self.root / 'data/python-runtime/bootstrap/bin/uv'
        tool.parent.mkdir(parents=True)
        tool.write_text('existing uv')
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return Mock(returncode=0, stdout=str(self.host) + '\n')
        with patch.object(bootstrap, 'compatible', self.compatible), \
                patch.object(bootstrap.subprocess, 'run', side_effect=run):
            self.assertEqual(bootstrap.managed_python(self.root), self.host)
        self.assertEqual(calls[0][0][-3:], ['python', 'install', '3.12'])
        self.assertEqual(calls[0][1]['env']['UV_PYTHON_INSTALL_BIN'], '0')
        self.assertTrue(calls[0][1]['env']['UV_PYTHON_INSTALL_DIR'].startswith(str(self.root)))
        self.assertIn('--no-python-downloads', calls[1][0])


if __name__ == '__main__':
    unittest.main()

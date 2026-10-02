import shutil
import zipfile

from backend.services.queue_service import create_support_archive


def test_global_support_archive_is_created_and_readable():
    archive_path, filename, temp_root = create_support_archive(
        queue={"queue_version": 1, "daily_batches": []},
        description="Download regression test",
    )
    try:
        assert filename.startswith("support_global_")
        assert filename.endswith(".zip")
        assert archive_path.is_file()
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None
            members = archive.namelist()
            assert any(name.endswith("/report.json") for name in members)
            assert any(name.endswith("/system_info.json") for name in members)
            assert any(name.endswith("/README.txt") for name in members)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

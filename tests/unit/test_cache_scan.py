"""Cache statistics remain readable while completed jobs remove their folders."""
import os
import shutil

from mimetic.web.server import Project


def test_cache_scan_tolerates_directory_removed_during_traversal(tmp_path, monkeypatch):
    project = Project.__new__(Project)
    project.upload_dir = tmp_path / "uploads"
    project.work_dir = tmp_path / "jobs"
    project.upload_dir.mkdir()
    project.work_dir.mkdir()
    (project.upload_dir / "retained.wav").write_bytes(b"kept")
    expired = project.work_dir / "completed_job"
    expired.mkdir()
    (expired / "temporary.wav").write_bytes(b"temporary")
    original_scandir = os.scandir
    removed = False

    def remove_before_scan(path):
        nonlocal removed
        if path in (str(expired), expired) and not removed:
            removed = True
            shutil.rmtree(expired)
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", remove_before_scan)
    assert project.cache_bytes() == 4
    assert removed and not expired.exists()

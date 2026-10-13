import io
import json
import stat
import threading
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from mimetic import pro_tools
from mimetic.domain.errors import MimeticError
from mimetic_portable.job import Job, hash_file
from mimetic_portable.selftest import build_fixture, fixed_estimator
from mimetic_portable.zip_import import unpack


@pytest.fixture
def no_eq(monkeypatch):
    def unavailable(*args, **kwargs):
        raise MimeticError("EQ_INSUFFICIENT_SPEECH")
    monkeypatch.setattr(pro_tools.eq_match, "build_profile", unavailable)


def verify(output, fixture):
    root = output.parent / ("verify_" + output.stem)
    with zipfile.ZipFile(output) as archive:
        archive.extractall(root)  # generated result only
    session_path = next(root.rglob("*.ptx"))
    session = pro_tools.api().ProToolsSession(str(session_path))
    rows = session.get_timeline_clips()
    assert len(rows) == 2 and len(session.get_tracks()) == 6
    dry, _ = sf.read(fixture.parent / "session" / "Audio Files" / "adr.wav")
    expected = np.zeros(len(dry) + 48000 - 1)
    expected[480:480+len(dry)] += 0.3 * dry
    expected[47999:47999+len(dry)] -= 0.1 * dry
    for row in rows:
        mix = row["track"].startswith("MIMETIC_MIX")
        assert row["start_samples"] == 1740100860 and row["muted"] == mix
        assert pro_tools.clip_gain(session, row["clip_name"]) == (0 if mix else 3.5)
        actual, fs = sf.read(session_path.parent / "Audio Files" / row["physical_filename"])
        truth = expected.copy()
        if mix:
            truth[:len(dry)] += dry
        assert fs == 48000
        np.testing.assert_allclose(actual, truth.astype(np.float32), atol=1e-7)
    report = json.loads((session_path.parent / "Mimetic_report.json").read_text(encoding="utf-8"))
    assert report["units"][0]["eq_status"] == "identity_fallback"
    assert report["units"][0]["destination"]["length_samples"] == 4800
    assert report["portable"]["version"].startswith("1.5.0")


def test_short_adr_exact_tail_metadata_and_unicode_filename(tmp_path, no_eq):
    archive = build_fixture(tmp_path / "synthetic")
    renamed = archive.with_name("Scène 012 dialogue.zip")
    archive.rename(renamed)
    original = hash_file(renamed)
    events = []
    job = Job(estimator=fixed_estimator, emit=events.append)
    output = job.run(renamed)
    assert output.name == "Scène 012 dialogue_Mimetic.zip"
    assert output.parent == renamed.parent
    assert hash_file(renamed) == original
    assert events[-1]["event"] == "ready" and events[-1]["total"] == 1
    verify(output, renamed)
    assert not list(output.parent.glob(".mimetic-*"))


def test_previous_export_never_overwritten_and_output_folder_selectable(tmp_path, no_eq):
    archive = build_fixture(tmp_path / "synthetic")
    folder = tmp_path / "exports"
    folder.mkdir()
    previous = folder / "Scene_012_Mimetic.zip"
    previous.write_bytes(b"previous export")
    previous_hash = hash_file(previous)
    output = Job(estimator=fixed_estimator).run(archive, folder)
    assert output.name == "Scene_012_Mimetic_02.zip" and hash_file(previous) == previous_hash
    verify(output, archive)


@pytest.mark.parametrize("member", ["../escape.wav", "/escape.wav", "C:/escape.wav", "Dialogue/../../escape.wav", "Dialogue\\..\\escape.wav"])
def test_hostile_zip_paths_rejected_without_writing_outside(tmp_path, member):
    archive = tmp_path / "hostile.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(member, b"unsafe")
    root = tmp_path / "unpack"
    root.mkdir()
    with pytest.raises(MimeticError, match="PTX_UNSUPPORTED"):
        unpack(archive, root)
    assert not list(root.iterdir()) and not (tmp_path / "escape.wav").exists()


def test_zip_symlink_is_not_followed(tmp_path):
    archive = tmp_path / "symlink.zip"
    member = zipfile.ZipInfo("link.wav")
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(member, "../../outside")
    root = tmp_path / "unpack"
    root.mkdir()
    with pytest.raises(MimeticError, match="PTX_UNSUPPORTED"):
        unpack(archive, root)
    assert not list(root.iterdir())


def test_backups_and_mac_metadata_ignored_but_audio_hierarchy_required(tmp_path):
    original = build_fixture(tmp_path / "synthetic")
    augmented = tmp_path / "augmented.zip"
    with zipfile.ZipFile(original) as src, zipfile.ZipFile(augmented, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))
        dst.writestr("Scene_012/Session File Backups/backup.ptx", b"backup")
        dst.writestr("__MACOSX/Scene_012/._Synthetic.ptx", b"metadata")
        dst.writestr("Unrelated/adr.wav", b"unrelated")
    unpacked = tmp_path / "unpack"
    unpacked.mkdir()
    session, name, media = unpack(augmented, unpacked)
    assert name == "Synthetic.ptx" and session.is_file() and set(media) == {"adr.wav", "reference.wav"}
    wrong = tmp_path / "wrong.zip"
    with zipfile.ZipFile(original) as src, zipfile.ZipFile(wrong, "w") as dst:
        for name in src.namelist():
            dst.writestr(name.replace("Audio Files", "Elsewhere"), src.read(name))
    with pytest.raises(MimeticError, match="PTX_MEDIA_MISSING"):
        Job(estimator=fixed_estimator).run(wrong)
    assert not list(tmp_path.glob("*_Mimetic*.zip"))


def test_cancel_during_inference_publishes_no_zip_and_cleans_work(tmp_path):
    archive = build_fixture(tmp_path / "synthetic")
    entered = threading.Event()
    errors = []
    def slow(signal, fs, **kwargs):
        entered.set()
        while not kwargs["client"].cancelled:
            time.sleep(0.01)
        raise MimeticError("CANCELLED")
    job = Job(estimator=slow)
    def run():
        try:
            job.run(archive)
        except MimeticError as exc:
            errors.append(exc.code)
    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(5)
    job.cancel()
    thread.join(timeout=5)
    assert not thread.is_alive() and errors == ["CANCELLED"]
    assert archive.is_file() and not list(archive.parent.glob("*_Mimetic*.zip"))
    assert not list(archive.parent.glob(".mimetic-*"))


def test_cancel_before_packaging_leaves_no_partial_deliverable(tmp_path, no_eq):
    archive = build_fixture(tmp_path / "synthetic")
    job = None
    def progress(event):
        if event.get("step") == "Création du ZIP":
            job.cancel()
    job = Job(estimator=fixed_estimator, emit=progress)
    with pytest.raises(MimeticError, match="CANCELLED"):
        job.run(archive)
    assert not list(archive.parent.glob("*_Mimetic*.zip"))
    assert not list(archive.parent.glob(".mimetic-*"))


def test_invalid_archive_preserves_original_and_creates_no_result(tmp_path):
    archive = tmp_path / "bad.zip"
    archive.write_bytes(b"not a ZIP")
    original = hash_file(archive)
    with pytest.raises(MimeticError, match="PTX_UNSUPPORTED"):
        Job().run(archive)
    assert hash_file(archive) == original and not list(tmp_path.glob(".mimetic-*"))


def test_duplicate_media_case_insensitive_is_rejected(tmp_path):
    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("session.ptx", b"session")
        z.writestr("Audio Files/adr.wav", b"first")
        z.writestr("Audio Files/ADR.wav", b"second")
    root = tmp_path / "unpack"
    root.mkdir()
    with pytest.raises(MimeticError, match="PTX_UNSUPPORTED"):
        unpack(archive, root)


def test_resource_locations_do_not_require_original_windows_project():
    from mimetic_portable.runtime import engine_paths, resource_root
    paths = engine_paths()
    assert paths["ckpt"].is_relative_to(resource_root())
    assert paths["repo"].is_relative_to(resource_root())
    assert paths["python"].is_file()
    assert pro_tools.capabilities()["available"]
    assert pro_tools.TEMPLATE.is_relative_to(resource_root())


def test_cli_failed_zip_emits_utf8_error_and_writes_visible_log(tmp_path):
    import os
    import subprocess
    from mimetic_portable.runtime import command
    archive = tmp_path / "Scène invalide.zip"
    archive.write_bytes(b"invalid archive")
    logs = tmp_path / "logs"
    result = subprocess.run(command("--process", archive), capture_output=True,
                            env=dict(os.environ, MIMETIC_PORTABLE_LOG_DIR=str(logs)), timeout=30)
    assert result.returncode == 1
    messages = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
    assert messages[-1]["event"] == "error" and messages[-1]["error"]["code"] == "PTX_UNSUPPORTED"
    assert "protégé" in messages[-1]["error"]["detail"]
    assert "Traceback" in result.stderr.decode("utf-8")
    assert (logs / "jobs.log").is_file()
    assert archive.read_bytes() == b"invalid archive" and not list(tmp_path.glob(".mimetic-*"))

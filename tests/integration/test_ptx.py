import io
import json
import threading
import time
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from mimetic import pro_tools
from mimetic.domain.errors import MimeticError
from mimetic.engines import known_ir
from mimetic.engines.recrir import adapter
from mimetic.web.server import create_app


def input_bundle(root):
    root.mkdir()
    specs = []
    fs, starts = 48000, [1740100860, 1740100860 + 6000]
    for pair in (1, 2):
        for number, start in enumerate(starts, 1):
            for role, length, slot in (("ref", 1.55, "IR"), ("adr", 0.05 if number == 1 else 0.1, "MIX")):
                path = root / f"historical_{role}_{pair}_{number}.wav"
                t = np.arange(int(fs * length)) / fs
                signal = (0.1 + 0.01 * pair) * np.sin(2 * np.pi * (170 + number * 11) * t)
                pro_tools.write_bwf(path, signal, start)
                specs.append({"audio_path":str(path), "track_name":f"MIMETIC_{slot}_P{pair:02d}",
                              "clip_name":f"arbitrary_{role}_{pair}_{number}", "placement_start_samples":start})
    result = pro_tools.api().build_audio_session(str(pro_tools.TEMPLATE), specs, str(root / "session"), session_name="input")
    # Names carry no role hint; main playlist order is authoritative.
    session = pro_tools.api().ProToolsSession(result["session_path"])
    for i, old in enumerate(session.get_tracks(), 1):
        session.rename_track(old, f"Track {i}")
    session.save(result["session_path"])
    return Path(result["session_path"])


def estimator(signal, fs, **kwargs):
    h = np.zeros(48000)
    h[0], h[480], h[-1] = 1, 0.3, -0.1
    return known_ir.build_profile(h, fs, source_name="test", source_sha256="0", convention="total")


def client_at(root, estimate=estimator):
    return TestClient(create_app(root / "data", root / "exports", estimator=estimate,
                                capabilities=lambda: {"available":True, "detail":None}))


def archive_bytes(session, *, extras=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(session, "Dialogue/input.ptx")
        for path in (session.parent / "Audio Files").glob("*.wav"):
            archive.write(path, "Dialogue/Audio Files/" + path.name)
        for name, content in (extras or {}).items():
            archive.writestr(name, content)
    return data.getvalue()


def wait_ptx(client):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        p = client.get("/api/state").json()["ptx"]
        if p["job"]["state"] != "running":
            return p
        time.sleep(0.02)
    raise AssertionError("PTX still running")


def test_zip_to_self_contained_ptx_short_adrs_and_overlap(tmp_path, monkeypatch):
    # Match EQ cannot measure these tiny ADRs: exporting must retain them anyway.
    def unavailable_eq(*args, **kwargs):
        raise MimeticError("EQ_INSUFFICIENT_SPEECH")
    monkeypatch.setattr(pro_tools.eq_match, "build_profile", unavailable_eq)
    session = input_bundle(tmp_path / "input")
    client = client_at(tmp_path / "app")
    response = client.post("/api/ptx/upload?name=session.zip", content=archive_bytes(session))
    assert response.status_code == 200, response.text
    p = wait_ptx(client)
    assert p["job"]["state"] == "ready", p
    assert p["total"] == p["export"]["adr_units"] == 4
    assert p["pairs"][0]["source"] == "Track 1" and p["pairs"][0]["destination"] == "Track 2"
    assert p["export"]["placements"] == 4  # Two overlapping ADRs summed per pair/output.
    target = Path(p["export"]["directory"])
    output = pro_tools.api().ProToolsSession(str(target / p["export"]["session"]))
    rows = output.get_timeline_clips()
    assert len(output.get_tracks()) == 6 and not any(r["track"].endswith("P03") for r in rows)
    assert all(r["start_samples"] == 1740100860 for r in rows)
    for row in rows:
        is_mix = row["track"].startswith("MIMETIC_MIX")
        assert row["muted"] is is_mix
        assert pro_tools.clip_gain(output, row["clip_name"]) == (0 if is_mix else 3.5)
        assert row["length_samples"] == 6000 + 4800 + 48000 - 1
        pair = int(row["track"][-2:])
        expected = np.zeros(row["length_samples"])
        for number, offset in ((1, 0), (2, 6000)):
            dry, _ = sf.read(session.parent / "Audio Files" / f"historical_adr_{pair}_{number}.wav")
            expected[offset+480:offset+480+len(dry)] += 0.3 * dry
            expected[offset+47999:offset+47999+len(dry)] -= 0.1 * dry
            if is_mix:
                expected[offset:offset+len(dry)] += dry
        actual, _ = sf.read(target / "Audio Files" / row["physical_filename"], dtype="float32")
        # Independent sparse-IR truth: original ADRs, never reference waveforms;
        # +3.5 dB is metadata and must not be baked into the wet samples.
        np.testing.assert_allclose(actual, expected.astype(np.float32), atol=1e-7)
    report = json.loads((target / "Mimetic_report.json").read_text(encoding="utf-8"))
    assert all(u["eq_status"] == "identity_fallback" for u in report["units"])
    assert all("SHORT_REFERENCE_LOW_CONFIDENCE" in u["warnings"] for u in report["units"])
    assert Counter(ident for c in report["placements"] for ident in c["units"]) == {u["id"]:2 for u in report["units"]}
    downloaded = client.get("/api/exports/" + p["export"]["zip"])
    assert downloaded.status_code == 200
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert len([n for n in archive.namelist() if n.endswith(".ptx")]) == 1
        assert len([n for n in archive.namelist() if n.endswith(".wav")]) == 4
    client.post("/api/ptx/new")
    assert target.is_dir()  # New sequence keeps exports.
    assert client.post("/api/cache/clear").status_code == 200
    assert not target.exists()
    assert pro_tools.TEMPLATE.is_file()  # Native template is never part of the cache.


def test_zip_only_and_incomplete_zip_preserve_previous_sequence(tmp_path):
    session = input_bundle(tmp_path / "input")
    client = client_at(tmp_path / "app")
    assert client.post("/api/ptx/upload?name=input.zip", content=archive_bytes(session)).status_code == 200
    original = wait_ptx(client)
    assert client.post("/api/ptx/upload?name=input.ptx", content=session.read_bytes()).status_code == 400
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("Input/input.ptx", session.read_bytes())
    response = client.post("/api/ptx/upload?name=incomplete.zip", content=data.getvalue())
    assert response.status_code == 400 and response.json()["error"]["code"] == "PTX_MEDIA_MISSING"
    p = client.get("/api/state").json()["ptx"]
    assert p["id"] == original["id"] and p["export"] == original["export"]


def test_zip_traversal_cannot_replace_current_session(tmp_path):
    session = input_bundle(tmp_path / "input")
    client = client_at(tmp_path / "app")
    client.post("/api/ptx/upload?name=input.zip", content=archive_bytes(session))
    original = wait_ptx(client)
    response = client.post("/api/ptx/upload?name=bad.zip", content=archive_bytes(session, extras={"../escape.wav":b"bad"}))
    assert response.status_code == 400
    assert client.get("/api/state").json()["ptx"]["id"] == original["id"]
    assert not (tmp_path / "escape.wav").exists()


def test_normal_zip_hierarchy_and_backups(tmp_path):
    session = input_bundle(tmp_path / "input")
    client = client_at(tmp_path / "app")
    bundle = archive_bytes(session, extras={"Dialogue/Session File Backups/backup.ptx":session.read_bytes(),
                                           "__MACOSX/Dialogue/._input.ptx":b"metadata", "Dialogue/WaveCache.wfm":b"ignored"})
    response = client.post("/api/ptx/upload?name=input.zip", content=bundle)
    assert response.status_code == 200, response.text
    assert wait_ptx(client)["job"]["state"] == "ready"
    # WAVs outside the sibling Audio Files folder must not accidentally match.
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.write(session, "input.ptx")
        for file in (session.parent / "Audio Files").glob("*.wav"):
            archive.write(file, "Unrelated/" + file.name)
    response = client.post("/api/ptx/upload?name=wrong_hierarchy.zip", content=data.getvalue())
    assert response.status_code == 400 and response.json()["error"]["code"] == "PTX_MEDIA_MISSING"


def test_cancel_during_estimation_publishes_no_partial_package(tmp_path):
    entered, finish = threading.Event(), threading.Event()
    def slow(signal, fs, **kwargs):
        entered.set()
        while not kwargs["client"].cancelled:
            time.sleep(0.01)
        finish.set()
        raise MimeticError("CANCELLED")
    session = input_bundle(tmp_path / "input")
    client = client_at(tmp_path / "app", slow)
    assert client.post("/api/ptx/upload?name=input.zip", content=archive_bytes(session)).status_code == 200
    assert entered.wait(3)
    assert client.post("/api/ptx/cancel").status_code == 200
    assert finish.is_set()
    p = wait_ptx(client)
    assert p["job"]["state"] == "cancelled" and p["export"] is None
    assert not list((tmp_path / "app" / "exports").iterdir())


def test_short_selection_policy_is_scoped_not_global():
    x = np.sin(np.arange(4800) * 0.1)
    with pytest.raises(MimeticError):
        adapter.select_windows(x, 48000)
    windows, _ = adapter.select_windows(x, 48000, minimum_seconds=0.032)
    assert windows == [(0, 4800)] and adapter.MIN_WINDOW_SECONDS == 2.0

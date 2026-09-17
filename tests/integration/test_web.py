import io as pyio
import time

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from mimetic.engines import known_ir
from mimetic.web.server import create_app

FS = 48000


def wav_bytes(data, fs=FS, subtype="PCM_24"):
    buf = pyio.BytesIO()
    sf.write(buf, data, fs, subtype=subtype, format="WAV")
    return buf.getvalue()


def fake_estimator(calls):
    """Estimateur de test : IR connue, sans modèle. Compte les appels pour vérifier l'invalidation."""
    def estimate(signal, fs, *, workdir, client, reference_meta, progress):
        calls.append(reference_meta["asset_sha256"])
        progress("Analyse de la pièce — passage 1/1")
        h = np.zeros(8000)
        h[0], h[800], h[3000] = 1.0, 0.3, -0.1
        p = known_ir.build_profile(h, 16000, source_name="fake", source_sha256="0", convention="total")
        p.parameters["windows"] = []
        return p
    return estimate


def wait_idle(client, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get("/api/state").json()
        if st["job"]["state"] != "running" and not (st["render"] and st["render"]["stale"]):
            return st
        time.sleep(0.05)
    raise AssertionError("job toujours en cours")


def make_client(tmp_path, calls=None, available=True):
    caps = lambda: {"available": available, "detail": None if available else "absent"}  # noqa: E731
    est = fake_estimator(calls if calls is not None else [])
    return TestClient(create_app(tmp_path / "data", tmp_path / "exports", estimator=est, capabilities=caps))


def test_two_files_trigger_analysis_and_render_automatically(tmp_path):
    calls = []
    client = make_client(tmp_path, calls)
    rng = np.random.default_rng(0)
    st = client.post("/api/upload/source?name=tournage.wav", content=wav_bytes(rng.uniform(-0.2, 0.2, (FS * 3, 2)))).json()
    assert st["source"]["channel_mode"] == "left" and st["render"] is None and calls == []  # attend la destination

    r = client.post("/api/upload/destination?name=../../ADR.wav", content=wav_bytes(rng.uniform(-0.3, 0.3, FS)))
    assert r.status_code == 200 and r.json()["destination"]["name"] == "ADR.wav"
    st = wait_idle(client)
    assert len(calls) == 1 and st["render"] is not None and st["render"]["stale"] is False
    wet = np.frombuffer(client.get("/api/pcm/wet").content, dtype="<f4")
    assert wet.size == st["render"]["length_frames"]

    # Réglage de niveau : nouveau rendu sans nouvelle analyse
    client.post("/api/settings", json={"wet_gain_db": -6, "additional_predelay_ms": 10})
    st = wait_idle(client)
    assert len(calls) == 1 and st["settings"]["wet_gain_db"] == -6
    wet2 = np.frombuffer(client.get("/api/pcm/wet").content, dtype="<f4")
    assert wet2.size == wet.size + 480

    # Nouvelle destination : rendu seulement
    client.post("/api/upload/destination?name=ADR2.wav", content=wav_bytes(rng.uniform(-0.3, 0.3, FS * 2)))
    st = wait_idle(client)
    assert len(calls) == 1 and st["destination"]["name"] == "ADR2.wav"

    # Canal de la source : nouvelle analyse
    client.post("/api/channel/source", json={"mode": "right"})
    st = wait_idle(client)
    assert len(calls) == 2 and st["profile"]["valid"]

    st = client.post("/api/export", json={"export_ir": True}).json()
    files = st["last_export"]["files"]
    assert files["wet_wav"] == "ADR2_ADR_REVERB.wav"
    dl = client.get(f"/api/exports/{files['wet_wav']}")
    back, fs = sf.read(pyio.BytesIO(dl.content), dtype="float32")
    np.testing.assert_array_equal(back, np.frombuffer(client.get("/api/pcm/wet").content, dtype="<f4"))
    assert fs == FS
    assert client.get("/api/exports/..%2F..%2Fsecret.wav").status_code in (400, 404)


def test_engine_missing_is_reported_without_fake_result(tmp_path):
    client = make_client(tmp_path, available=False)
    rng = np.random.default_rng(1)
    client.post("/api/upload/source?name=s.wav", content=wav_bytes(rng.uniform(-0.2, 0.2, FS * 3)))
    client.post("/api/upload/destination?name=d.wav", content=wav_bytes(rng.uniform(-0.2, 0.2, FS)))
    st = wait_idle(client)
    assert st["job"]["state"] == "failed" and st["last_error"]["code"] == "MODEL_UNAVAILABLE"
    assert st["profile"] is None and st["render"] is None
    assert client.post("/api/export", json={}).json()["error"]["code"] == "INVALID_PARAMETER"


def test_bad_upload_and_settings_validation(tmp_path):
    client = make_client(tmp_path)
    assert client.post("/api/upload/destination?name=x.wav", content=b"garbage" * 50).json()["error"]["code"] == "INVALID_AUDIO"
    assert client.post("/api/upload/nope?name=x.wav", content=b"x").status_code == 400
    assert client.post("/api/settings", json={"wet_gain_db": 99}).json()["error"]["code"] == "INVALID_PARAMETER"

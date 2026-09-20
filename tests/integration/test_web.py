import io as pyio
import json
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

    st = client.post("/api/export", json={}).json()
    files = st["last_export"]["files"]
    assert files["wet_wav"] == "ADR2_IR_ONLY.wav"
    ir = client.post("/api/export", json={"mode": "ir_profile"}).json()["last_export"]["files"]
    assert ir["ir_profile_wav"] == "ADR2_IR_PROFILE.wav"
    dl = client.get(f"/api/exports/{files['wet_wav']}")
    back, fs = sf.read(pyio.BytesIO(dl.content), dtype="float32")
    np.testing.assert_array_equal(back, np.frombuffer(client.get("/api/pcm/wet").content, dtype="<f4"))
    assert fs == FS
    assert client.get("/api/exports/..%2F..%2Fsecret.wav").status_code in (400, 404)


def test_new_pair_clears_both_files_without_touching_exports(tmp_path):
    """Enchaîner deux couples : remplacer la seule SOURCE relancerait un calcul avec l'ancien ADR."""
    calls = []
    client = make_client(tmp_path, calls)
    rng = np.random.default_rng(7)
    client.post("/api/upload/source?name=s1.wav", content=wav_bytes(speechy(8, seed=1)))
    client.post("/api/upload/destination?name=d1.wav", content=wav_bytes(speechy(6, seed=2)))
    wait_idle(client)
    st = client.post("/api/export", json={}).json()
    exported = tmp_path / "exports" / st["last_export"]["files"]["wet_wav"]
    assert exported.exists() and len(calls) == 1

    st = client.post("/api/session/new").json()
    assert st["source"] is None and st["destination"] is None
    assert st["profile"] is None and st["render"] is None and st["last_export"] is None
    assert st["job"]["state"] == "idle" and st["eq"]["state"] in ("pending", "disabled")
    assert exported.exists()                      # les exports sont conservés
    assert not any((tmp_path / "data" / "uploads").iterdir())

    # Une source seule ne déclenche rien : on a le temps de déposer la destination.
    st = client.post("/api/upload/source?name=s2.wav", content=wav_bytes(speechy(8, seed=3))).json()
    assert st["render"] is None and len(calls) == 1
    client.post("/api/upload/destination?name=d2.wav", content=wav_bytes(speechy(6, seed=4)))
    st = wait_idle(client)
    assert st["render"] is not None and len(calls) == 2


def test_clear_cache_resets_session_and_deletes_exports(tmp_path):
    client = make_client(tmp_path)
    rng = np.random.default_rng(2)
    client.post("/api/upload/source?name=s.wav", content=wav_bytes(rng.uniform(-0.2, 0.2, FS * 3)))
    client.post("/api/upload/destination?name=d.wav", content=wav_bytes(rng.uniform(-0.2, 0.2, FS)))
    wait_idle(client)
    st = client.post("/api/export", json={}).json()
    exported = tmp_path / "exports" / st["last_export"]["files"]["wet_wav"]
    assert exported.exists()
    state = client.get("/api/state").json()
    assert state["cache_bytes"] > 0 and state["exports"]["count"] == 2  # wav + json

    st = client.post("/api/cache/clear").json()
    assert st["freed_bytes"] > 0 and st["deleted_exports"] == 2
    assert st["source"] is None and st["destination"] is None
    assert st["profile"] is None and st["render"] is None and st["cache_bytes"] == 0
    assert st["job"]["state"] in ("idle", "cancelled") and st["last_error"] is None
    assert not any((tmp_path / "data" / "uploads").iterdir())
    assert not exported.exists() and not any((tmp_path / "exports").iterdir())
    assert st["exports"]["count"] == 0

    # la session repart proprement
    client.post("/api/upload/source?name=s2.wav", content=wav_bytes(rng.uniform(-0.2, 0.2, FS * 3)))
    client.post("/api/upload/destination?name=d2.wav", content=wav_bytes(rng.uniform(-0.2, 0.2, FS)))
    assert wait_idle(client)["render"]["stale"] is False


def speechy(seconds, fs=FS, seed=0):
    """Signal assez « vocal » pour l'analyse de timbre (harmoniques modulées + pauses)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * fs)) / fs
    phase = 2 * np.pi * np.cumsum(130 * (1 + 0.1 * np.sin(2 * np.pi * 2.7 * t))) / fs
    x = sum(np.sin(k * phase) / k for k in range(1, 20)) + 0.02 * rng.standard_normal(t.size)
    x *= (np.sin(2 * np.pi * 0.4 * t) > -0.3)
    return 0.3 * x / np.max(np.abs(x))


def test_match_eq_toggle_render_and_both_export_modes(tmp_path):
    calls = []
    client = make_client(tmp_path, calls)
    client.post("/api/upload/source?name=boom.wav", content=wav_bytes(speechy(8, seed=1)))
    client.post("/api/upload/destination?name=adr.wav", content=wav_bytes(speechy(6, seed=2)))
    # Match EQ actif par défaut : la correction arrive sans action de l'utilisateur.
    st = wait_idle(client)
    assert st["eq"]["state"] == "ready", st["eq"]
    assert st["render"]["eq_applied"] is True and len(calls) == 1

    st = client.post("/api/eq/settings", json={"enabled": False}).json()
    st = wait_idle(client)
    assert st["eq"]["state"] == "disabled" and st["render"]["eq_applied"] is False
    wet_plain = np.frombuffer(client.get("/api/pcm/wet").content, dtype="<f4")

    st = client.post("/api/eq/settings", json={"enabled": True}).json()
    st = wait_idle(client)
    assert st["eq"]["state"] == "ready" and st["render"]["eq_applied"] is True
    assert len(calls) == 1  # aucune nouvelle analyse de pièce
    assert st["eq"]["curve"]["gain_db"] and st["eq"]["applied_common_gain_db"] is not None
    wet_eq = np.frombuffer(client.get("/api/pcm/wet").content, dtype="<f4")
    original = np.frombuffer(client.get("/api/pcm/original").content, dtype="<f4")
    dry = np.frombuffer(client.get("/api/pcm/dry").content, dtype="<f4")
    assert original.size == dry.size and not np.allclose(original, dry)  # dry = ADR corrigé
    assert wet_eq.size >= wet_plain.size and not np.array_equal(wet_eq[: wet_plain.size], wet_plain)

    # export des deux modes ; le clip traité exige le Match EQ
    st = client.post("/api/export", json={"mode": "wet"}).json()
    assert st["last_export"]["files"]["wet_wav"].endswith("_IR_ONLY.wav")
    st = client.post("/api/export", json={"mode": "matched"}).json()
    matched = st["last_export"]["files"]["matched_wav"]
    assert matched.endswith("_EQ_IR_MIX.wav")
    audio, fs = sf.read(pyio.BytesIO(client.get(f"/api/exports/{matched}").content), dtype="float32")
    np.testing.assert_allclose(audio, dry + wet_eq, atol=1e-6)  # voix corrigée + reverb
    report = json.loads((tmp_path / "exports" / st["last_export"]["files"]["report_json"]).read_text(encoding="utf-8"))
    assert report["output"]["mode"] == "matched" and report["eq"]["amount"] == 1.0
    assert "REMPLACE" in report["placement"]

    # retour à OFF : rendu identique au tout premier, et plus de clip traité possible
    client.post("/api/eq/settings", json={"enabled": False})
    st = wait_idle(client)
    assert st["render"]["eq_applied"] is False
    assert client.post("/api/export", json={"mode": "matched"}).json()["error"]["code"] == "EQ_REQUIRED"
    np.testing.assert_array_equal(np.frombuffer(client.get("/api/pcm/wet").content, dtype="<f4"), wet_plain)
    assert len(calls) == 1


def test_eq_settings_validation(tmp_path):
    client = make_client(tmp_path)
    assert client.post("/api/eq/settings", json={"amount": 5}).json()["error"]["code"] == "INVALID_PARAMETER"
    assert client.post("/api/eq/settings", json={"bidon": 1}).json()["error"]["code"] == "INVALID_PARAMETER"
    assert client.post("/api/export", json={"mode": "bidon"}).json()["error"]["code"] == "INVALID_PARAMETER"


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

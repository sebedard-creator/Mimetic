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


def fake_estimator_per_channel(calls):
    """Estimateur de test donnant une pièce **différente** selon le canal analysé."""
    def estimate(signal, fs, *, workdir, client, reference_meta, progress):
        calls.append(reference_meta.get("channel_mode"))
        progress("Analyse de la pièce")
        # Canal gauche : pièce courte, une réflexion proche. Canal droit : pièce plus longue.
        right = reference_meta.get("channel_mode") == "right"
        h = np.zeros(16000 if right else 8000)
        h[0] = 1.0
        if right:
            h[400], h[12000] = 0.5, 0.25
        else:
            h[200] = 0.2
        p = known_ir.build_profile(h, 16000, source_name="fake", source_sha256="0", convention="total")
        p.parameters["windows"] = []
        return p
    return estimate


def test_two_channel_files_are_processed_as_two_independent_tracks(tmp_path):
    """A1 et A2 = perche et lavalier : deux pièces, deux timbres, un export entrelacé dans l'ordre."""
    calls = []
    caps = lambda: {"available": True, "detail": None}  # noqa: E731
    client = TestClient(create_app(tmp_path / "data", tmp_path / "exports",
                                   estimator=fake_estimator_per_channel(calls), capabilities=caps))
    boom, lav = speechy(8, seed=1), speechy(8, seed=5) * 0.6
    adr_boom, adr_lav = speechy(6, seed=2), speechy(6, seed=6) * 0.6
    client.post("/api/upload/source?name=ref.wav",
                content=wav_bytes(np.stack([boom, lav], axis=1)))
    client.post("/api/upload/destination?name=adr.wav",
                content=wav_bytes(np.stack([adr_boom, adr_lav], axis=1)))
    st = wait_idle(client)

    assert st["multitrack"] is True and [t["label"] for t in st["tracks"]] == ["A1", "A2"]
    assert calls == ["left", "right"]                      # une analyse par piste, dans l'ordre
    a1, a2 = st["tracks"]
    assert a1["source_channel"] == "left" and a2["source_channel"] == "right"
    assert a1["profile"]["profile_id"] != a2["profile"]["profile_id"]
    assert a1["render"]["length_frames"] != a2["render"]["length_frames"]   # IR de durées différentes
    assert a1["eq"]["state"] == "ready" and a2["eq"]["state"] == "ready"
    assert a1["eq"]["curve"]["gain_db"] != a2["eq"]["curve"]["gain_db"]     # timbres corrigés séparément

    # Les flux d'écoute sont bien ceux de la piste demandée.
    wet1 = np.frombuffer(client.get("/api/pcm/wet?track=0").content, dtype="<f4")
    wet2 = np.frombuffer(client.get("/api/pcm/wet?track=1").content, dtype="<f4")
    assert wet1.size == a1["render"]["length_frames"] and wet2.size == a2["render"]["length_frames"]
    assert client.get("/api/pcm/wet?track=2").status_code == 400

    # Export entrelacé : A1 reste A1, A2 reste A2, la piste courte est complétée à la fin.
    st = client.post("/api/export", json={"mode": "wet"}).json()
    name = st["last_export"]["files"]["wet_wav"]
    audio, fs = sf.read(pyio.BytesIO(client.get(f"/api/exports/{name}").content), dtype="float32")
    assert audio.ndim == 2 and audio.shape[1] == 2 and fs == FS
    assert audio.shape[0] == max(wet1.size, wet2.size)
    np.testing.assert_allclose(audio[: wet1.size, 0], wet1, atol=1e-6)
    np.testing.assert_allclose(audio[: wet2.size, 1], wet2, atol=1e-6)
    assert np.all(audio[wet1.size:, 0] == 0) or wet1.size == audio.shape[0]  # complété à la fin

    report = json.loads((tmp_path / "exports" / st["last_export"]["files"]["report_json"]).read_text(encoding="utf-8"))
    assert report["output"]["channels"] == 2 and report["output"]["channel_order"] == ["A1", "A2"]
    assert report["output"]["shorter_tracks_zero_padded_at_end"] is True
    assert [t["label"] for t in report["tracks"]] == ["A1", "A2"]
    assert report["tracks"][0]["profile"]["profile_id"] != report["tracks"][1]["profile"]["profile_id"]

    # Le clip traité sort lui aussi entrelacé, voix corrigée par piste.
    st = client.post("/api/export", json={"mode": "matched"}).json()
    matched, _ = sf.read(pyio.BytesIO(client.get(
        f"/api/exports/{st['last_export']['files']['matched_wav']}").content), dtype="float32")
    dry1 = np.frombuffer(client.get("/api/pcm/dry?track=0").content, dtype="<f4")
    np.testing.assert_allclose(matched[: dry1.size, 0], dry1 + wet1, atol=1e-6)


def test_channel_count_mismatch_stays_in_manual_mode(tmp_path):
    client = make_client(tmp_path)
    rng = np.random.default_rng(11)
    client.post("/api/upload/source?name=stereo.wav", content=wav_bytes(rng.uniform(-0.2, 0.2, (FS * 4, 2))))
    client.post("/api/upload/destination?name=mono.wav", content=wav_bytes(speechy(5, seed=3)))
    st = wait_idle(client)
    assert st["multitrack"] is False and st["channel_mismatch"] is True
    assert len(st["tracks"]) == 1 and st["tracks"][0]["source_channel"] == "left"
    st = client.post("/api/channel/source", json={"mode": "right"}).json()
    assert st["tracks"][0]["source_channel"] == "right"


def test_one_track_can_fail_without_losing_the_other(tmp_path, monkeypatch):
    """Panne sur A2 : A1 garde son rendu et reste consultable ; l'export entrelacé, lui, est refusé.

    C'est l'état que l'interface doit continuer d'afficher (sélecteur de piste compris) ; un export
    à moitié calculé n'aurait aucun sens dans un fichier entrelacé."""
    from mimetic.web import server as srv

    real_render = srv.pipeline.render

    def render(adr, channel_mode, profile, settings, eq=None):
        if channel_mode == "right":
            raise MemoryError("panne injectée")
        return real_render(adr, channel_mode, profile, settings, eq)

    monkeypatch.setattr(srv.pipeline, "render", render)
    calls = []
    caps = lambda: {"available": True, "detail": None}  # noqa: E731
    client = TestClient(create_app(tmp_path / "data", tmp_path / "exports",
                                   estimator=fake_estimator_per_channel(calls), capabilities=caps))
    client.post("/api/upload/source?name=ref.wav",
                content=wav_bytes(np.stack([speechy(8, seed=1), speechy(8, seed=5)], axis=1)))
    client.post("/api/upload/destination?name=adr.wav",
                content=wav_bytes(np.stack([speechy(6, seed=2), speechy(6, seed=6)], axis=1)))
    st = wait_idle(client)

    a1, a2 = st["tracks"]
    assert st["job"]["state"] == "failed" and st["last_error"]["code"] == "OUT_OF_MEMORY"
    assert a1["render"] is not None and a2["render"] is None    # la piste saine survit
    assert a1["profile"] is not None and a2["profile"] is not None
    assert np.frombuffer(client.get("/api/pcm/wet?track=0").content, dtype="<f4").size > 0
    assert client.get("/api/pcm/wet?track=1").status_code == 400
    for mode in ("wet", "matched", "ir_profile"):
        assert client.post("/api/export", json={"mode": mode}).status_code == 400


def test_eq_failure_on_one_track_blocks_only_the_matched_export(tmp_path):
    """Match EQ impossible sur A2 : les deux pistes se rendent quand même, seul EQ_IR_MIX est refusé."""
    calls = []
    caps = lambda: {"available": True, "detail": None}  # noqa: E731
    client = TestClient(create_app(tmp_path / "data", tmp_path / "exports",
                                   estimator=fake_estimator_per_channel(calls), capabilities=caps))
    client.post("/api/upload/source?name=ref.wav",
                content=wav_bytes(np.stack([speechy(8, seed=1), speechy(8, seed=5)], axis=1)))
    # A2 de la destination : silence numérique, aucun timbre à comparer.
    adr = np.stack([speechy(6, seed=2), np.zeros(6 * FS)], axis=1)
    client.post("/api/upload/destination?name=adr.wav", content=wav_bytes(adr))
    st = wait_idle(client)

    a1, a2 = st["tracks"]
    assert a1["eq"]["state"] == "ready" and a2["eq"]["state"] == "failed"
    assert a2["eq"]["error"]["code"] == "EQ_INSUFFICIENT_SPEECH"
    assert a1["render"] is not None and a2["render"] is not None   # la reverb reste calculée
    assert st["job"]["state"] == "idle"

    r = client.post("/api/export", json={"mode": "matched"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "EQ_REQUIRED"
    st = client.post("/api/export", json={"mode": "wet"}).json()   # l'export sans EQ reste possible
    audio, _ = sf.read(pyio.BytesIO(client.get(
        f"/api/exports/{st['last_export']['files']['wet_wav']}").content), dtype="float32")
    assert audio.ndim == 2 and audio.shape[1] == 2


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
    assert report["output"]["mode"] == "matched" and report["output"]["channels"] == 1
    assert report["tracks"][0]["eq"]["amount"] == 1.0
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

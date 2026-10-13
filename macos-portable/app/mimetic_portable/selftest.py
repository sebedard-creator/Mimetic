"""Offline checks run against source and the final frozen executable."""
from __future__ import annotations

import json
import platform
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf
from mimetic import pro_tools
from mimetic.engines import known_ir
from mimetic.engines.recrir import adapter
from mimetic_portable.runtime import resource_root


def diagnostics():
    import torch
    return {"platform": platform.platform(), "architecture": platform.machine(),
            "python": platform.python_version(), "numpy": np.__version__, "torch": torch.__version__,
            "pt_api": pro_tools.capabilities(), "engine": adapter.capabilities(),
            "resources": str(resource_root())}


def synthetic_input() -> np.ndarray:
    t = np.arange(32000, dtype=np.float64) / 16000
    envelope = 0.2 + 0.8 * np.sin(np.pi * t * 2.7)**2
    return (envelope * (0.12 * np.sin(2*np.pi*173*t) + 0.06 * np.sin(2*np.pi*349*t)
                        + 0.035 * np.sin(2*np.pi*711*t))).astype(np.float32)


def raw_engine(work: Path):
    client = adapter.WorkerClient()
    try:
        input_wav, result = work / "engine.wav", work / "engine.npy"
        sf.write(input_wav, synthetic_input(), 16000, subtype="FLOAT")
        response = client.request({"cmd": "estimate", "job_id": "portable-self-test",
                                   "input_wav": str(input_wav), "output_npy": str(result)})
        if not response.get("ok"):
            raise RuntimeError(f"Moteur : {response}")
        rir = np.load(result, allow_pickle=False)
        if rir.ndim != 1 or len(rir) < 1000 or not np.isfinite(rir).all() or not np.any(rir):
            raise RuntimeError("Réponse du moteur invalide")
        return rir, response["diagnostics"]
    finally:
        client.stop()


def build_fixture(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    start = 1740100860
    specs = []
    for role, track, seconds in (("reference", "MIMETIC_IR_P01", 1.55),
                                 ("adr", "MIMETIC_MIX_P01", 0.1)):
        t = np.arange(int(48000 * seconds)) / 48000
        x = (0.1 if role == "reference" else 0.04) * np.sin(2*np.pi*193*t)
        audio = root / f"{role}.wav"
        pro_tools.write_bwf(audio, x, start)
        specs.append({"audio_path": str(audio), "track_name": track, "clip_name": role,
                      "placement_start_samples": start})
    result = pro_tools.api().build_audio_session(str(pro_tools.TEMPLATE), specs, str(root / "session"), session_name="Synthetic")
    session = Path(result["session_path"])
    archive_path = root / "Scene_012.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(session, "Scene_012/Synthetic.ptx")
        for file in (session.parent / "Audio Files").glob("*.wav"):
            archive.write(file, "Scene_012/Audio Files/" + file.name)
    return archive_path


def fixed_estimator(signal, fs, **kwargs):
    h = np.zeros(48000)
    h[0], h[480], h[-1] = 1.0, 0.3, -0.1
    return known_ir.build_profile(h, fs, source_name="synthetic-test", source_sha256="0", convention="total")


def run(emit=lambda obj: None):
    from mimetic_portable.job import Job, hash_file
    d = diagnostics()
    if not d["engine"]["available"] or not d["pt_api"]["available"]:
        raise RuntimeError(f"Ressources manquantes : {d}")
    emit({"event": "progress", "step": "Autotest : modèle et ressources embarquées", "completed": 0, "total": 2})
    with tempfile.TemporaryDirectory(prefix="mimetic-selftest-") as temporary:
        work = Path(temporary)
        rir, engine_diag = raw_engine(work)
        baseline = resource_root() / "validation" / "engine_baseline.npy"
        comparison = None
        if baseline.is_file():
            expected = np.load(baseline, allow_pickle=False)
            if expected.shape != rir.shape:
                raise RuntimeError("La longueur d'IR diffère du rendu de référence Windows.")
            error = float(np.linalg.norm(rir.astype(np.float64) - expected) / max(np.linalg.norm(expected), 1e-12))
            if error > 0.002:
                raise RuntimeError(f"Écart d'inférence supérieur à la tolérance : {error:.6g}")
            comparison = {"relative_l2": error, "maximum_relative_l2": 0.002}
        emit({"event": "progress", "step": "Autotest : export PTX et régions courtes", "completed": 1, "total": 2})
        archive = build_fixture(work / "fixture")
        original_hash = hash_file(archive)
        # Known sparse IR gives an independent exact truth for the audio and
        # avoids interpreting synthetic tones as speech in the inference check.
        from mimetic.domain.errors import MimeticError
        original_eq = pro_tools.eq_match.build_profile
        def no_eq(*args, **kwargs):
            raise MimeticError("EQ_INSUFFICIENT_SPEECH")
        pro_tools.eq_match.build_profile = no_eq
        try:
            output = Job(estimator=fixed_estimator).run(archive)
        finally:
            pro_tools.eq_match.build_profile = original_eq
        if hash_file(archive) != original_hash:
            raise RuntimeError("L'archive originale a été modifiée")
        exported = work / "verify"
        with zipfile.ZipFile(output) as zip_file:
            zip_file.extractall(exported)  # our own generated archive only
        session = next(exported.rglob("*.ptx"))
        native = pro_tools.api().ProToolsSession(str(session))
        rows = native.get_timeline_clips()
        if len(rows) != 2 or len(native.get_tracks()) != 6:
            raise RuntimeError("Nombre de pistes ou de clips exportés incorrect")
        dry, _ = sf.read(work / "fixture" / "session" / "Audio Files" / "adr.wav")
        expected = np.zeros(4800 + 48000 - 1)
        expected[480:480+len(dry)] += 0.3 * dry
        expected[47999:47999+len(dry)] -= 0.1 * dry
        for row in rows:
            mix = row["track"].startswith("MIMETIC_MIX")
            if row["start_samples"] != 1740100860 or row["muted"] != mix or pro_tools.clip_gain(native, row["clip_name"]) != (0.0 if mix else 3.5):
                raise RuntimeError("Timecode, mute ou Clip Gain incorrect")
            audio, _ = sf.read(session.parent / "Audio Files" / row["physical_filename"])
            truth = expected.copy()
            if mix:
                truth[:len(dry)] += dry
            np.testing.assert_allclose(audio, truth.astype(np.float32), atol=1e-7)
    emit({"event": "self_test_ready", "diagnostics": d, "engine": engine_diag,
          "comparison": comparison, "checks": ["bundled model inference", "native template + silent WAV",
          "PTX roundtrip", "exact sample positions", "IR +3.5 metadata", "MIX muted",
          "50k-sample full tail", "unchanged input ZIP", "offline source-independent execution"]})

"""Orchestration rendu/export partagée par la CLI et le service web (architecture §7.2)."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from mimetic import DSP_VERSION, __version__
from mimetic.audio import dsp, hybrid, io
from mimetic.domain.errors import MimeticError
from mimetic.domain.models import AudioAsset, RenderResult, RenderSettings, RoomProfile


def profile_ir_at_rate(profile: RoomProfile, fs: int) -> np.ndarray:
    return profile_ir_and_info(profile, fs)[0]


def profile_ir_and_info(profile: RoomProfile, fs: int) -> tuple[np.ndarray, dict | None]:
    """IR wet à la fréquence de rendu ; avec extension hybride si le profil en porte une (mise en cache)."""
    if fs not in profile._rate_cache:
        if profile.extension and fs > profile.sample_rate_hz:
            h, info = hybrid.extend(profile.wet_ir, profile.sample_rate_hz, fs, profile.extension,
                                    seed=profile.seed if profile.seed is not None else 0)
        else:
            h, info = dsp.resample_ir(profile.wet_ir, profile.sample_rate_hz, fs), None
        profile._rate_cache[fs] = (h, info)
    return profile._rate_cache[fs]


def dependency_key(adr: AudioAsset, channel_mode: str, profile: RoomProfile, settings: RenderSettings) -> str:
    parts = [adr.sha256, channel_mode, profile.profile_id, repr(settings.wet_gain_db),
             repr(settings.additional_predelay_seconds), DSP_VERSION]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def render(adr: AudioAsset, channel_mode: str, profile: RoomProfile, settings: RenderSettings) -> RenderResult:
    if "DIRECT_PATH_UNRESOLVED" in profile.quality_reasons:
        # Profil non renderable, indépendamment de son statut descriptif (§6.4, §7.1).
        raise MimeticError("DIRECT_PATH_UNRESOLVED")
    x = io.select_channel(adr, channel_mode)
    fs = adr.sample_rate_hz
    h, ir_info = profile_ir_and_info(profile, fs)
    dry, wet = dsp.render_wet(x, h, fs, settings)
    wet_peak = float(np.max(np.abs(wet))) if wet.size else 0.0
    mix_peak = float(np.max(np.abs(dry + wet)))
    warnings = [w for w in profile.quality_reasons if w not in ("BANDWIDTH_LIMITED", "HF_SYNTHESIZED")]
    if mix_peak > 1.0 or wet_peak > 1.0:
        warnings.append("MIX_OVER_0DBFS")
    if ir_info and ir_info.get("synthesized_bands"):
        warnings.append("HF_SYNTHESIZED")  # aigus présents mais synthétisés, jamais présentés comme estimés
    elif profile.usable_band_hz[1] is not None and profile.usable_band_hz[1] < fs / 2:
        warnings.append("BANDWIDTH_LIMITED")
    return RenderResult(dry=dry, wet=wet, sample_rate_hz=fs, wet_peak=wet_peak, mix_peak=mix_peak,
                        warnings=sorted(set(warnings)), dependency_key=dependency_key(adr, channel_mode, profile, settings),
                        ir_info=ir_info)


def _db(v: float) -> float | None:
    return None if v <= 0 else float(20 * np.log10(v))


def safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^\w\-. ]", "_", stem, flags=re.UNICODE).strip(" .") or "ADR"
    return stem[:80]


def export(result: RenderResult, adr: AudioAsset, channel_mode: str, profile: RoomProfile,
           settings: RenderSettings, directory: Path, export_ir: bool = False) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    base = f"{safe_stem(adr.original_name)}_ADR_REVERB"
    # Même suffixe pour le WAV et le JSON, sans écraser un export existant.
    i, stem = 1, base
    while (directory / f"{stem}.wav").exists() or (directory / f"{stem}.json").exists():
        i += 1
        stem = f"{base}_{i}"
    wav_path = directory / f"{stem}.wav"
    json_path = directory / f"{stem}.json"

    io.write_float_wav(wav_path, result.wet, result.sample_rate_hz)

    files = {"wet_wav": wav_path.name, "report_json": json_path.name}
    if export_ir:
        ir_path = io.unique_path(directory, f"{safe_stem(adr.original_name)}_PROFILE_WET_IR", ".wav")
        io.write_float_wav(ir_path, profile_ir_at_rate(profile, result.sample_rate_hz), result.sample_rate_hz)
        files["wet_ir_wav"] = ir_path.name

    report = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "app_version": __version__,
        "dsp_version": DSP_VERSION,
        "destination": {**{k: v for k, v in adr.summary().items() if k != "id"}, "channel_mode": channel_mode},
        "render_settings": {"wet_gain_db": settings.wet_gain_db,
                            "additional_predelay_seconds": settings.additional_predelay_seconds},
        "profile": profile.manifest(),
        "output": {
            "sample_rate_hz": result.sample_rate_hz,
            "length_frames": int(result.wet.size),
            "subtype": "FLOAT",
            "channels": 1,
            "content": "wet only (réflexions), aligné sur l'échantillon 0 de l'ADR",
            "wet_peak_dbfs": _db(result.wet_peak),
            "mix_peak_dbfs": _db(result.mix_peak),
        },
        "render_ir": result.ir_info,
        "warnings": result.warnings,
        "placement": "Aligner le début de ce fichier sur le début exact de l'ADR (BWF TimeReference non copié).",
        "dependency_key": result.dependency_key,
        "files": files,
    }
    if export_ir:
        report["ir_note"] = "Désactiver toute normalisation automatique dans le convolueur externe."
    io.write_json(json_path, report)
    return {"directory": str(directory), "files": files, "report": io.json_safe(report)}

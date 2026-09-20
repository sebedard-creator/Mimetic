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


def dependency_key(adr: AudioAsset, channel_mode: str, profile: RoomProfile, settings: RenderSettings,
                   eq: dict | None = None) -> str:
    parts = [adr.sha256, channel_mode, profile.profile_id, repr(settings.wet_gain_db),
             repr(settings.additional_predelay_seconds), DSP_VERSION]
    if eq and not eq["filter"].get("identity"):
        # Le filtre réellement appliqué fait partie des dépendances du rendu.
        parts += [eq["filter"].get("coefficients_sha256", "identity"), repr(eq.get("amount")),
                  repr(eq.get("preserve_adr_level")), eq.get("method", "")]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def render(adr: AudioAsset, channel_mode: str, profile: RoomProfile, settings: RenderSettings,
           eq: dict | None = None) -> RenderResult:
    """Sans `eq`, le chemin et les échantillons sont exactement ceux d'avant le Match EQ.

    Avec `eq`, le filtre est appliqué **une seule fois** à l'ADR ; la reverb est ensuite calculée
    depuis cet ADR corrigé (architecture-match-eq §4).
    """
    if "DIRECT_PATH_UNRESOLVED" in profile.quality_reasons:
        # Profil non renderable, indépendamment de son statut descriptif (§6.4, §7.1).
        raise MimeticError("DIRECT_PATH_UNRESOLVED")
    x = io.select_channel(adr, channel_mode)
    fs = adr.sample_rate_hz
    h, ir_info = profile_ir_and_info(profile, fs)
    original, eq_info = x, None
    if eq and not eq["filter"].get("identity"):
        from mimetic.analysis import speech_stats
        from mimetic.audio import eq_filter
        mask = speech_stats.voiced_sample_mask(eq.get("voiced_stats") or {}, x.size, fs)
        x, gain_db = eq_filter.apply_filter(x, eq["filter"]["coefficients"], mask,
                                            bool(eq.get("preserve_adr_level", True)))
        eq_info = {"applied_common_gain_db": gain_db, "amount": eq.get("amount"),
                   "method": eq.get("method"), "filter_taps": eq["filter"]["taps"],
                   "filter_fit_error_db": eq["filter"]["fit_error_db"],
                   "coefficients_sha256": eq["filter"].get("coefficients_sha256"),
                   "curve": {"frequencies_hz": np.asarray(eq["curve"]["frequencies_hz"]).tolist(),
                             "gain_db": np.asarray(eq["curve"]["gain_db"]).tolist()},
                   "warnings": list(eq.get("warnings", []))}
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
    if eq_info:
        warnings.extend(eq_info["warnings"])
    padded_original = np.zeros(dry.size)
    padded_original[: original.size] = original
    return RenderResult(dry=dry, wet=wet, sample_rate_hz=fs, wet_peak=wet_peak, mix_peak=mix_peak,
                        warnings=sorted(set(warnings)),
                        dependency_key=dependency_key(adr, channel_mode, profile, settings, eq),
                        ir_info=ir_info, eq_info=eq_info, original=padded_original)


def _db(v: float) -> float | None:
    return None if v <= 0 else float(20 * np.log10(v))


def safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^\w\-. ]", "_", stem, flags=re.UNICODE).strip(" .") or "ADR"
    return stem[:80]


EXPORT_MODES = ("wet", "matched", "ir_profile")
EXPORT_SUFFIX = {"wet": "_IR_ONLY", "matched": "_EQ_IR_MIX", "ir_profile": "_IR_PROFILE"}
EXPORT_FILE_KEY = {"wet": "wet_wav", "matched": "matched_wav", "ir_profile": "ir_profile_wav"}


def interleave(signals: list[np.ndarray]) -> np.ndarray:
    """Assemble des pistes en un tableau [frames, canaux], dans l'ordre reçu.

    Deux pistes n'ont pas la même longueur quand leurs IR diffèrent : on complète **à la fin**,
    jamais au début, pour que l'échantillon zéro reste l'origine commune de toutes les pistes.
    """
    length = max(s.size for s in signals)
    out = np.zeros((length, len(signals)))
    for i, s in enumerate(signals):
        out[: s.size, i] = s
    return out


def export(tracks: list[dict], adr: AudioAsset, settings: RenderSettings, directory: Path,
           mode: str = "wet") -> dict:
    """Écrit un fichier par mode, **entrelacé dans l'ordre des pistes**.

    mode="wet" : reverb seule, à poser en parallèle de l'ADR original (comportement historique).
    mode="matched" : l'ADR traité (EQ de raccord + reverb), qui **remplace** l'ADR.
    mode="ir_profile" : l'IR wet de la pièce seule, pour un convolueur externe.

    Chaque piste porte son `result`, son `profile`, son `channel_mode` et son `label` ; l'ordre des
    canaux de sortie est exactement celui des canaux d'entrée (A1 reste A1).
    """
    if mode not in EXPORT_MODES:
        raise MimeticError("INVALID_PARAMETER", f"mode d'export inconnu : {mode}")
    if not tracks:
        raise MimeticError("INVALID_PARAMETER", "aucune piste à exporter")
    matched = mode == "matched"
    if matched and any(t["result"].eq_info is None for t in tracks):
        # Un clip complet sans raccord de timbre n'a pas d'usage : c'est l'ADR + reverb, déjà
        # obtenable en posant la reverb seule. Le mode matched exige donc le Match EQ.
        raise MimeticError("EQ_REQUIRED", "activez Match EQ pour exporter le clip traité")
    directory.mkdir(parents=True, exist_ok=True)
    base = f"{safe_stem(adr.original_name)}{EXPORT_SUFFIX[mode]}"
    # Même suffixe pour le WAV et le JSON, sans écraser un export existant.
    i, stem = 1, base
    while (directory / f"{stem}.wav").exists() or (directory / f"{stem}.json").exists():
        i += 1
        stem = f"{base}_{i}"
    wav_path = directory / f"{stem}.wav"
    json_path = directory / f"{stem}.json"

    channels = []
    per_track = []
    for t in tracks:
        result, profile = t["result"], t["profile"]
        signal = {"wet": result.wet, "matched": result.dry + result.wet}.get(mode)
        if signal is None:
            signal = profile_ir_at_rate(profile, result.sample_rate_hz)
        channels.append(signal)
        per_track.append({
            "channel": len(channels),
            "label": t.get("label"),
            "source_channel": t.get("source_channel"),
            "destination_channel": t.get("channel_mode"),
            "length_frames": int(signal.size),
            "peak_dbfs": _db(float(np.max(np.abs(signal))) if signal.size else 0.0),
            "wet_peak_dbfs": _db(result.wet_peak),
            "mix_peak_dbfs": _db(result.mix_peak),
            "profile": profile.manifest(),
            "render_ir": result.ir_info,
            "eq": result.eq_info,
            "warnings": result.warnings,
            "dependency_key": result.dependency_key,
        })
    audio = interleave(channels) if len(channels) > 1 else channels[0]
    fs = tracks[0]["result"].sample_rate_hz
    io.write_float_wav(wav_path, audio, fs)
    files = {EXPORT_FILE_KEY[mode]: wav_path.name, "report_json": json_path.name}

    report = {
        "schema_version": 2,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "app_version": __version__,
        "dsp_version": DSP_VERSION,
        "destination": {k: v for k, v in adr.summary().items() if k != "id"},
        "render_settings": {"wet_gain_db": settings.wet_gain_db,
                            "additional_predelay_seconds": settings.additional_predelay_seconds},
        "output": {
            "mode": mode,
            "sample_rate_hz": fs,
            "length_frames": int(audio.shape[0] if audio.ndim > 1 else audio.size),
            "subtype": "FLOAT",
            "channels": len(channels),
            "channel_order": [t.get("label") for t in tracks],
            "content": {"matched": "ADR traité : voix corrigée (EQ de raccord) + reverb de la pièce",
                        "wet": "wet only (réflexions), aligné sur l'échantillon 0 de l'ADR",
                        "ir_profile": "IR wet de la pièce, relative à un direct unitaire"}[mode],
            "peak_dbfs": _db(float(np.max(np.abs(audio))) if audio.size else 0.0),
            "shorter_tracks_zero_padded_at_end": bool(len({c.size for c in channels}) > 1),
        },
        "tracks": per_track,
        "warnings": sorted({w for t in per_track for w in t["warnings"]}),
        "placement": {
            "matched": ("REMPLACE l'ADR original : ne pas superposer les deux, cela doublerait la voix. "
                        "Caler sur le début exact de l'ADR (BWF TimeReference non copié)."),
            "wet": "Aligner le début de ce fichier sur le début exact de l'ADR (BWF TimeReference non copié).",
            "ir_profile": ("À charger dans un convolueur : désactiver toute normalisation automatique "
                           "pour conserver le dosage calibré."),
        }[mode],
        "dependency_keys": [t["dependency_key"] for t in per_track],
        "files": files,
    }
    io.write_json(json_path, report)
    return {"directory": str(directory), "files": files, "report": io.json_safe(report)}

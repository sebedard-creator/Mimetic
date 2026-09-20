"""Import/validation WAV et export flottant (architecture §6.1, §6.9)."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from mimetic.domain.errors import MimeticError
from mimetic.domain.models import (
    ACCEPTED_RATES,
    ACCEPTED_SUBTYPES,
    MAX_FILE_SECONDS,
    AudioAsset,
    ChannelMode,
)


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_wav(path: str | os.PathLike, original_name: str | None = None,
             max_seconds: float = MAX_FILE_SECONDS,
             accepted_rates: tuple[int, ...] = ACCEPTED_RATES) -> AudioAsset:
    """Lit l'en-tête, valide le périmètre V1, puis décode en float64 [frames, channels]."""
    path = Path(path)
    try:
        info = sf.info(str(path))
    except Exception as exc:  # libsndfile refuse le contenu, quelle que soit l'extension
        raise MimeticError("INVALID_AUDIO", str(exc)) from exc

    if info.format != "WAV":
        raise MimeticError("UNSUPPORTED_FORMAT", f"conteneur {info.format}")
    if info.subtype not in ACCEPTED_SUBTYPES:
        raise MimeticError("UNSUPPORTED_FORMAT", f"sous-type {info.subtype}")
    if info.samplerate not in accepted_rates:
        raise MimeticError("UNSUPPORTED_FORMAT", f"{info.samplerate} Hz")
    if info.channels not in (1, 2):
        raise MimeticError("UNSUPPORTED_FORMAT", f"{info.channels} canaux")
    if info.frames <= 0:
        raise MimeticError("INVALID_AUDIO", "fichier vide")
    if info.frames / info.samplerate > max_seconds:
        raise MimeticError("LIMIT_EXCEEDED",
                           f"durée {info.frames / info.samplerate:.1f} s > {max_seconds:.0f} s")

    try:
        data, sr = sf.read(str(path), dtype="float64", always_2d=True)
    except Exception as exc:
        raise MimeticError("INVALID_AUDIO", str(exc)) from exc
    if data.shape[0] == 0:
        raise MimeticError("INVALID_AUDIO", "aucun échantillon décodé")
    if not np.all(np.isfinite(data)):
        raise MimeticError("INVALID_AUDIO", "valeurs non finies")

    peak = float(np.max(np.abs(data)))
    # Indice d'écrêtage : plusieurs échantillons consécutifs collés au pic (pas une preuve).
    clipping = False
    if peak >= 0.999:
        stuck = np.abs(data) >= peak - 1e-6
        run = np.convolve(stuck.any(axis=1).astype(np.int8), np.ones(3, dtype=np.int8), "valid")
        clipping = bool(np.any(run >= 3))

    return AudioAsset(
        id=uuid.uuid4().hex,
        path=str(path),
        original_name=original_name or path.name,
        sha256=sha256_file(path),
        sample_rate_hz=int(sr),
        frame_count=int(data.shape[0]),
        channels=int(data.shape[1]),
        subtype=info.subtype,
        peak=peak,
        clipping_suspected=clipping,
        data=data,
    )


def select_channel(asset: AudioAsset, mode: ChannelMode) -> np.ndarray:
    """Retourne un signal mono 1-D. Aucune somme automatique : le mode est explicite."""
    d = asset.data
    if asset.channels == 1:
        if mode not in ("mono", "left", "mean"):
            raise MimeticError("INVALID_PARAMETER", f"canal {mode} absent d'un fichier mono")
        return d[:, 0].copy()
    if mode == "left":
        return d[:, 0].copy()
    if mode == "right":
        return d[:, 1].copy()
    if mode == "mean":
        return d.mean(axis=1)
    raise MimeticError("INVALID_PARAMETER", "fichier 2 canaux : choisir left, right ou mean")


def waveform_overview(signal_2d: np.ndarray, buckets: int = 1200) -> dict:
    """Aperçu min/max par canal pour l'affichage."""
    frames = signal_2d.shape[0]
    buckets = max(1, min(buckets, frames))
    edges = np.linspace(0, frames, buckets + 1).astype(np.int64)
    out = []
    for c in range(signal_2d.shape[1]):
        ch = signal_2d[:, c]
        mins = np.minimum.reduceat(ch, edges[:-1])
        maxs = np.maximum.reduceat(ch, edges[:-1])
        out.append({"min": np.round(mins, 4).tolist(), "max": np.round(maxs, 4).tolist()})
    return {"buckets": buckets, "frames": int(frames), "channels": out}


def write_float_wav(path: str | os.PathLike, signal: np.ndarray, sample_rate_hz: int) -> None:
    """Écrit un WAV 32 bits flottant via fichier temporaire + renommage atomique. Aucune normalisation.

    `signal` est mono (1-D) ou multipiste entrelacé (2-D `[frames, canaux]`, ordre conservé).
    """
    path = Path(path)
    signal = np.asarray(signal)
    tmp = path.with_name(path.name + ".tmp")
    sf.write(str(tmp), signal.astype(np.float32), sample_rate_hz, subtype="FLOAT", format="WAV")
    check = sf.info(str(tmp))
    if check.frames != signal.shape[0] or check.samplerate != sample_rate_hz:
        tmp.unlink(missing_ok=True)
        raise MimeticError("ENGINE_FAILURE", "vérification de l'export échouée")
    os.replace(tmp, path)


def json_safe(obj):
    """Remplace NaN/Inf par null (architecture §7)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.generic):
        return json_safe(obj.item())
    return obj


def write_json(path: str | os.PathLike, payload: dict) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False),
                   encoding="utf-8")
    os.replace(tmp, path)


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    """Ne jamais écraser un export existant : ajoute _2, _3..."""
    candidate = directory / f"{stem}{suffix}"
    i = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{i}{suffix}"
        i += 1
    return candidate

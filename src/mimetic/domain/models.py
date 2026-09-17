"""Contrats de données internes (architecture §7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

ChannelMode = Literal["mono", "left", "right", "mean"]

# Limites V1 configurables (architecture §2.1).
MAX_FILE_SECONDS = 600.0
MAX_SELECTION_SECONDS = 30.0
MAX_IR_SECONDS = 10.0
ACCEPTED_RATES = (44100, 48000)
ACCEPTED_SUBTYPES = ("PCM_16", "PCM_24", "FLOAT")


@dataclass
class AudioAsset:
    id: str
    path: str
    original_name: str
    sha256: str
    sample_rate_hz: int
    frame_count: int
    channels: int
    subtype: str
    peak: float
    clipping_suspected: bool
    data: np.ndarray = field(repr=False)  # float64, [frames, channels]

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.sample_rate_hz

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.original_name,
            "sha256": self.sha256,
            "sample_rate_hz": self.sample_rate_hz,
            "frame_count": self.frame_count,
            "duration_seconds": self.duration_seconds,
            "channels": self.channels,
            "subtype": self.subtype,
            "peak": self.peak,
            "clipping_suspected": self.clipping_suspected,
        }


@dataclass(frozen=True)
class ReferenceSelection:
    asset_id: str
    asset_sha256: str
    start_frame: int
    end_frame: int
    channel_mode: ChannelMode


@dataclass
class RoomProfile:
    """Profil wet canonique : direct retiré, origine à l'arrivée directe, gain relatif à un direct unitaire."""

    profile_id: str
    engine_id: str
    status: str  # "validated_dsp" | "manual" | "experimental"
    wet_ir: np.ndarray = field(repr=False)  # float64 mono
    sample_rate_hz: int
    time_origin: str
    direct_removal: dict
    gain_calibration: str  # "exact_impulse" | "energy_equivalent" | "manual" | "manual_required"
    drr_db: float | None
    usable_band_hz: list
    tail_horizon_seconds: float | None
    quality_reasons: list[str]
    parameters: dict
    provenance: dict
    seed: int | None = None
    # Extension hybride des aigus (mimetic.audio.hybrid) : paramètres mesurés sur wet_ir, appliqués au rendu.
    extension: dict | None = None
    _rate_cache: dict = field(default_factory=dict, repr=False, compare=False)

    def manifest(self) -> dict:
        return {
            "extension": self.extension,
            "schema_version": 1,
            "profile_id": self.profile_id,
            "status": self.status,
            "engine": {"id": self.engine_id, **self.provenance},
            "wet_ir_sample_rate_hz": self.sample_rate_hz,
            "wet_ir_length_frames": int(self.wet_ir.size),
            "time_origin": self.time_origin,
            "direct_removal": self.direct_removal,
            "gain_calibration": self.gain_calibration,
            "drr_db": self.drr_db,
            "usable_band_hz": self.usable_band_hz,
            "tail_horizon_seconds": self.tail_horizon_seconds,
            "parameters": self.parameters,
            "seed": self.seed,
            "quality": {"reasons": list(self.quality_reasons)},
        }


@dataclass(frozen=True)
class RenderSettings:
    wet_gain_db: float = 0.0
    additional_predelay_seconds: float = 0.0


@dataclass
class RenderResult:
    dry: np.ndarray = field(repr=False)  # x paddé, longueur N+M-1
    wet: np.ndarray = field(repr=False)
    sample_rate_hz: int
    wet_peak: float
    mix_peak: float
    warnings: list[str]
    dependency_key: str
    ir_info: dict | None = None  # détail de l'IR de rendu (bandes synthétisées)

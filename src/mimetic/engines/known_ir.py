"""Backend `known_ir` : IR mesurée ou synthétique dont la convention du direct est déclarée (architecture §4.1, §6.4)."""

from __future__ import annotations

import uuid

import numpy as np

from mimetic import DSP_VERSION
from mimetic.audio import dsp
from mimetic.domain.errors import MimeticError
from mimetic.domain.models import MAX_IR_SECONDS, RoomProfile

ENGINE_ID = "known_ir"
ADAPTER_VERSION = "0.1.0"


def suggest_direct_index(h: np.ndarray, threshold_ratio: float = 0.5) -> int:
    """Suggestion affichée à l'utilisateur : premier échantillon atteignant 50 % du pic absolu.

    Ce n'est pas un détecteur validé (une réflexion peut dominer) : l'indice doit être confirmé.
    """
    a = np.abs(np.asarray(h, dtype=np.float64))
    peak = a.max() if a.size else 0.0
    if peak <= 0:
        return 0
    return int(np.argmax(a >= threshold_ratio * peak))


def build_profile(h: np.ndarray, sample_rate_hz: int, *, source_name: str, source_sha256: str,
                  convention: str, direct_index: int = 0, direct_length: int = 1) -> RoomProfile:
    """convention :
    - "total" : IR totale avec direct à `direct_index` (longueur `direct_length`) → séparation + calibration ;
    - "wet_unit_direct" : IR déjà wet, relative à un direct unitaire à n=0 (ex. export Mimetic).
    """
    h = np.asarray(h, dtype=np.float64).ravel()
    if h.size > int(MAX_IR_SECONDS * sample_rate_hz) + 1:
        raise MimeticError("LIMIT_EXCEEDED", f"IR > {MAX_IR_SECONDS:.0f} s")
    reasons: list[str] = []

    if convention == "total":
        wet, direct_gain, calibration = dsp.split_known_direct(h, direct_index, direct_length)
        removal = {
            "method": "declared_impulse" if direct_length == 1 else "declared_window_energy",
            "direct_index_frames": int(direct_index),
            "window_frames": int(direct_length),
            "direct_gain": direct_gain,
            "validated": direct_length == 1,
        }
        status = "validated_dsp" if direct_length == 1 else "experimental"
        if direct_length > 1:
            reasons.append("GAIN_UNCALIBRATED")
    elif convention == "wet_unit_direct":
        if not np.all(np.isfinite(h)):
            raise MimeticError("INVALID_AUDIO", "IR non finie")
        wet = h.copy()
        removal = {"method": "none_declared_wet", "window_frames": None, "validated": True}
        calibration = "declared_unit_direct"
        status = "validated_dsp"
    else:
        raise MimeticError("INVALID_PARAMETER", f"convention inconnue : {convention}")

    return RoomProfile(
        profile_id=uuid.uuid4().hex,
        engine_id=ENGINE_ID,
        status=status,
        wet_ir=wet,
        sample_rate_hz=sample_rate_hz,
        time_origin="declared_direct_arrival",
        direct_removal=removal,
        gain_calibration=calibration,
        drr_db=dsp.drr_db_of_wet(wet),
        usable_band_hz=[0, sample_rate_hz / 2],
        tail_horizon_seconds=wet.size / sample_rate_hz,
        quality_reasons=reasons,
        parameters={"convention": convention},
        provenance={"adapter_version": ADAPTER_VERSION, "dsp_version": DSP_VERSION,
                    "source_name": source_name, "source_sha256": source_sha256},
    )

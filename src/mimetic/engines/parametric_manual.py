"""Backend `parametric_manual` : reverb construite à partir de paramètres explicites (architecture §6.7).

Mode manuel clairement identifié : aucun paramètre n'est issu de la référence.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass

import numpy as np
from scipy import signal as sps

from mimetic import DSP_VERSION
from mimetic.audio import dsp
from mimetic.domain.errors import MimeticError
from mimetic.domain.models import MAX_IR_SECONDS, RoomProfile

ENGINE_ID = "parametric_manual"
ADAPTER_VERSION = "0.1.0"
CROSSOVERS_HZ = (500.0, 4000.0)
EARLY_COUNT = 8
EARLY_SPREAD_S = 0.040
LATE_ONSET_S = 0.020


@dataclass(frozen=True)
class ManualParams:
    rt60_s: float = 0.6
    bass_ratio: float = 1.0     # RT60 bande basse (< 500 Hz) = rt60 × bass_ratio
    treble_ratio: float = 0.7   # RT60 bande haute (> 4 kHz) = rt60 × treble_ratio
    drr_db: float = 6.0         # direct unitaire / énergie totale du wet
    first_reflection_ms: float = 8.0
    seed: int = 2026
    sample_rate_hz: int = 48000

    def validate(self) -> None:
        checks = [
            (0.05 <= self.rt60_s <= 8.0, "RT60 hors [0,05 ; 8] s"),
            (0.25 <= self.bass_ratio <= 4.0, "ratio basses hors [0,25 ; 4]"),
            (0.1 <= self.treble_ratio <= 2.0, "ratio aigus hors [0,1 ; 2]"),
            (-30.0 <= self.drr_db <= 40.0, "DRR hors [-30 ; 40] dB"),
            (0.0 <= self.first_reflection_ms <= 150.0, "première réflexion hors [0 ; 150] ms"),
            (self.sample_rate_hz in (44100, 48000), "fréquence non prise en charge"),
        ]
        for ok, msg in checks:
            if not ok:
                raise MimeticError("INVALID_PARAMETER", msg)


def synthesize(p: ManualParams) -> tuple[np.ndarray, list[str], dict]:
    """Construit un wet relatif à un direct unitaire. Déterministe pour une graine donnée."""
    p.validate()
    fs = p.sample_rate_hz
    rng = np.random.default_rng(p.seed)
    t_first = p.first_reflection_ms / 1000.0
    rts = [p.rt60_s * p.bass_ratio, p.rt60_s, p.rt60_s * p.treble_ratio]
    wanted = t_first + max(rts)  # enveloppe à -60 dB en fin de plus longue bande
    reasons: list[str] = []
    length_s = wanted
    if wanted > MAX_IR_SECONDS:
        length_s = MAX_IR_SECONDS
        reasons.append("TAIL_TRUNCATED")
    n = int(round(length_s * fs)) + 1
    t = np.arange(n) / fs

    # Queue diffuse par bandes : bruit filtré × exp(-ln(1000)·τ/RT60_b), τ depuis la première réflexion.
    tau = np.maximum(t - t_first, 0.0)
    onset = np.clip(tau / LATE_ONSET_S, 0.0, 1.0) * (t >= t_first)
    lo, hi = CROSSOVERS_HZ
    filters = [
        sps.butter(4, lo, "lowpass", fs=fs, output="sos"),
        sps.butter(4, [lo, hi], "bandpass", fs=fs, output="sos"),
        sps.butter(4, hi, "highpass", fs=fs, output="sos"),
    ]
    late = np.zeros(n)
    for sos, rt in zip(filters, rts):
        noise = sps.sosfiltfilt(sos, rng.standard_normal(n))
        noise /= np.sqrt(np.mean(noise**2)) + 1e-12  # même énergie de départ par bande
        late += noise * np.exp(-np.log(1000.0) * tau / rt)
    late *= onset

    # Premières réflexions éparses, construites séparément.
    early = np.zeros(n)
    times = np.sort(t_first + rng.uniform(0.0, EARLY_SPREAD_S, EARLY_COUNT))
    times[0] = t_first
    for tr in times:
        k = int(round(tr * fs))
        if k < n:
            env = np.exp(-np.log(1000.0) * (tr - t_first) / p.rt60_s)
            early[k] += rng.choice([-1.0, 1.0]) * env * 3.0

    raw = early + late
    wet = dsp.calibrate_to_drr(raw, p.drr_db)
    wet[: int(round(t_first * fs))] = 0.0  # zéros acoustiques préservés jusqu'à la première réflexion

    bands = [(None, lo), (lo, hi), (hi, None)]
    measured = dsp.band_rt60(wet, fs, bands)
    diagnostics = {
        "requested_rt60_s_by_band": rts,
        "measured_t20_rt60_s_by_band": measured,
        "bands_hz": [[b[0], b[1]] for b in bands],
        "length_s": n / fs,
    }
    return wet, reasons, diagnostics


def build_profile(p: ManualParams) -> RoomProfile:
    wet, reasons, diagnostics = synthesize(p)
    return RoomProfile(
        profile_id=uuid.uuid4().hex,
        engine_id=ENGINE_ID,
        status="manual",
        wet_ir=wet,
        sample_rate_hz=p.sample_rate_hz,
        time_origin="synthetic_unit_direct",
        direct_removal={"method": "not_applicable_synthetic", "window_frames": None, "validated": True},
        gain_calibration="manual",
        drr_db=p.drr_db,
        usable_band_hz=[0, p.sample_rate_hz / 2],
        tail_horizon_seconds=wet.size / p.sample_rate_hz,
        quality_reasons=reasons,
        parameters={**asdict(p), "diagnostics": diagnostics, "note": "paramètres manuels, non issus de la référence"},
        provenance={"adapter_version": ADAPTER_VERSION, "dsp_version": DSP_VERSION},
        seed=p.seed,
    )

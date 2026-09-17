"""Extension hybride des aigus d'une IR wet à bande limitée (architecture §4.4, étape 4).

Au-dessous de CROSSOVER_HZ : l'IR estimée, convertie à la fréquence de rendu, inchangée.
Au-dessus : bandes synthétisées, jamais présentées comme estimées. Pour chaque bande :

    hf_b(t) = bruit filtré_b(t) · enveloppe_ref(t) · exp(−ln(1000)·t·(1/RT_b − 1/RT_ref)) · g_b

- enveloppe_ref : enveloppe (Hilbert, lissée 1 ms) de la bande de référence 5–7 kHz de l'IR estimée,
  qui porte la structure temporelle des réflexions (position et densité) ;
- RT_b : taux de décroissance 1/RT = a + b·f² ajusté (b ≥ 0) sur les RT60 des bandes 1–7 kHz : forme de
  l'absorption de l'air, qui domine la perte des aigus. RT_b borné à [RT_MIN_S ; RT_ref] ;
- g_b : niveau précoce (0–50 ms) prolongé linéairement en fréquence (dB/kHz), pente bornée à [−4 ; 0] dB/kHz.

hybrid-hf-v1 (pentes linéaires en octaves) donnait des aigus trop brillants (+5 à +10 dB) et trop longs
(×1,3 à ×2,8) sur le pilote pleine bande : voir docs/model-evaluation.md.
Les bornes empêchent une extrapolation plus brillante ou plus longue que la bande estimée la plus haute.
Déterministe pour une graine donnée.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import signal as sps

from mimetic.audio import dsp

METHOD = "hybrid-hf-v2"
CROSSOVER_HZ = 7800.0
REF_BAND = (5000.0, 7000.0)
ANALYSIS_BANDS = [(1000.0, 2000.0), (2000.0, 3500.0), (3500.0, 5000.0), REF_BAND]
EARLY_S = 0.050
RT_MIN_S = 0.03
SLOPE_BOUNDS_DB_KHZ = (-4.0, 0.0)


def _band(x, fs, lo, hi, order=4):
    if hi is None or hi >= 0.49 * fs:
        sos = sps.butter(order, lo, "highpass", fs=fs, output="sos")
    else:
        sos = sps.butter(order, [lo, hi], "bandpass", fs=fs, output="sos")
    return sps.sosfiltfilt(sos, x)


def _rt(x, fs):
    rt = dsp.schroeder_rt(x, fs, -5.0, -25.0)
    return rt if rt is not None else dsp.schroeder_rt(x, fs, -5.0, -15.0)


def _early_density_db(x, fs, lo, hi, onset):
    seg = x[onset:onset + int(EARLY_S * fs)]
    e = float(np.sum(seg ** 2)) / (hi - lo)
    return None if e <= 0 else 10 * math.log10(e)


def hf_bands(fs: int) -> list[tuple[float, float]]:
    top = min(20000.0, 0.45 * fs)
    edges = [CROSSOVER_HZ, 11300.0, 16000.0, top]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1) if edges[i] < top]


def fit(wet_native: np.ndarray, fs_native: int) -> dict:
    """Mesures sur l'IR estimée (à sa fréquence native) et prolongement vers les aigus."""
    w = np.asarray(wet_native, dtype=np.float64)
    nz = np.flatnonzero(w)
    onset = int(nz[0]) if nz.size else 0
    rows = []
    for lo, hi in ANALYSIS_BANDS:
        b = _band(w, fs_native, lo, hi)
        rows.append({"band_hz": [lo, hi], "fc": math.sqrt(lo * hi), "rt60_s": _rt(b, fs_native),
                     "early_density_db": _early_density_db(b, fs_native, lo, hi, onset)})
    ref = rows[-1]
    rt_ref = ref["rt60_s"]
    # Taux de décroissance 1/RT = a + b·(f/1 kHz)², moindres carrés avec b ≥ 0.
    pts = [((r["fc"] / 1000.0) ** 2, 1.0 / r["rt60_s"]) for r in rows if r["rt60_s"]]
    a, b = (1.0 / rt_ref if rt_ref else 0.0), 0.0
    if len(pts) >= 2:
        x2, y = np.array(pts).T
        b, a = np.polyfit(x2, y, 1)
        if b < 0:
            b, a = 0.0, float(np.mean(y))
    lvl = [(r["fc"] / 1000.0, r["early_density_db"]) for r in rows if r["early_density_db"] is not None]
    lvl_slope = float(np.polyfit(*zip(*lvl), 1)[0]) if len(lvl) >= 2 else 0.0
    lvl_slope_used = min(max(lvl_slope, SLOPE_BOUNDS_DB_KHZ[0]), SLOPE_BOUNDS_DB_KHZ[1])
    return {
        "method": METHOD,
        "analysis": rows,
        "rt_ref_s": rt_ref,
        "decay_rate_fit": {"a_per_s": float(a), "b_per_s_per_khz2": float(b)},
        "level_slope_db_khz_measured": lvl_slope,
        "level_slope_db_khz_used": lvl_slope_used,
        "ref_band_hz": list(REF_BAND),
        "onset_frames_native": onset,
    }


def band_rt(params: dict, fc: float) -> float:
    fit_ = params["decay_rate_fit"]
    rate = fit_["a_per_s"] + fit_["b_per_s_per_khz2"] * (fc / 1000.0) ** 2
    rt = 1.0 / rate if rate > 0 else params["rt_ref_s"]
    return min(max(rt, RT_MIN_S), params["rt_ref_s"])


def extend(wet_native: np.ndarray, fs_native: int, fs_out: int, params: dict, seed: int) -> tuple[np.ndarray, dict]:
    """IR wet à fs_out : bande estimée sous CROSSOVER_HZ + bandes synthétisées au-dessus."""
    base = dsp.resample_ir(wet_native, fs_native, fs_out)
    bands = hf_bands(fs_out)
    info = {"method": METHOD, "estimated_band_hz": [0.0, CROSSOVER_HZ], "synthesized_bands": [], "seed": seed}
    rt_ref = params.get("rt_ref_s")
    ref_row = params["analysis"][-1]
    if not bands or rt_ref is None or ref_row["early_density_db"] is None or not np.any(base):
        info["skipped"] = "mesures de la bande de référence indisponibles"
        return base, info

    low = sps.sosfiltfilt(sps.butter(8, CROSSOVER_HZ, "lowpass", fs=fs_out, output="sos"), base)
    ref = _band(base, fs_out, *REF_BAND)
    env = np.abs(sps.hilbert(ref))
    k = max(1, int(0.001 * fs_out))
    env = np.convolve(env, np.ones(k) / k, mode="same")
    onset = int(round(params["onset_frames_native"] * fs_out / fs_native))
    gate = (np.arange(base.size) >= onset).astype(np.float64)
    t = np.maximum(np.arange(base.size) - onset, 0) / fs_out

    # Niveau précoce de la bande de référence, mesuré à fs_out sur le même signal que l'enveloppe.
    ref_density_db = _early_density_db(ref, fs_out, *REF_BAND, onset)
    rng = np.random.default_rng(seed)
    fc_ref = math.sqrt(REF_BAND[0] * REF_BAND[1])
    hf = np.zeros(base.size)
    for lo, hi in bands:
        fc = math.sqrt(lo * hi)
        rt_b = band_rt(params, fc)
        carrier = _band(rng.standard_normal(base.size), fs_out, lo, hi)
        carrier /= math.sqrt(np.mean(carrier ** 2)) + 1e-12
        x = carrier * env * np.exp(-math.log(1000.0) * t * (1.0 / rt_b - 1.0 / rt_ref)) * gate
        x = _band(x, fs_out, lo, hi)  # confine la modulation à la bande
        target_db = ref_density_db + params["level_slope_db_khz_used"] * (fc - fc_ref) / 1000.0
        got_db = _early_density_db(x, fs_out, lo, hi, onset)
        if got_db is None:
            continue
        x *= 10 ** ((target_db - got_db) / 20)
        hf += x
        info["synthesized_bands"].append({"band_hz": [lo, hi], "rt60_s": rt_b,
                                          "early_density_db_rel_ref": target_db - ref_density_db})
    hf *= gate
    info["synthesized_band_hz"] = [CROSSOVER_HZ, bands[-1][1]] if info["synthesized_bands"] else None
    return low + hf, info

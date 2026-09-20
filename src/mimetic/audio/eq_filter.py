"""Conception et application du filtre de raccord EQ (architecture-match-eq §6.4, §7).

Une courbe de gain en dB sur une grille logarithmique devient un FIR causal à phase minimale
approximée, construit par cepstre réel :

    L = g·ln(10)/20  →  c = irfft(L)  →  cepstre causal  →  q = irfft(exp(rfft(c_causal)))

`scipy.signal.minimum_phase` n'est pas utilisé : en 1.13.1 il renvoie une magnitude approximativement
égale à la racine carrée de la magnitude d'entrée, ce qui fausserait les gains.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np
from scipy import signal as sps

from mimetic.domain.errors import MimeticError

DESIGN_VERSION = "eq-fir-v1"
DESIGN_FFT = 32768
TAPS_LADDER = (2048, 4096, 8192)
FADE_TAPS = 128
FIT_TOLERANCE_DB = 0.25
# Retours continus vers 0 dB hors de la zone de correction utile.
EDGE_LOW_HZ = (40.0, 80.0)
EDGE_HIGH_HZ = (12000.0, 16000.0)
LEVEL_GAIN_LIMIT_DB = 6.0


def curve_at(freqs_hz: np.ndarray, gains_db: np.ndarray, at_hz: np.ndarray, fs: int) -> np.ndarray:
    """Courbe interpolée en log-fréquence **avec** ses transitions vers 0 dB : c'est la cible réelle.

    Utilisée pour la conception et pour la vérification, afin de ne pas mesurer l'erreur du filtre
    contre une courbe qu'on n'a jamais demandée aux extrémités.
    """
    f = np.asarray(freqs_hz, dtype=np.float64)
    g = np.asarray(gains_db, dtype=np.float64)
    if f.size != g.size or f.size < 2:
        raise MimeticError("INVALID_PARAMETER", "courbe EQ invalide")
    if not np.all(np.isfinite(g)) or not np.all(np.isfinite(f)):
        raise MimeticError("INVALID_PARAMETER", "courbe EQ non finie")
    dense_f = np.asarray(at_hz, dtype=np.float64)
    lo, hi = float(f[0]), float(f[-1])
    safe = np.clip(dense_f, lo, hi)
    dense = np.interp(np.log(safe), np.log(f), g)

    # Transitions douces (cosinus en log-fréquence) vers 0 dB.
    def ramp(x, a, b):
        t = np.clip((np.log(np.maximum(x, 1e-9)) - math.log(a)) / (math.log(b) - math.log(a)), 0.0, 1.0)
        return 0.5 - 0.5 * np.cos(math.pi * t)

    w = np.ones_like(dense_f)
    w *= ramp(dense_f, EDGE_LOW_HZ[0], EDGE_LOW_HZ[1])
    top_lo, top_hi = EDGE_HIGH_HZ[0], min(EDGE_HIGH_HZ[1], fs / 2)
    if top_hi > top_lo:
        w *= 1.0 - ramp(dense_f, top_lo, top_hi)
    w[dense_f <= EDGE_LOW_HZ[0]] = 0.0
    w[dense_f >= top_hi] = 0.0
    return dense * w


def curve_to_dense_db(freqs_hz, gains_db, fs: int, n_fft: int) -> np.ndarray:
    return curve_at(freqs_hz, gains_db, np.fft.rfftfreq(n_fft, 1.0 / fs), fs)


def design_fir(freqs_hz, gains_db, fs: int, amount: float = 1.0) -> dict:
    """Retourne {coefficients, fit_error_db, taps, ...}. Courbe nulle → [1.0] exact."""
    if not 0.0 <= amount <= 1.0:
        raise MimeticError("INVALID_PARAMETER", "intensité EQ hors [0, 1]")
    g = np.asarray(gains_db, dtype=np.float64) * amount
    if amount == 0.0 or not np.any(np.abs(g) > 1e-9):
        return {"coefficients": np.array([1.0]), "fit_error_db": 0.0, "taps": 1,
                "design_version": DESIGN_VERSION, "amount": amount, "identity": True}

    dense_db = curve_to_dense_db(freqs_hz, g, fs, DESIGN_FFT)
    log_mag = dense_db * math.log(10.0) / 20.0
    cep = np.fft.irfft(log_mag, n=DESIGN_FFT)
    causal = np.zeros_like(cep)
    causal[0] = cep[0]
    causal[DESIGN_FFT // 2] = cep[DESIGN_FFT // 2]
    causal[1:DESIGN_FFT // 2] = 2.0 * cep[1:DESIGN_FFT // 2]
    q_full = np.fft.irfft(np.exp(np.fft.rfft(causal)), n=DESIGN_FFT)

    target_f = np.geomspace(max(20.0, EDGE_LOW_HZ[0]), min(EDGE_HIGH_HZ[1], 0.47 * fs), 400)
    target_db = curve_at(freqs_hz, g, target_f, fs)
    best = None
    for taps in TAPS_LADDER:
        q = q_full[:taps].copy()
        fade = min(FADE_TAPS, taps // 8)
        q[-fade:] *= 0.5 + 0.5 * np.cos(np.pi * (np.arange(fade) + 1) / (fade + 1))
        w, h = sps.freqz(q, worN=target_f, fs=fs)
        got_db = 20 * np.log10(np.maximum(np.abs(h), 1e-12))
        err = float(np.max(np.abs(got_db - target_db)))
        best = {"coefficients": q, "fit_error_db": err, "taps": taps}
        if err <= FIT_TOLERANCE_DB:
            break
    if best is None or not np.all(np.isfinite(best["coefficients"])):
        raise MimeticError("EQ_FILTER_FIT_FAILED", "conception du filtre impossible")
    if best["fit_error_db"] > FIT_TOLERANCE_DB * 4:
        raise MimeticError("EQ_FILTER_FIT_FAILED", f"erreur {best['fit_error_db']:.2f} dB")
    return {**best, "design_version": DESIGN_VERSION, "amount": amount, "identity": False,
            "coefficients_sha256": sha256_array(best["coefficients"])}


def sha256_array(a: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(a, dtype=np.float64).tobytes()).hexdigest()


def apply_filter(x: np.ndarray, coefficients: np.ndarray, mask: np.ndarray | None = None,
                 preserve_level: bool = True) -> tuple[np.ndarray, float]:
    """x filtré (longueur N+L-1) et gain commun appliqué, en dB.

    Le gain compense le niveau RMS de parole (mesuré sur `mask` si fourni), borné à ±6 dB.
    Ce n'est ni un matching LUFS ni une copie du niveau de la production.
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    q = np.asarray(coefficients, dtype=np.float64).ravel()
    if q.size == 1 and q[0] == 1.0:
        return x.copy(), 0.0
    y = sps.oaconvolve(x, q, mode="full")
    gain_db = 0.0
    if preserve_level:
        sel = np.ones(x.size, dtype=bool) if mask is None else np.asarray(mask, dtype=bool)[: x.size]
        e_in = float(np.sum(x[sel] ** 2))
        e_out = float(np.sum(y[: x.size][sel] ** 2))
        if e_in > 0 and e_out > 0:
            gain_db = float(np.clip(10 * np.log10(e_in / e_out), -LEVEL_GAIN_LIMIT_DB, LEVEL_GAIN_LIMIT_DB))
            y *= 10 ** (gain_db / 20.0)
    return y, gain_db

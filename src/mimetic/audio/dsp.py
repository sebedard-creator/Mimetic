"""DSP pur : séparation direct/wet connue, calibration, conversion d'IR, convolution (architecture §6.4–6.8).

Aucune dépendance à l'UI ni à un modèle de recherche.
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np
from scipy import signal as sps

from mimetic.domain.errors import MimeticError
from mimetic.domain.models import MAX_IR_SECONDS, RenderSettings

MIN_DIRECT_GAIN = 1e-4
# Budget mémoire V1 : ~4 tableaux float64 de longueur N+M-1.
MAX_RENDER_BYTES = 2 * 1024**3


def split_known_direct(h_total: np.ndarray, direct_index: int, direct_length: int = 1) -> tuple[np.ndarray, float, str]:
    """Sépare un direct dont la position est connue.

    direct_length == 1 : direct impulsionnel `a·delta[n-d]` → h_wet = r_aligned / a (signe conservé).
    direct_length > 1  : paquet direct étalé → normalisation par sqrt(E_direct), calibration seulement équivalente.

    Retourne (h_wet aligné sur n=0, gain direct, méthode de calibration).
    """
    h = np.asarray(h_total, dtype=np.float64).ravel()
    if h.size == 0 or not np.all(np.isfinite(h)):
        raise MimeticError("INVALID_AUDIO", "IR vide ou non finie")
    if not (0 <= direct_index < h.size):
        raise MimeticError("INVALID_PARAMETER", f"indice direct {direct_index} hors IR")
    if direct_length < 1 or direct_index + direct_length > h.size:
        raise MimeticError("INVALID_PARAMETER", "fenêtre directe hors IR")

    aligned = h[direct_index:].copy()  # retire le temps de propagation absolu
    if direct_length == 1:
        a = float(aligned[0])
        if abs(a) < MIN_DIRECT_GAIN:
            raise MimeticError("DIRECT_PATH_UNRESOLVED", f"gain direct trop faible ({a:.2e})")
        aligned[0] = 0.0
        return aligned / a, a, "exact_impulse"

    e_direct = float(np.sum(aligned[:direct_length] ** 2))
    if e_direct < MIN_DIRECT_GAIN**2:
        raise MimeticError("DIRECT_PATH_UNRESOLVED", "énergie du paquet direct trop faible")
    aligned[:direct_length] = 0.0
    return aligned / np.sqrt(e_direct), float(np.sqrt(e_direct)), "energy_equivalent"


def calibrate_to_drr(raw_wet: np.ndarray, drr_db: float) -> np.ndarray:
    """Échelle d'un wet brut pour obtenir DRR = 10·log10(1 / E_wet) face à un direct unitaire."""
    raw = np.asarray(raw_wet, dtype=np.float64)
    e = float(np.sum(raw**2))
    if e <= 0.0:
        return raw.copy()
    rho = 10.0 ** (-drr_db / 10.0)
    return raw * np.sqrt(rho / e)


def drr_db_of_wet(h_wet: np.ndarray) -> float | None:
    """DRR d'un wet relatif à un direct unitaire (fenêtres : direct = 1 échantillon, reste = wet entier)."""
    e = float(np.sum(np.asarray(h_wet, dtype=np.float64) ** 2))
    return None if e <= 0.0 else float(10.0 * np.log10(1.0 / e))


def resample_ir(h: np.ndarray, fs_src: int, fs_dst: int) -> np.ndarray:
    """Convertit une IR (filtre) de fs_src vers fs_dst en préservant la réponse de transfert dans la bande commune.

    resample_poly conserve l'amplitude des formes d'onde et compense son propre délai (filtre à phase
    linéaire centré) ; les coefficients sont donc multipliés une seule fois par fs_src / fs_dst.
    """
    h = np.asarray(h, dtype=np.float64)
    if fs_src == fs_dst:
        return h.copy()
    ratio = Fraction(fs_dst, fs_src).limit_denominator(10000)
    out = sps.resample_poly(h, ratio.numerator, ratio.denominator)
    target_len = int(round(h.size * fs_dst / fs_src))
    out = out[:target_len]
    return out * (fs_src / fs_dst)


def apply_predelay(h_wet: np.ndarray, seconds: float, fs: int) -> np.ndarray:
    if not (0.0 <= seconds <= 0.2):
        raise MimeticError("INVALID_PARAMETER", "délai supplémentaire hors [0, 200] ms")
    n = int(round(seconds * fs))
    return np.concatenate([np.zeros(n), np.asarray(h_wet, dtype=np.float64)])


def check_render_budget(n: int, m: int) -> None:
    if 4 * 8 * (n + m - 1) > MAX_RENDER_BYTES:
        raise MimeticError("OUT_OF_MEMORY", f"{n + m - 1} échantillons dépassent le budget de rendu")


def render_wet(x: np.ndarray, h_wet: np.ndarray, fs: int, settings: RenderSettings) -> tuple[np.ndarray, np.ndarray]:
    """w = conv(x, h_wet ⊕ prédélai) · 10^(g/20), mode full. Retourne (dry paddé, wet), longueur N+M-1."""
    x = np.asarray(x, dtype=np.float64).ravel()
    if x.ndim != 1 or x.size == 0:
        raise MimeticError("INVALID_AUDIO", "ADR vide")
    if not np.isfinite(settings.wet_gain_db) or not (-60.0 <= settings.wet_gain_db <= 24.0):
        raise MimeticError("INVALID_PARAMETER", "niveau wet hors [-60, +24] dB")
    h = apply_predelay(h_wet, settings.additional_predelay_seconds, fs)
    if h.size == 0:
        h = np.zeros(1)
    if h.size > int(MAX_IR_SECONDS * fs) + int(0.2 * fs) + 1:
        raise MimeticError("LIMIT_EXCEEDED", "IR de rendu trop longue")
    n, m = x.size, h.size
    check_render_budget(n, m)

    wet = np.zeros(n + m - 1)
    if np.any(h) and np.any(x):
        # Zéros initiaux retirés puis replacés à leur indice : la convolution FFT laisserait sinon
        # un bruit numérique (~1e-17) là où l'acoustique impose des zéros exacts.
        hx, hh = int(np.argmax(x != 0)), int(np.argmax(h != 0))
        core = sps.oaconvolve(x[hx:], h[hh:], mode="full")
        wet[hx + hh:hx + hh + core.size] = core * (10.0 ** (settings.wet_gain_db / 20.0))
    dry = np.zeros(n + m - 1)
    dry[:n] = x  # branche dry exactement x, aucun gain
    assert wet.size == n + m - 1
    return dry, wet


def schroeder_rt(h: np.ndarray, fs: int, lo_db: float = -5.0, hi_db: float = -25.0) -> float | None:
    """Estimation RT60 par pente de la courbe de Schroeder entre lo_db et hi_db (T20 par défaut).

    Destiné à vérifier une IR synthétique sans bruit ; retourne None si la dynamique est insuffisante.
    """
    e = np.asarray(h, dtype=np.float64) ** 2
    total = e.sum()
    if total <= 0:
        return None
    edc = np.cumsum(e[::-1])[::-1] / total
    edc_db = 10 * np.log10(np.maximum(edc, 1e-30))
    idx = np.where((edc_db <= lo_db) & (edc_db >= hi_db))[0]
    if idx.size < 10 or edc_db[-1] > hi_db:
        return None
    t = idx / fs
    slope, _ = np.polyfit(t, edc_db[idx], 1)
    if slope >= 0:
        return None
    return float(-60.0 / slope)


def band_rt60(h: np.ndarray, fs: int, bands: list[tuple[float | None, float | None]]) -> list[float | None]:
    out = []
    for lo, hi in bands:
        if lo is None:
            sos = sps.butter(4, hi, "lowpass", fs=fs, output="sos")
        elif hi is None:
            sos = sps.butter(4, lo, "highpass", fs=fs, output="sos")
        else:
            sos = sps.butter(4, [lo, hi], "bandpass", fs=fs, output="sos")
        out.append(schroeder_rt(sps.sosfilt(sos, h), fs))
    return out

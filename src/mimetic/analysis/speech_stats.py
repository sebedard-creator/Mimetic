"""Statistiques spectrales de parole pour le raccord EQ (architecture-match-eq §5).

Détection d'activité locale (énergie + planéité spectrale), spectre moyen en puissance par bandes
de 1/12 d'octave, soustraction du bruit estimé sur les pauses, agrégation robuste par blocs.

Pas de VAD neuronal en V1 : le matériel est du dialogue. Le détecteur est déclaré comme un indice
d'activité, jamais comme une détection de parole validée (`method: "energy_flatness_v1"`).
Aucun de ces calculs ne touche l'audio exporté : ce sont des statistiques.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import signal as sps

STATS_VERSION = "speech-ltas-v1"
FRAME_S = 0.020
HOP_S = 0.010
BLOCK_S = 0.5
BAND_LOW_HZ = 40.0
BAND_HIGH_HZ = 16000.0
BANDS_PER_OCTAVE = 12
ANCHOR_HZ = (200.0, 5000.0)
# Séparation grossière voyelles / consonnes sourdes, par pente spectrale. Le mélange de phonèmes
# diffère entre deux prises : comparer chaque classe séparément évite de prendre un « s » de plus
# pour une différence de micro. Ce n'est pas une classification phonétique validée.
TILT_SPLIT_DB = -10.0
MIN_CLASS_FRAMES = 15
# Trois catégories temporelles, au lieu de « actif / inactif » : une queue de réverbération n'est
# pas du bruit de fond, et la confondre avec lui fausse tout le rapport signal/bruit.
GUARD_BEFORE_MS = 100.0      # avant la parole : respiration, attaque, amorce
GUARD_AFTER_MS = 250.0       # après la parole : queue de pièce, prolongée tant que ça décroît
GUARD_MAX_MS = 600.0         # au-delà, on cesse de suivre la décroissance : du dialogue continu
                             # finirait sinon sans aucune trame de fond exploitable
MIN_NOISE_FRAMES = 10        # sous ce nombre de trames de fond seul : bruit déclaré inconnu
NOISE_NEGLIGIBLE_DB = -45.0  # fond à plus de 45 dB sous la parole : traité comme négligeable
ACTIVE_RANGE_DB = 35.0       # trames actives : à moins de 35 dB de la plus forte
SNR_GATE_DB = 6.0            # et au moins 6 dB au-dessus du plancher du fichier
SNR_FULL_DB = 18.0           # poids plein à partir de 18 dB
# Une réplique d'ADR isolée contient souvent moins de 1 s de parole : refuser à 2 s écartait
# des cas courants. En dessous de 3 s, la correction passe en confiance réduite (bornes serrées).
MIN_ACTIVE_S = 0.8


def band_edges(fs: int) -> tuple[np.ndarray, np.ndarray]:
    top = min(BAND_HIGH_HZ, 0.45 * fs)
    n = int(math.floor(BANDS_PER_OCTAVE * math.log2(top / BAND_LOW_HZ)))
    centers = BAND_LOW_HZ * 2 ** (np.arange(n + 1) / BANDS_PER_OCTAVE)
    ratio = 2 ** (1 / (2 * BANDS_PER_OCTAVE))
    return centers, np.stack([centers / ratio, centers * ratio], axis=1)


def _stft(x: np.ndarray, fs: int):
    n_fft = 4096
    win_len = max(64, int(round(FRAME_S * fs)))
    hop = max(1, int(round(HOP_S * fs)))
    f, t, Z = sps.stft(x, fs=fs, window=sps.get_window("hann", win_len), nperseg=win_len,
                       noverlap=win_len - hop, nfft=n_fft, boundary=None, padded=False,
                       scaling="spectrum")
    return f, t, (np.abs(Z) ** 2)


def activity(power: np.ndarray, freqs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Trames actives et poids de fiabilité.

    Critère : énergie 100 Hz–6 kHz à moins de 30 dB de la plus forte trame **et** au moins 6 dB
    au-dessus du plancher du fichier (10e centile). Le poids monte avec ce rapport jusqu'à 18 dB.
    C'est un indice d'activité, pas une détection de parole validée.
    """
    band = (freqs >= 100) & (freqs <= 6000)
    energy = power[band].sum(axis=0)
    if np.count_nonzero(energy) < 5:
        return np.zeros(power.shape[1], dtype=bool), np.zeros(power.shape[1])
    peak = float(np.percentile(energy[energy > 0], 95))
    # Plancher estimé sur toutes les trames, silences compris : sur un fichier sans souffle,
    # le calculer sur les seules trames non nulles prendrait la voix pour du bruit.
    floor = max(float(np.percentile(energy, 10)), peak * 1e-9)
    snr_db = 10 * np.log10(np.maximum(energy, 1e-30) / floor)
    voiced = (energy >= peak * 10 ** (-ACTIVE_RANGE_DB / 10)) & (snr_db >= SNR_GATE_DB)
    weight = np.clip((snr_db - SNR_GATE_DB) / (SNR_FULL_DB - SNR_GATE_DB), 0.0, 1.0) * voiced
    return voiced, weight


def _to_bands(power_frames: np.ndarray, freqs: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Puissance par bande : somme des bins, normalisée par le nombre de bins (noyaux identiques
    des deux côtés de la comparaison)."""
    out = np.zeros((edges.shape[0], power_frames.shape[1]))
    for b, (lo, hi) in enumerate(edges):
        sel = (freqs >= lo) & (freqs < hi)
        if not np.any(sel):  # bande plus étroite que la résolution FFT : bin le plus proche
            sel = np.zeros_like(freqs, dtype=bool)
            sel[np.argmin(np.abs(freqs - math.sqrt(lo * hi)))] = True
        out[b] = power_frames[sel].mean(axis=0)
    return out


def measure(x: np.ndarray, fs: int, label: str = "") -> dict:
    """Spectre de parole robuste par bandes, bruit estimé sur les pauses, poids et diagnostics."""
    x = np.asarray(x, dtype=np.float64).ravel()
    centers, edges = band_edges(fs)
    freqs, times, power = _stft(x, fs)
    voiced, weight = activity(power, freqs)
    active_seconds = float(voiced.sum() * HOP_S)
    bands = _to_bands(power, freqs, edges)

    # Fond seul : trames éloignées de la parole **et de sa décroissance**. Une queue de pièce prise
    # pour du bruit ferait croire à un plateau bruyant et fausserait tous les rapports signal/bruit.
    noise_only = _noise_only_mask(bands, voiced)
    noise = noise_variability_db = None
    active_total = float(bands[:, voiced].sum(axis=0).mean()) if np.any(voiced) else 0.0
    quiet_total = float(np.percentile(bands.sum(axis=0), 5))
    # « Pas de fond mesurable » et « fond négligeable » sont deux situations opposées : un ADR de
    # studio en silence numérique n'est pas un enregistrement dont on ignore le bruit.
    negligible = bool(active_total > 0 and quiet_total <= active_total * 10 ** (NOISE_NEGLIGIBLE_DB / 10))
    if noise_only.sum() >= MIN_NOISE_FRAMES:
        noise = np.percentile(bands[:, noise_only], 20, axis=1)
        if not negligible:
            # Un fond qui bouge (moteur qui passe, porte) n'est pas décrit par un spectre unique :
            # on mesure sa variabilité pour en tenir compte dans la confiance.
            in_db = 10 * np.log10(np.maximum(bands[:, noise_only], 1e-30))
            noise_variability_db = np.percentile(in_db, 90, axis=1) - np.percentile(in_db, 10, axis=1)
    elif negligible:
        noise = np.full(centers.size, quiet_total / max(centers.size, 1))

    classes = _class_spectra(bands, power, freqs, voiced, weight, centers, noise)

    # Agrégation par blocs de 0,5 s, moyenne pondérée dans le bloc puis moyenne tronquée entre blocs.
    per_block = max(1, int(round(BLOCK_S / HOP_S)))
    block_specs, block_weights = [], []
    for start in range(0, bands.shape[1], per_block):
        sl = slice(start, start + per_block)
        w = weight[sl]
        if w.sum() <= 0:
            continue
        spec = (bands[:, sl] * w).sum(axis=1) / w.sum()
        if noise is not None:
            spec = np.maximum(spec - noise, spec * 1e-4)  # plancher relatif : pas de création de signal
        anchor = (centers >= ANCHOR_HZ[0]) & (centers <= ANCHOR_HZ[1])
        e = float(spec[anchor].sum())
        if e <= 0:
            continue
        block_specs.append(spec / e)  # normalisation de forme, avant comparaison
        block_weights.append(float(w.sum()))
    if not block_specs:
        return {"version": STATS_VERSION, "label": label, "ok": False, "reason": "EQ_INSUFFICIENT_SPEECH",
                "active_seconds": active_seconds, "centers_hz": centers}

    stack = np.stack(block_specs, axis=1)
    if stack.shape[1] >= 10:
        k = max(1, int(round(0.1 * stack.shape[1])))
        ordered = np.sort(stack, axis=1)[:, k:stack.shape[1] - k]
        spectrum = ordered.mean(axis=1)
        dispersion = ordered.std(axis=1) / np.maximum(spectrum, 1e-20)
    else:
        # Dispersion mesurée quand même, avec un statut : mieux vaut peu d'information signalée
        # comme telle qu'une incertitude purement conventionnelle (audit §3.3).
        spectrum = np.median(stack, axis=1)
        dispersion = (stack.std(axis=1) / np.maximum(spectrum, 1e-20) if stack.shape[1] >= 2
                      else np.full(spectrum.shape, np.nan))
    low_support = bool(stack.shape[1] < 10)

    # Rapport signal/bruit **de la parole seule** : le fond est retiré avant le rapport, sinon on
    # mesure (parole + bruit) / bruit, qui ne descend jamais sous 0 dB (audit §3.2).
    active_raw = bands[:, voiced].mean(axis=1) if np.any(voiced) else bands.mean(axis=1)
    snr_db = None
    if noise is not None:
        speech_only = np.maximum(active_raw - noise, 0.0)
        snr_db = 10 * np.log10(np.maximum(speech_only, 1e-30) / np.maximum(noise, 1e-30))
    return {
        "version": STATS_VERSION,
        "label": label,
        "ok": active_seconds >= MIN_ACTIVE_S,
        "reason": None if active_seconds >= MIN_ACTIVE_S else "EQ_INSUFFICIENT_SPEECH",
        "sample_rate_hz": fs,
        "centers_hz": centers,
        "power": spectrum,
        "power_raw": active_raw,
        "classes": classes,
        "dispersion": dispersion,
        "blocks": stack.shape[1],
        "low_support": low_support,
        "active_seconds": active_seconds,
        "noise_known": noise is not None,
        "noise_negligible": negligible,
        "noise_frames": int(noise_only.sum()),
        "noise_variability_db": noise_variability_db,
        "snr_db": snr_db,
        "snr_definition": "speech_only_over_noise",
        "voiced_mask_hop_s": HOP_S,
        "voiced": voiced,
        "method": "energy_snr_v2",
    }


def _noise_only_mask(bands: np.ndarray, voiced: np.ndarray) -> np.ndarray:
    """Trames de fond seul : ni parole, ni sa décroissance.

    La garde après la parole dure au moins GUARD_AFTER_MS, et se prolonge tant que l'énergie
    continue de baisser — c'est la signature d'une queue de réverbération, pas d'un fond stable.
    """
    before = max(1, int(GUARD_BEFORE_MS / 1000.0 / HOP_S))
    after = max(1, int(GUARD_AFTER_MS / 1000.0 / HOP_S))
    limit = max(after, int(GUARD_MAX_MS / 1000.0 / HOP_S))
    energy = bands.sum(axis=0)
    blocked = voiced.copy()
    idx = np.flatnonzero(voiced)
    if idx.size == 0:
        return np.zeros_like(voiced)
    for i in idx:
        blocked[max(0, i - before):i] = True
        end = min(len(voiced), i + after + 1)
        blocked[i:end] = True
        j = end
        while j + 1 < len(voiced) and j - i < limit and energy[j] < energy[j - 1] and not voiced[j]:
            blocked[j] = True
            j += 1
    return ~blocked


def _class_spectra(bands: np.ndarray, power: np.ndarray, freqs: np.ndarray, voiced: np.ndarray,
                   weight: np.ndarray, centers: np.ndarray, noise) -> dict:
    """Spectres moyens par classe de trame, avec leur incertitude (erreur type en dB).

    L'incertitude, et non la durée, mesure ce qu'on sait vraiment : une différence franche et
    constante reste fiable sur une réplique courte, une différence instable ne l'est pas.
    """
    low = (freqs >= 300) & (freqs <= 1000)
    high = freqs >= 3000
    with np.errstate(divide="ignore", invalid="ignore"):
        tilt = 10 * np.log10(np.maximum(power[high].sum(axis=0), 1e-30)
                             / np.maximum(power[low].sum(axis=0), 1e-30))
    out = {}
    for name, sel in (("vowel", voiced & (tilt <= TILT_SPLIT_DB)),
                      ("fricative", voiced & (tilt > TILT_SPLIT_DB))):
        n = int(sel.sum())
        if n < MIN_CLASS_FRAMES:
            continue
        frames = bands[:, sel]
        if noise is not None:
            frames = np.maximum(frames - noise[:, None], frames * 1e-4)
        anchor = (centers >= ANCHOR_HZ[0]) & (centers <= ANCHOR_HZ[1])
        energy = frames[anchor].sum(axis=0)
        keep = energy > 0
        if keep.sum() < MIN_CLASS_FRAMES:
            continue
        shaped = frames[:, keep] / energy[keep]            # forme, indépendante du niveau de trame
        db = 10 * np.log10(np.maximum(shaped, 1e-30))
        w = np.clip(weight[sel][keep], 1e-6, None)
        mean_db = np.average(db, axis=1, weights=w)
        var = np.average((db - mean_db[:, None]) ** 2, axis=1, weights=w)
        n_eff = (w.sum() ** 2) / np.sum(w ** 2)            # trames indépendantes équivalentes
        out[name] = {"mean_db": mean_db, "se_db": np.sqrt(var / max(n_eff, 1.0)), "frames": int(keep.sum())}
    return out


def voiced_sample_mask(stats: dict, n_samples: int, fs: int) -> np.ndarray:
    """Masque échantillon dérivé des trames actives, pour la compensation de niveau."""
    voiced = stats.get("voiced")
    mask = np.zeros(n_samples, dtype=bool)
    if voiced is None:
        return ~mask
    hop = max(1, int(round(HOP_S * fs)))
    for i in np.flatnonzero(voiced):
        mask[i * hop: i * hop + hop] = True
    return mask if mask.any() else ~mask

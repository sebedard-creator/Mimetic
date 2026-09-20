"""Estimation de la courbe de raccord EQ (architecture-match-eq §4, §6).

La cible n'est pas l'ADR sec mais le **mélange virtuel** `y0 = ADR * (delta + h_eff)`, c'est-à-dire le
rendu actuel de Mimetic. Pour des filtres fixes linéaires :

    (x * q) * (delta + h_eff) = q * y0

donc corriger l'ADR une seule fois suffit : la reverb suit. Comparer la référence à l'ADR **sec**
apprendrait une correction qui serait ensuite recolorée par la reverb ajoutée (double correction).
"""

from __future__ import annotations

import math

import numpy as np
from scipy import optimize, signal as sps

from mimetic import pipeline
from mimetic.analysis import speech_stats
from mimetic.audio import eq_filter
from mimetic.domain.errors import MimeticError
from mimetic.domain.models import RenderSettings, RoomProfile

MATCH_VERSION = "speech-ltas-room-aware-v1"
ANCHOR_HZ = speech_stats.ANCHOR_HZ
BOUNDS_DB = (-9.0, 6.0)
BOUNDS_DB_LOW_CONFIDENCE = (-6.0, 3.0)
# Lissage fort : sur deux textes différents, le spectre moyen porte une « fausse correction » due
# aux phonèmes. Balayage sur 16 cas (benchmarks/eq_phoneme_bias.py) : l'erreur sur une EQ connue est
# au plus bas entre 3000 et 10000 (1,5 dB contre 2,1 dB à λ=20), et la fausse correction continue de
# baisser avec λ. Au-delà de 10000, les vraies différences de micro commencent à être écrasées.
LAMBDA_SMOOTH = 5000.0
LAMBDA_ZERO_BASE = 0.05
# Retrait vers zéro piloté par la fiabilité **absolue** : g ≈ d·w/(w+λ0), λ0 = base + K·(1/c − 1).
# Confiance 1 → λ0 = 0,05 (rien n'est bridé) ; 0,5 → 0,35 ; 0,2 → 1,25 ; 0,05 → 5,8.
# Tant que ce terme dépendait des poids renormalisés, il écrasait aussi les mesures propres.
LAMBDA_ZERO_UNRELIABLE = 0.3
SNR_GATE_DB = 6.0
SNR_FULL_DB = 18.0
SE_HALF_WEIGHT_DB = 1.5       # poids divisé par deux quand l'incertitude atteint 1,5 dB
SE_LOW_CONFIDENCE_DB = 5.0    # au-delà, bornes resserrées (matériel vraiment instable)
SNR_MIN_DB = 0.0                 # départ de la rampe de confiance
MIN_CONFIDENCE = 0.05            # confiance résiduelle d'une bande très bruitée (jamais zéro net)
NOISE_UNKNOWN_CONFIDENCE = 0.6   # fond non mesurable (dialogue continu) : prudence, pas inertie
NOISE_STABLE_DB = 6.0            # écart 10–90 % du fond jugé stable
NOISE_UNSTABLE_DB = 20.0         # au-delà : fond très changeant, confiance minimale
LOW_SUPPORT_CONFIDENCE = 0.7     # moins de 10 blocs : dispersion peu documentée
LOW_CONFIDENCE_BELOW = 0.5       # confiance médiane sous ce seuil : bornes resserrées
MIN_USABLE_CONFIDENCE = 0.02     # en dessous, plus rien d'exploitable : refus explicite
HF_SYNTHETIC_HZ = 7800.0
HF_SYNTHETIC_MAX_BOOST_DB = 3.0
HF_SYNTHETIC_WEIGHT = 0.5
MAX_ANALYSIS_SECONDS = 30.0


def build_baseline(x: np.ndarray, fs: int, profile: RoomProfile | None,
                   settings: RenderSettings) -> np.ndarray:
    """y0 = ADR + sa reverb actuelle, exactement l'IR du rendu (extension HF et graine comprises)."""
    x = np.asarray(x, dtype=np.float64).ravel()
    if profile is None:
        return x.copy()
    h, _ = pipeline.profile_ir_and_info(profile, fs)
    from mimetic.audio import dsp
    dry, wet = dsp.render_wet(x, h, fs, settings)
    return dry + wet


def _trim(x: np.ndarray, fs: int) -> np.ndarray:
    n = int(MAX_ANALYSIS_SECONDS * fs)
    return x if x.size <= n else x[:n]


def resample_reference(ref: np.ndarray, fs_ref: int, fs_out: int) -> np.ndarray:
    """Conversion **forme d'onde** (sans le facteur de gain réservé aux coefficients d'IR)."""
    if fs_ref == fs_out:
        return np.asarray(ref, dtype=np.float64)
    g = math.gcd(fs_ref, fs_out)
    return sps.resample_poly(np.asarray(ref, dtype=np.float64), fs_out // g, fs_ref // g)


def estimate_curve(ref_stats: dict, base_stats: dict, *, hf_synthesized: bool,
                   use_classes: bool = False, lambda_smooth: float | None = None) -> dict:
    """Courbe de gain en dB par bande, bornée et régularisée, avec poids et diagnostics."""
    for s, who in ((ref_stats, "SOURCE"), (base_stats, "DESTINATION")):
        if not s.get("ok"):
            raise MimeticError(
                s.get("reason") or "EQ_INSUFFICIENT_SPEECH",
                f"{who} : {s.get('active_seconds', 0.0):.1f} s de parole détectée, "
                f"minimum {speech_stats.MIN_ACTIVE_S:.1f} s")
    centers = np.asarray(ref_stats["centers_hz"], dtype=np.float64)
    if centers.shape != np.asarray(base_stats["centers_hz"]).shape:
        raise MimeticError("EQ_NO_RELIABLE_BANDS", "grilles de bandes différentes")

    d_raw, se, classes_used = _difference_by_class(ref_stats, base_stats, centers, use_classes=use_classes)

    # Deux notions distinctes (audit §4.3) :
    #  - c_abs : fiabilité **absolue** de chaque bande, jamais renormalisée. Si tout est mauvais,
    #    tout reste mauvais, et la correction s'efface d'elle-même.
    #  - w : importance **relative** des bandes dans l'ajustement, remise à l'échelle.
    # Les confondre faisait qu'un extrait à 6 dB de SNR donnait exactement la même courbe qu'un
    # extrait propre, puis un refus brutal un dixième de dB plus bas.
    sides = []
    for s in (ref_stats, base_stats):
        snr = s.get("snr_db")
        if s.get("noise_negligible"):
            c = np.ones(centers.size)   # silence numérique entre les mots : rien à craindre du fond
        elif snr is None:
            c = np.full(centers.size, NOISE_UNKNOWN_CONFIDENCE)  # fond non mesurable : prudence
        else:
            # Rampe démarrant bas plutôt qu'un seuil : un extrait très bruité reçoit une petite
            # confiance, donc une correction fortement réduite, au lieu d'un refus brutal.
            c = MIN_CONFIDENCE + (1.0 - MIN_CONFIDENCE) * np.clip(
                (np.asarray(snr, dtype=np.float64) - SNR_MIN_DB) / (SNR_FULL_DB - SNR_MIN_DB), 0.0, 1.0)
        var = s.get("noise_variability_db")
        if var is not None:
            # Fond instable : son spectre moyen ne le décrit pas, la soustraction est peu fiable.
            c = c * np.clip(1.0 - (np.asarray(var, dtype=np.float64) - NOISE_STABLE_DB)
                            / (NOISE_UNSTABLE_DB - NOISE_STABLE_DB), 0.2, 1.0)
        if s.get("low_support"):
            c = c * LOW_SUPPORT_CONFIDENCE
        power = np.asarray(s["power"], dtype=np.float64)
        c = c * (power > 1e-9 * float(np.max(power)))  # bande réellement vide
        sides.append(c)
    # Le côté le plus faible commande : additionner ou multiplier punirait deux fois le même défaut.
    # L'incertitude statistique (se) reste dans les poids relatifs : grossièrement estimée, elle
    # brider ait sinon aussi les enregistrements propres.
    c_abs = np.minimum(sides[0], sides[1])
    if hf_synthesized:
        c_abs = np.where(centers >= HF_SYNTHETIC_HZ, c_abs * HF_SYNTHETIC_WEIGHT, c_abs)
    c_abs = np.clip(c_abs, 0.0, 1.0)

    w = c_abs / (1.0 + se / SE_HALF_WEIGHT_DB)
    scale = float(np.percentile(w, 90))
    if scale > 1e-6:
        w = np.clip(w / scale, 0.0, 1.0)

    # L'ancrage sert à retirer l'écart de niveau : il suffit qu'il reste des bandes utilisables,
    # même moyennes. Le refus ne doit venir que d'une absence réelle d'information.
    anchor = (centers >= ANCHOR_HZ[0]) & (centers <= ANCHOR_HZ[1]) & (w > 0.05)
    if anchor.sum() < 5 or float(np.max(c_abs)) < MIN_USABLE_CONFIDENCE:
        raise MimeticError("EQ_NO_RELIABLE_BANDS",
                           f"aucune bande exploitable (confiance max {float(np.max(c_abs)):.2f})")
    # Niveau global retiré : un écart de gain ne doit pas devenir une EQ.
    offset = float(np.average(d_raw[anchor], weights=w[anchor]))
    d = d_raw - offset

    # Bornes fixées par la fiabilité absolue et l'incertitude mesurée, jamais par la durée :
    # un ADR est court par nature, et une différence franche reste fiable sur une seconde.
    reliable = w > 0.2
    median_se = float(np.median(se[reliable])) if np.any(reliable) else float("inf")
    conf = float(np.median(c_abs[anchor]))
    low_confidence = bool(conf < LOW_CONFIDENCE_BELOW or median_se > SE_LOW_CONFIDENCE_DB)
    k = 0.0 if low_confidence else 1.0
    if low_confidence:
        k = float(np.clip(conf / LOW_CONFIDENCE_BELOW, 0.0, 1.0))
    lo_b = BOUNDS_DB_LOW_CONFIDENCE[0] + k * (BOUNDS_DB[0] - BOUNDS_DB_LOW_CONFIDENCE[0])
    hi_b = BOUNDS_DB_LOW_CONFIDENCE[1] + k * (BOUNDS_DB[1] - BOUNDS_DB_LOW_CONFIDENCE[1])
    lower = np.full(centers.size, lo_b)
    upper = np.full(centers.size, hi_b)
    upper[w <= 0.05] = 0.0  # pas de boost là où l'information manque
    if hf_synthesized:
        upper[centers >= HF_SYNTHETIC_HZ] = min(hi_b, HF_SYNTHETIC_MAX_BOOST_DB)

    g = _solve_regularised(d, w, lower, upper, lambda_smooth, c_abs)
    saturated = float(np.mean((g <= lower + 1e-6) | (g >= upper - 1e-6)))
    warnings = []
    if low_confidence:
        warnings.append("EQ_LOW_CONFIDENCE")
    if saturated > 0.15:
        warnings.append("EQ_CORRECTION_LIMITED")
    if hf_synthesized:
        warnings.append("EQ_ROOM_CONTEXT_UNCERTAIN")
    return {
        "version": MATCH_VERSION,
        "frequencies_hz": centers,
        "gain_db": g,
        "weights": w,
        "absolute_confidence": c_abs,
        "median_confidence": conf,
        "raw_difference_db": d,
        "level_offset_db": offset,
        "saturated_fraction": saturated,
        "bounds_db": [round(lo_b, 2), round(hi_b, 2)],
        "uncertainty_db": se,
        "median_uncertainty_db": median_se,
        "phoneme_classes_used": classes_used,
        "low_confidence": low_confidence,
        "warnings": warnings,
        "reference_active_seconds": ref_stats["active_seconds"],
        "destination_active_seconds": base_stats["active_seconds"],
        "hf_synthesized": hf_synthesized,
    }


def _difference_by_class(ref_stats: dict, base_stats: dict, centers: np.ndarray, use_classes: bool = True):
    """Écart référence − baseline en dB, comparé **classe de phonèmes par classe de phonèmes**.

    Deux prises n'ont ni le même texte ni la même proportion de voyelles et de consonnes sourdes :
    comparer un spectre moyen global prendrait cette différence de contenu pour une différence de
    prise de son. Chaque classe est donc comparée à elle-même, puis les écarts sont combinés en
    pondérant par l'inverse de leur variance. Retourne (écart, erreur type, classes utilisées).
    """
    shared = [c for c in ("vowel", "fricative")
              if c in ref_stats.get("classes", {}) and c in base_stats.get("classes", {})] if use_classes else []
    if not shared:
        # Repli : spectre global, incertitude déduite de la dispersion entre blocs.
        p_ref = np.asarray(ref_stats["power"], dtype=np.float64)
        p_base = np.asarray(base_stats["power"], dtype=np.float64)
        eps_ref, eps_base = 1e-6 * float(np.max(p_ref)), 1e-6 * float(np.max(p_base))
        d = 10 * np.log10(p_ref + eps_ref) - 10 * np.log10(p_base + eps_base)
        # Erreur type de la moyenne : dispersion **divisée par la racine du nombre de blocs**.
        var = np.zeros(centers.size)
        for s in (ref_stats, base_stats):
            disp = np.nan_to_num(np.asarray(s["dispersion"], dtype=np.float64), nan=1.0)
            var += (4.34 * disp) ** 2 / max(int(s.get("blocks", 1)), 1)  # dispersion relative → dB
        return d, np.maximum(np.sqrt(var), 0.1), []

    num = np.zeros(centers.size)
    den = np.zeros(centers.size)
    for name in shared:
        r, b = ref_stats["classes"][name], base_stats["classes"][name]
        d_c = np.asarray(r["mean_db"]) - np.asarray(b["mean_db"])
        var = np.asarray(r["se_db"]) ** 2 + np.asarray(b["se_db"]) ** 2
        weight = 1.0 / np.maximum(var, 1e-4)
        num += weight * d_c
        den += weight
    d = num / np.maximum(den, 1e-12)
    se = np.sqrt(1.0 / np.maximum(den, 1e-12))
    return d, np.maximum(se, 0.1), shared


def _solve_regularised(d: np.ndarray, w: np.ndarray, lower: np.ndarray, upper: np.ndarray,
                       lambda_smooth: float | None = None,
                       c_abs: np.ndarray | None = None) -> np.ndarray:
    """min_g  mean(w·(g-d)²) + λs·mean((D²g)²) + mean(λ0(b)·g(b)²), bornes incluses.

    Le retrait vers zéro est piloté par la fiabilité **absolue** : une bande médiocre est ramenée
    vers zéro même si elle est, relativement, la meilleure du lot.
    """
    n = d.size
    reliability = np.clip(w if c_abs is None else c_abs, 0.01, 1.0)
    # Pénalité en 1/c − 1 plutôt que linéaire : une bande seulement « non vérifiable » (c ≈ 0,5)
    # garde l'essentiel de sa correction, une bande vraiment mauvaise (c ≈ 0,05) s'efface.
    lam0 = LAMBDA_ZERO_BASE + LAMBDA_ZERO_UNRELIABLE * (1.0 / reliability - 1.0)
    rows = [np.diag(np.sqrt(np.clip(w, 0.0, None) / n))]
    targets = [np.sqrt(np.clip(w, 0.0, None) / n) * d]
    d2 = np.zeros((max(0, n - 2), n))
    for i in range(n - 2):
        d2[i, i:i + 3] = (1.0, -2.0, 1.0)
    rows.append(math.sqrt((LAMBDA_SMOOTH if lambda_smooth is None else lambda_smooth) / max(1, n - 2)) * d2)
    targets.append(np.zeros(max(0, n - 2)))
    rows.append(np.diag(np.sqrt(lam0 / n)))
    targets.append(np.zeros(n))
    A = np.vstack(rows)
    b = np.concatenate(targets)
    res = optimize.lsq_linear(A, b, bounds=(lower, upper), method="bvls", max_iter=200)
    if not np.all(np.isfinite(res.x)):
        raise MimeticError("EQ_NO_RELIABLE_BANDS", "solution non finie")
    return res.x


def build_profile(reference: np.ndarray, fs_ref: int, destination: np.ndarray, fs_dst: int,
                  profile: RoomProfile | None, settings: RenderSettings, *, amount: float = 1.0,
                  preserve_level: bool = True, use_classes: bool = False,
                  lambda_smooth: float | None = None) -> dict:
    """Chaîne complète : baseline → statistiques → courbe → FIR, prête pour le rendu."""
    x = np.asarray(destination, dtype=np.float64).ravel()
    ref = resample_reference(np.asarray(reference, dtype=np.float64).ravel(), fs_ref, fs_dst)
    baseline = build_baseline(_trim(x, fs_dst), fs_dst, profile, settings)

    ref_stats = speech_stats.measure(_trim(ref, fs_dst), fs_dst, label="source")
    base_stats = speech_stats.measure(baseline, fs_dst, label="destination+room")
    hf_synth = bool(profile is not None and profile.extension)
    curve = estimate_curve(ref_stats, base_stats, hf_synthesized=hf_synth, use_classes=use_classes,
                           lambda_smooth=lambda_smooth)
    fir = eq_filter.design_fir(curve["frequencies_hz"], curve["gain_db"], fs_dst, amount=amount)
    dst_stats = speech_stats.measure(_trim(x, fs_dst), fs_dst, label="destination")
    return {
        "curve": curve,
        "filter": fir,
        "voiced_stats": dst_stats,  # masque de parole de l'ADR, pour la compensation de niveau
        "amount": amount,
        "preserve_adr_level": preserve_level,
        "stats": {"reference": _light(ref_stats), "baseline": _light(base_stats),
                  "destination": _light(dst_stats)},
        "room_context": {
            "profile_id": None if profile is None else profile.profile_id,
            "wet_gain_db": settings.wet_gain_db,
            "additional_predelay_seconds": settings.additional_predelay_seconds,
            "hf_synthesized": hf_synth,
        },
        "warnings": curve["warnings"],
        "method": MATCH_VERSION,
    }


def _light(stats: dict) -> dict:
    """Version sérialisable (sans masques ni tableaux de trames)."""
    return {k: (v.tolist() if isinstance(v, np.ndarray) else v)
            for k, v in stats.items() if k not in ("voiced", "centers_hz", "power", "dispersion", "snr_db")}

"""Lot A2 : fiabilité absolue, fond seul et incertitude honnête.

Critères repris de `docs/architecture-references-difficiles.md` §10.2. Chaque test verrouille un
défaut vérifié dans la V2, chiffres à l'appui dans `docs/eq-validation.md`.
"""

import numpy as np
import pytest
from scipy import signal as sps

from mimetic.analysis import eq_match, speech_stats
from mimetic.domain.errors import MimeticError

FS = 48000


def stats(power_shape_db=None, snr_db=24.0, blocks=12, dispersion=0.2, **extra):
    """Statistiques construites : on fait varier un seul facteur à la fois."""
    f, _ = speech_stats.band_edges(FS)
    p = (f / 1000) ** -1.0
    if power_shape_db is not None:
        p = p * 10 ** (power_shape_db(f) / 10)
    return dict(ok=True, active_seconds=4.0, centers_hz=f, power=p, blocks=blocks,
                dispersion=np.full(f.size, dispersion), classes={},
                snr_db=None if snr_db is None else np.full(f.size, snr_db),
                low_support=blocks < 10, **extra)


def curve_for(snr_db, **kw):
    shape = lambda f: 4.0 * np.tanh(np.log2(f / 1500))  # noqa: E731
    return eq_match.estimate_curve(stats(shape, snr_db=snr_db, **kw),
                                   stats(snr_db=snr_db, **kw), hf_synthesized=False)


# --- 1. la confiance absolue ne doit pas survivre à la renormalisation des poids ---------------
def test_lower_snr_reduces_confidence_and_correction():
    clean, noisy = curve_for(24.0), curve_for(6.0)
    assert clean["median_confidence"] > noisy["median_confidence"]
    # V2 : courbes strictement identiques de 24 dB à 6,2 dB, puis refus brutal à 6,0 dB.
    assert np.std(np.asarray(noisy["gain_db"])) < 0.75 * np.std(np.asarray(clean["gain_db"]))
    assert clean["low_confidence"] is False and noisy["low_confidence"] is True
    assert np.all(np.asarray(clean["weights"]) <= 1.0)


def test_confidence_degrades_gradually_without_cliff():
    amplitudes, confidences = [], []
    for snr in (24.0, 18.0, 12.0, 8.0, 6.0, 3.0, 0.0):
        c = curve_for(snr)       # aucun de ces niveaux ne doit provoquer un refus
        amplitudes.append(float(np.std(np.asarray(c["gain_db"]))))
        confidences.append(c["median_confidence"])
    assert all(a >= b - 1e-9 for a, b in zip(amplitudes, amplitudes[1:])), amplitudes
    assert all(a >= b - 1e-9 for a, b in zip(confidences, confidences[1:])), confidences
    assert confidences[0] > confidences[-1] * 2


def test_unstable_background_lowers_confidence():
    f, _ = speech_stats.band_edges(FS)
    stable = curve_for(12.0, noise_variability_db=np.full(f.size, 4.0))
    moving = curve_for(12.0, noise_variability_db=np.full(f.size, 22.0))
    assert moving["median_confidence"] < stable["median_confidence"]
    assert np.std(np.asarray(moving["gain_db"])) < np.std(np.asarray(stable["gain_db"]))


def test_everything_unusable_is_refused_explicitly():
    f, _ = speech_stats.band_edges(FS)
    with pytest.raises(MimeticError) as e:
        eq_match.estimate_curve(stats(snr_db=-30.0, noise_variability_db=np.full(f.size, 40.0)),
                                stats(snr_db=-30.0, noise_variability_db=np.full(f.size, 40.0)),
                                hf_synthesized=False)
    assert e.value.code == "EQ_NO_RELIABLE_BANDS"


# --- 2. une queue de réverbération n'est pas du bruit de fond ----------------------------------
def speech_then_tail(rt=0.8, tail=True, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(6.0 * FS)) / FS
    voice = np.sin(2 * np.pi * 180 * t) * ((t % 3.0) < 1.2) * 0.3
    x = voice
    if tail:
        n = int(rt * FS)
        ir = rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / FS / rt) * 0.05
        x = voice + sps.oaconvolve(voice, ir, mode="full")[: voice.size]
    return x + rng.standard_normal(x.size) * noise


def test_reverb_tail_is_not_counted_as_background():
    with_tail = speech_stats.measure(speech_then_tail(tail=True, noise=1e-4), FS)
    without = speech_stats.measure(speech_then_tail(tail=False, noise=1e-4), FS)
    # Le fond réel est identique dans les deux cas : la queue ne doit pas le gonfler.
    a = np.median(np.asarray(with_tail["snr_db"]))
    b = np.median(np.asarray(without["snr_db"]))
    assert abs(a - b) < 12.0, (a, b)
    assert with_tail["noise_known"] and without["noise_known"]


def test_noise_only_mask_excludes_speech_and_its_decay():
    x = speech_then_tail(tail=True, noise=1e-4)
    freqs, _, power = speech_stats._stft(x, FS)
    voiced, _ = speech_stats.activity(power, freqs)
    bands = speech_stats._to_bands(power, freqs, speech_stats.band_edges(FS)[1])
    mask = speech_stats._noise_only_mask(bands, voiced)
    assert not np.any(mask & voiced)                       # jamais de parole
    guard = int(speech_stats.GUARD_AFTER_MS / 1000 / speech_stats.HOP_S)
    for i in np.flatnonzero(voiced):
        assert not mask[i:i + guard].any()                 # ni sa décroissance immédiate


def test_digital_silence_is_negligible_noise_not_unknown_noise():
    """Un ADR de studio n'a pas de fond : ce n'est pas la même chose qu'un fond non mesurable."""
    t = np.arange(int(5.0 * FS)) / FS
    clean = np.sin(2 * np.pi * 200 * t) * ((t % 2.5) < 1.0) * 0.3
    st = speech_stats.measure(clean, FS)
    assert st["noise_negligible"] is True and st["noise_known"] is True
    assert st["noise_variability_db"] is None
    f, _ = speech_stats.band_edges(FS)
    c = eq_match.estimate_curve(stats(lambda x: 4.0 * np.tanh(np.log2(x / 1500)), snr_db=None,
                                      noise_negligible=True),
                                stats(snr_db=None, noise_negligible=True), hf_synthesized=False)
    assert c["median_confidence"] == pytest.approx(1.0)


# --- 3. incertitude : mesurée quand c'est possible, signalée sinon ------------------------------
def test_short_clip_reports_low_support_but_is_still_corrected():
    t = np.arange(int(1.6 * FS)) / FS
    short = np.sin(2 * np.pi * 190 * t) * (np.sin(2 * np.pi * 1.5 * t) > -0.3) * 0.3
    st = speech_stats.measure(np.concatenate([np.zeros(FS // 2), short, np.zeros(FS // 2)]), FS)
    assert st["ok"] and st["low_support"] is True
    assert st["blocks"] < 10 and not np.all(np.isnan(np.asarray(st["dispersion"])))
    c = curve_for(18.0, blocks=4)
    assert np.std(np.asarray(c["gain_db"])) > 0.3   # une réplique courte reste corrigée


def test_snr_is_speech_over_noise_not_signal_plus_noise():
    """Définition corrigée : sous 0 dB de SNR vrai, le diagnostic doit devenir négatif."""
    rng = np.random.default_rng(3)
    t = np.arange(int(8.0 * FS)) / FS
    speech_on = (t % 3.0) < 1.5
    # Voix sans aigus + souffle continu au-dessus de 3 kHz : dans ces bandes il n'y a **que** du bruit.
    voice = np.sin(2 * np.pi * 200 * t) * speech_on * 0.30
    hiss = sps.sosfilt(sps.butter(4, 3000, "high", fs=FS, output="sos"), rng.standard_normal(t.size)) * 0.01
    st = speech_stats.measure(voice + hiss, FS)
    assert st["ok"] and st["snr_definition"] == "speech_only_over_noise"
    hf = np.asarray(st["centers_hz"]) >= 4000
    # (parole + bruit) / bruit ne peut pas être négatif ; parole seule / bruit, si.
    hf_snr = np.asarray(st["snr_db"])[hf]
    assert np.median(hf_snr) < -0.5 and hf_snr.min() < -3.0, hf_snr

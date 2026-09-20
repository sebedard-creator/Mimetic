"""Match EQ : filtre, routage et estimation (architecture-match-eq §12.1)."""

import numpy as np
import pytest
from scipy import signal as sps

from mimetic import pipeline
from mimetic.analysis import eq_match, speech_stats
from mimetic.audio import eq_filter
from mimetic.domain.errors import MimeticError
from mimetic.domain.models import AudioAsset, RenderSettings, RoomProfile

FS = 48000


def fake_speech(seconds=10.0, fs=FS, seed=0):
    """Voix synthétique : harmoniques modulées, formants, pauses. Assez « vocale » pour le détecteur."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * fs)) / fs
    f0 = 120 * (1 + 0.15 * np.sin(2 * np.pi * 3.1 * t))
    phase = 2 * np.pi * np.cumsum(f0) / fs
    x = sum(np.sin(k * phase) / k for k in range(1, 25))
    for fc, q, g in ((600, 4, 6.0), (1400, 6, 4.0), (2900, 8, 3.0)):
        b, a = sps.iirpeak(fc, q, fs=fs)
        x = sps.lfilter(b, a * 10 ** (-g / 40), x)
    x += 0.02 * rng.standard_normal(x.size)          # souffle léger
    env = (np.sin(2 * np.pi * 0.45 * t) > -0.2).astype(float)  # phrases et pauses
    env = sps.lfilter(np.ones(480) / 480, 1, env)
    x *= env
    return 0.3 * x / np.max(np.abs(x))


def known_room(fs=FS, rt=0.5, drr_db=6.0, seed=3):
    rng = np.random.default_rng(seed)
    n = int(0.8 * fs)
    t = np.arange(n) / fs
    wet = rng.standard_normal(n) * np.exp(-6.9 * t / rt)
    wet[: int(0.008 * fs)] = 0.0
    wet *= np.sqrt(10 ** (-drr_db / 10) / np.sum(wet ** 2))
    return wet


def asset(x, fs=FS, name="adr.wav"):
    return AudioAsset("id", "p", name, "sha", fs, x.size, 1, "FLOAT", float(np.max(np.abs(x))), False,
                      x.reshape(-1, 1).copy())


def profile_from(wet, fs=FS):
    return RoomProfile("p", "known_ir", "validated_dsp", wet, fs, "x", {}, "exact_impulse", None,
                       [0, fs / 2], wet.size / fs, [], {}, {})


def known_curve(fs=FS):
    """Cible connue : +4 dB dans le bas-médium, -3 dB en haut-médium."""
    f = np.geomspace(40, min(16000, 0.45 * fs), 240)
    g = 4.0 * np.exp(-((np.log(f / 300)) ** 2) / 0.5) - 3.0 * np.exp(-((np.log(f / 5000)) ** 2) / 0.4)
    return f, g


# --- 1. bypass ---------------------------------------------------------------------------------
def test_bypass_is_bit_identical():
    x = fake_speech(3.0)
    a, prof, s = asset(x), profile_from(known_room()), RenderSettings(wet_gain_db=-2.0)
    base = pipeline.render(a, "mono", prof, s)
    identity = {"filter": eq_filter.design_fir(*known_curve(), FS, amount=0.0), "curve": {},
                "amount": 0.0, "method": "test"}
    same = pipeline.render(a, "mono", prof, s, identity)
    np.testing.assert_array_equal(base.dry, same.dry)
    np.testing.assert_array_equal(base.wet, same.wet)
    assert same.eq_info is None and base.dependency_key == same.dependency_key


# --- 2 & 4. courbe connue et réalisation -------------------------------------------------------
def test_designed_filter_matches_requested_curve():
    f, g = known_curve()
    fir = eq_filter.design_fir(f, g, FS)
    assert fir["fit_error_db"] <= eq_filter.FIT_TOLERANCE_DB
    test_f = np.geomspace(100, 12000, 200)
    _, h = sps.freqz(fir["coefficients"], worN=test_f, fs=FS)
    got = 20 * np.log10(np.abs(h))
    want = np.interp(np.log(test_f), np.log(f), g)
    assert np.max(np.abs(got - want)) <= 0.25
    # phase minimale : l'énergie est concentrée au début, pas de pré-écho centré
    q = fir["coefficients"]
    assert np.sum(q[:64] ** 2) > 0.9 * np.sum(q ** 2)


# --- 5. intensité ------------------------------------------------------------------------------
def test_amount_halves_curve_in_db_and_zero_is_identity():
    f, g = known_curve()
    half = eq_filter.design_fir(f, g, FS, amount=0.5)
    test_f = np.geomspace(100, 12000, 100)
    _, h = sps.freqz(half["coefficients"], worN=test_f, fs=FS)
    want = 0.5 * np.interp(np.log(test_f), np.log(f), g)
    assert np.max(np.abs(20 * np.log10(np.abs(h)) - want)) <= 0.3
    zero = eq_filter.design_fir(f, g, FS, amount=0.0)
    assert zero["identity"] and zero["coefficients"].size == 1
    y, gain = eq_filter.apply_filter(np.arange(10.0), zero["coefficients"])
    np.testing.assert_array_equal(y, np.arange(10.0))
    assert gain == 0.0


# --- 8. routage : une seule application du filtre ----------------------------------------------
def test_routing_sum_equals_filtered_baseline():
    x = fake_speech(2.0)
    wet_ir = known_room()
    a, prof, s = asset(x), profile_from(wet_ir), RenderSettings(wet_gain_db=-3.0, additional_predelay_seconds=0.005)
    f, g = known_curve()
    eq = {"filter": eq_filter.design_fir(f, g, FS), "curve": {"frequencies_hz": f, "gain_db": g},
          "amount": 1.0, "method": "test", "preserve_adr_level": False}
    r = pipeline.render(a, "mono", prof, s, eq)
    x_eq, _ = eq_filter.apply_filter(x, eq["filter"]["coefficients"], None, False)
    h = pipeline.profile_ir_at_rate(prof, FS)
    h_eff = np.concatenate([np.zeros(int(0.005 * FS)), h]) * 10 ** (-3.0 / 20)
    k_room = np.zeros(h_eff.size)
    k_room[0] = 1.0
    k_room += h_eff
    expected = sps.oaconvolve(x_eq, k_room, mode="full")
    got = r.dry + r.wet
    assert got.size == expected.size
    assert np.max(np.abs(got - expected)) <= 1e-6
    assert r.eq_info["applied_common_gain_db"] == 0.0
    np.testing.assert_array_equal(r.original[: x.size], x)


# --- 3. conservation de niveau -----------------------------------------------------------------
def test_level_preservation_keeps_speech_rms():
    x = fake_speech(3.0)
    f, g = known_curve()
    fir = eq_filter.design_fir(f, g, FS)
    stats = speech_stats.measure(x, FS)
    mask = speech_stats.voiced_sample_mask(stats, x.size, FS)
    y, gain_db = eq_filter.apply_filter(x, fir["coefficients"], mask, True)
    e_in = np.sum(x[mask] ** 2)
    e_out = np.sum(y[: x.size][mask] ** 2)
    assert abs(10 * np.log10(e_out / e_in)) <= 0.5
    assert abs(gain_db) <= eq_filter.LEVEL_GAIN_LIMIT_DB


# --- oracle : retrouver une EQ connue depuis référence et baseline ------------------------------
def _oracle_profile(scale=1.0, seed=0):
    x = fake_speech(12.0, seed=seed)
    wet_ir = known_room()
    prof, s = profile_from(wet_ir), RenderSettings()
    baseline = eq_match.build_baseline(x, FS, prof, s)
    f, g = known_curve()
    fir = eq_filter.design_fir(f, g, FS)
    reference = scale * sps.oaconvolve(baseline, fir["coefficients"], mode="full")
    return eq_match.build_profile(reference, FS, x, FS, prof, s), (f, g)


def test_oracle_recovers_known_curve_and_ignores_level():
    prof, (f, g) = _oracle_profile()
    centers = np.asarray(prof["curve"]["frequencies_hz"])
    got = np.asarray(prof["curve"]["gain_db"])
    want = np.interp(np.log(centers), np.log(f), g)
    band = (centers >= 200) & (centers <= 5000)
    err = got[band] - want[band]
    err -= err.mean()
    assert np.sqrt(np.mean(err ** 2)) <= 1.5, f"erreur RMS {np.sqrt(np.mean(err ** 2)):.2f} dB"
    assert np.corrcoef(got[band], want[band])[0, 1] > 0.9

    quiet, _ = _oracle_profile(scale=0.5)  # un simple changement de niveau ne doit pas changer la forme
    d = np.asarray(quiet["curve"]["gain_db"]) - got
    assert np.max(np.abs(d - d.mean())) <= 0.5


# --- refus propres -----------------------------------------------------------------------------
def test_refuses_when_no_speech():
    silence = np.zeros(FS * 3)
    with pytest.raises(MimeticError) as e:
        eq_match.build_profile(silence, FS, silence, FS, None, RenderSettings())
    assert e.value.code in ("EQ_INSUFFICIENT_SPEECH", "EQ_NO_RELIABLE_BANDS")


def test_curve_respects_bounds_and_flags_saturation():
    x = fake_speech(6.0)
    prof, s = profile_from(known_room()), RenderSettings()
    baseline = eq_match.build_baseline(x, FS, prof, s)
    extreme = sps.sosfilt(sps.butter(4, 900, "low", fs=FS, output="sos"), baseline)  # -30 dB d'aigus
    out = eq_match.build_profile(extreme, FS, x, FS, prof, s)
    g = np.asarray(out["curve"]["gain_db"])
    lo, hi = out["curve"]["bounds_db"]
    assert g.min() >= lo - 1e-6 and g.max() <= hi + 1e-6
    assert "EQ_CORRECTION_LIMITED" in out["warnings"]

"""Tests bloquants du moteur de rendu (architecture §10.1). Aucun modèle, aucune donnée personnelle."""

import numpy as np
import pytest
from scipy import signal as sps

from mimetic.audio import dsp
from mimetic.domain.errors import MimeticError
from mimetic.domain.models import RenderSettings

FS = 48000
RNG = np.random.default_rng(0)


def speech_like(n=4800):
    x = RNG.standard_normal(n) * np.hanning(n)
    return x / np.max(np.abs(x)) * 0.5


# 1. IR connue : delta[0] + 0.25 delta[k]
def test_known_ir_wet_is_delayed_scaled_adr_and_mix_reconstructs_total():
    k = 480
    h_total = np.zeros(1000)
    h_total[0], h_total[k] = 1.0, 0.25
    wet_ir, a, method = dsp.split_known_direct(h_total, 0, 1)
    assert a == 1.0 and method == "exact_impulse"
    x = speech_like()
    dry, wet = dsp.render_wet(x, wet_ir, FS, RenderSettings())
    expected = np.zeros(x.size + wet_ir.size - 1)
    expected[k:k + x.size] = 0.25 * x
    np.testing.assert_allclose(wet, expected, atol=1e-12)
    assert np.all(wet[:k] == 0)  # pas de duplication au temps zéro
    np.testing.assert_allclose(dry + wet, np.convolve(x, h_total), atol=1e-12)


def test_direct_offset_and_gain_are_removed_but_sign_preserved():
    d, k = 37, 200
    h = np.zeros(600)
    h[d], h[d + k] = -0.5, 0.1
    wet_ir, a, _ = dsp.split_known_direct(h, d, 1)
    assert a == -0.5
    assert wet_ir[0] == 0 and wet_ir[k] == pytest.approx(-0.2)
    assert np.count_nonzero(wet_ir) == 1


# 2. Direct seul → wet nul
def test_direct_only_gives_zero_wet():
    h = np.zeros(100)
    h[0] = 0.8
    wet_ir, _, _ = dsp.split_known_direct(h, 0, 1)
    _, wet = dsp.render_wet(speech_like(), wet_ir, FS, RenderSettings())
    assert np.all(wet == 0)


# 3. Réflexion dominante : l'indice déclaré fait foi, pas le maximum global
def test_dominant_reflection_does_not_move_origin():
    h = np.zeros(500)
    h[10], h[110] = 0.3, 0.9
    wet_ir, a, _ = dsp.split_known_direct(h, 10, 1)
    assert a == 0.3
    assert wet_ir[100] == pytest.approx(3.0)
    assert wet_ir[0] == 0


# 4. Direct étalé : fenêtre déclarée retirée, réflexions conservées
def test_spread_direct_window_energy_calibration():
    h = np.zeros(800)
    packet = np.array([0.3, 0.8, 0.4, 0.1])
    h[5:9] = packet
    h[300] = 0.2
    wet_ir, g, method = dsp.split_known_direct(h, 5, 4)
    assert method == "energy_equivalent"
    assert g == pytest.approx(np.sqrt(np.sum(packet**2)))
    assert np.all(wet_ir[:4] == 0)
    assert wet_ir[295] == pytest.approx(0.2 / g)


# 5. Linéarité du gain
def test_gain_linearity_and_dry_untouched():
    h = np.zeros(300)
    h[120] = 0.3
    h[250] = -0.1
    x = speech_like()
    dry0, w0 = dsp.render_wet(x, h, FS, RenderSettings())
    _, w_x2 = dsp.render_wet(2 * x, h, FS, RenderSettings())
    dry6, w6 = dsp.render_wet(x, h, FS, RenderSettings(wet_gain_db=20 * np.log10(2)))
    np.testing.assert_allclose(w_x2, 2 * w0, atol=1e-12)
    np.testing.assert_allclose(w6, 2 * w0, atol=1e-12)
    np.testing.assert_array_equal(dry0, dry6)
    np.testing.assert_array_equal(dry0[: x.size], x)
    _, w6b = dsp.render_wet(x, h, FS, RenderSettings(wet_gain_db=6.0206))
    np.testing.assert_allclose(w6b, 2 * w0, rtol=1e-5)


# 6. Silence et bornes
def test_silence_zero_ir_and_bad_inputs():
    x = np.zeros(1000)
    h = np.zeros(200)
    h[50] = 0.5
    dry, wet = dsp.render_wet(x, h, FS, RenderSettings())
    assert wet.size == 1199 and not np.any(wet) and np.all(np.isfinite(wet))
    _, wet = dsp.render_wet(speech_like(), np.zeros(200), FS, RenderSettings())
    assert not np.any(wet)
    tiny = np.zeros(10)
    tiny[0] = 1e-9
    with pytest.raises(MimeticError) as e:
        dsp.split_known_direct(tiny, 0, 1)
    assert e.value.code == "DIRECT_PATH_UNRESOLVED"
    bad = np.ones(10)
    bad[3] = np.nan
    with pytest.raises(MimeticError):
        dsp.split_known_direct(bad, 0, 1)
    with pytest.raises(MimeticError):
        dsp.render_wet(np.array([]), h, FS, RenderSettings())


# 7. Temps : silence initial, pré-délai, longueur N+M-1
def test_timing_initial_silence_predelay_and_length():
    lead = 1000
    x = np.concatenate([np.zeros(lead), speech_like()])
    k = 240
    h = np.zeros(k + 1)
    h[k] = 0.5
    pre = 0.010
    dry, wet = dsp.render_wet(x, h, FS, RenderSettings(additional_predelay_seconds=pre))
    m = h.size + int(round(pre * FS))
    assert wet.size == dry.size == x.size + m - 1
    first = np.argmax(np.abs(wet) > 0)
    assert first == lead + k + int(round(pre * FS)) + np.argmax(np.abs(x[lead:]) > 0)
    with pytest.raises(MimeticError):
        dsp.render_wet(x, h, FS, RenderSettings(additional_predelay_seconds=0.25))


# 8. Convolution : oaconvolve vs convolution directe float64
def test_convolution_matches_direct():
    x = RNG.uniform(-1, 1, 20000)
    h = RNG.uniform(-1, 1, 3000) * np.exp(-np.arange(3000) / 500)
    _, wet = dsp.render_wet(x, h, FS, RenderSettings())
    np.testing.assert_allclose(wet, np.convolve(x, h), atol=1e-6)


# 9. Conversion d'IR : gain dans la bande commune et délai
@pytest.mark.parametrize("fs_src,fs_dst", [(16000, 48000), (16000, 44100), (48000, 44100), (44100, 48000)])
def test_ir_resampling_preserves_transfer_gain_and_delay(fs_src, fs_dst):
    delay_s = 0.050
    h = np.zeros(int(0.2 * fs_src))
    h[int(round(delay_s * fs_src))] = 0.5
    h_dst = dsp.resample_ir(h, fs_src, fs_dst)
    f_test = 1000.0
    t = np.arange(int(0.5 * fs_dst)) / fs_dst
    s = np.sin(2 * np.pi * f_test * t)
    y = np.convolve(s, h_dst)[: t.size]
    steady = slice(int(0.15 * fs_dst), int(0.45 * fs_dst))
    amp = np.sqrt(2 * np.mean(y[steady] ** 2))
    assert 20 * np.log10(amp / 0.5) == pytest.approx(0.0, abs=0.1)
    # délai : pic de l'impulsion interpolée à ±1 échantillon cible
    assert abs(np.argmax(np.abs(h_dst)) - delay_s * fs_dst) <= 1


# 12. Canaux : pas de convolution sur l'axe des canaux
def test_render_rejects_2d_by_flattening_only_mono_input():
    from mimetic.audio.io import select_channel
    from mimetic.domain.models import AudioAsset
    data = np.stack([np.ones(100), -np.ones(100)], axis=1)
    a = AudioAsset("i", "p", "n", "s", FS, 100, 2, "FLOAT", 1.0, False, data)
    np.testing.assert_array_equal(select_channel(a, "left"), np.ones(100))
    np.testing.assert_array_equal(select_channel(a, "right"), -np.ones(100))
    np.testing.assert_array_equal(select_channel(a, "mean"), np.zeros(100))
    with pytest.raises(MimeticError):
        select_channel(a, "mono")


def test_calibrate_to_drr():
    raw = RNG.standard_normal(5000)
    for drr in (-6.0, 0.0, 10.0):
        wet = dsp.calibrate_to_drr(raw, drr)
        assert dsp.drr_db_of_wet(wet) == pytest.approx(drr, abs=1e-9)

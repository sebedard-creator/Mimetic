"""Extension hybride des aigus : bande estimée préservée, aigus présents, bornés, déterministes."""

import numpy as np
import pytest
from scipy import signal as sps

from mimetic import pipeline
from mimetic.audio import dsp, hybrid
from mimetic.domain.models import RenderSettings, RoomProfile


def wet16(rt_by_band=((None, 2000, 0.6), (2000, 5000, 0.45), (5000, None, 0.3)), seed=0):
    fs = 16000
    rng = np.random.default_rng(seed)
    n = fs
    t = np.arange(n) / fs
    w = np.zeros(n)
    for lo, hi, rt in rt_by_band:
        if lo is None:
            sos = sps.butter(4, hi, "low", fs=fs, output="sos")
        elif hi is None:
            sos = sps.butter(4, lo, "high", fs=fs, output="sos")
        else:
            sos = sps.butter(4, [lo, hi], "bandpass", fs=fs, output="sos")
        w += sps.sosfiltfilt(sos, rng.standard_normal(n)) * np.exp(-6.9 * t / rt)
    w[:17] = 0.0
    return w * 0.05


def band_energy(x, fs, lo, hi):
    f, p = sps.welch(x, fs, nperseg=2048)
    m = (f >= lo) & (f < hi)
    return p[m].mean()


def test_fit_and_extend_properties():
    w = wet16()
    params = hybrid.fit(w, 16000)
    assert params["decay_rate_fit"]["b_per_s_per_khz2"] >= 0
    out, info = hybrid.extend(w, 16000, 48000, params, seed=7)
    out2, _ = hybrid.extend(w, 16000, 48000, params, seed=7)
    np.testing.assert_array_equal(out, out2)
    plain = dsp.resample_ir(w, 16000, 48000)
    assert out.size == plain.size
    # bande estimée inchangée (hors zone de transition)
    lo_o = sps.sosfiltfilt(sps.butter(8, 6000, "low", fs=48000, output="sos"), out)
    lo_p = sps.sosfiltfilt(sps.butter(8, 6000, "low", fs=48000, output="sos"), plain)
    assert np.sqrt(np.sum((lo_o - lo_p) ** 2) / np.sum(lo_p ** 2)) < 0.02
    # aigus présents alors qu'ils étaient absents, sans dépasser la bande de référence
    ref = band_energy(out, 48000, 5000, 7000)
    assert band_energy(plain, 48000, 12000, 16000) < ref * 1e-4
    hf = band_energy(out, 48000, 12000, 16000)
    assert ref * 1e-4 < hf <= ref * 1.01
    # décroissance des aigus synthétisés pas plus longue que la bande de référence
    for b in info["synthesized_bands"]:
        assert hybrid.RT_MIN_S <= b["rt60_s"] <= params["rt_ref_s"] + 1e-9
        assert b["early_density_db_rel_ref"] <= 0
    assert info["synthesized_band_hz"][0] == hybrid.CROSSOVER_HZ

def test_no_synthesized_energy_before_first_reflection():
    w = wet16()
    w[:800] = 0.0  # première réflexion à 50 ms
    out, _ = hybrid.extend(w, 16000, 48000, hybrid.fit(w, 16000), seed=3)
    pre = out[: 2400 - 96]  # jusqu'à 2 ms avant (lobe des filtres de conversion)
    assert np.sum(pre ** 2) < 1e-6 * np.sum(out ** 2)


def test_render_marks_synthesized_highs_and_44k1():
    w = wet16()
    prof = RoomProfile("p", "recrir", "experimental", w, 16000, "x", {}, "energy_equivalent", 3.0,
                       [None, 8000], 1.0, ["HF_SYNTHESIZED"], {}, {}, seed=1, extension=hybrid.fit(w, 16000))
    from mimetic.domain.models import AudioAsset
    x = np.zeros((44100, 1))
    x[1000, 0] = 0.5
    adr = AudioAsset("a", "p", "a.wav", "s", 44100, 44100, 1, "PCM_24", 0.5, False, x)
    r = pipeline.render(adr, "mono", prof, RenderSettings())
    assert "HF_SYNTHESIZED" in r.warnings and "BANDWIDTH_LIMITED" not in r.warnings
    assert r.ir_info["synthesized_bands"][-1]["band_hz"][1] == pytest.approx(0.45 * 44100)
    # profil sans extension : limite signalée
    prof2 = RoomProfile("q", "recrir", "experimental", w, 16000, "x", {}, "energy_equivalent", 3.0,
                        [None, 8000], 1.0, ["BANDWIDTH_LIMITED"], {}, {})
    assert "BANDWIDTH_LIMITED" in pipeline.render(adr, "mono", prof2, RenderSettings()).warnings

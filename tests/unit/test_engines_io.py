import json

import numpy as np
import pytest
import soundfile as sf
from scipy import signal as sps

from mimetic import pipeline
from mimetic.audio import dsp, io
from mimetic.domain.errors import MimeticError
from mimetic.domain.models import RenderSettings
from mimetic.engines import known_ir, parametric_manual
from mimetic.engines.recrir import adapter as recrir

FS = 48000


def write(path, data, fs=FS, subtype="PCM_24"):
    sf.write(str(path), data, fs, subtype=subtype)
    return path


def test_parametric_manual_is_deterministic_calibrated_and_decays():
    p = parametric_manual.ManualParams(rt60_s=0.8, bass_ratio=1.0, treble_ratio=1.0, drr_db=4.0, seed=7)
    a, reasons, diag = parametric_manual.synthesize(p)
    b, _, _ = parametric_manual.synthesize(p)
    np.testing.assert_array_equal(a, b)
    assert dsp.drr_db_of_wet(a) == pytest.approx(4.0, abs=1e-6)
    k = int(round(p.first_reflection_ms / 1000 * FS))
    assert np.all(a[:k] == 0) and a[k] != 0
    assert reasons == []
    mid = diag["measured_t20_rt60_s_by_band"][1]
    assert mid == pytest.approx(0.8, rel=0.2)
    c, _, _ = parametric_manual.synthesize(parametric_manual.ManualParams(seed=8))
    assert not np.array_equal(a[: c.size], c[: a.size])


def test_parametric_manual_truncation_flag():
    _, reasons, _ = parametric_manual.synthesize(parametric_manual.ManualParams(rt60_s=8.0, bass_ratio=2.0))
    assert "TAIL_TRUNCATED" in reasons


def test_recrir_unavailable_without_install(tmp_path):
    caps = recrir.capabilities(tmp_path)
    assert caps["available"] is False and caps["reason"] == "MODEL_UNAVAILABLE"


def _bandlimited_ir(direct_at=2000, direct_amp=0.6, refl=((400, 0.3), (1200, -0.2)), rt=0.4, drr_tail=1e-3):
    """IR totale à 16 kHz façon sortie du modèle : impulsions interpolées (bande limitée) + queue."""
    n = 30000
    h = np.zeros(n)
    h[direct_at] = direct_amp
    for k, a in refl:
        h[direct_at + k] += a
    rng = np.random.default_rng(0)
    t = np.arange(n - direct_at - 800) / 16000
    h[direct_at + 800:] += rng.standard_normal(t.size) * np.exp(-6.9 * t / rt) * 0.05
    h[:direct_at - 50] += rng.standard_normal(direct_at - 50) * drr_tail * 0.01
    up = sps.resample_poly(h, 3, 1)      # étale les impulsions
    return sps.resample_poly(up, 1, 3)


def test_recrir_canonicalize_aligns_removes_direct_and_calibrates():
    h = _bandlimited_ir()
    c = recrir.canonicalize(h)
    w = c["wet"]
    assert c["direct_index_raw"] == 2000
    assert w.size == int(recrir.DEFAULT_POLICY.horizon_s * 16000)
    post = int(round(recrir.DEFAULT_POLICY.post_ms * 16)) + 1
    assert np.all(w[:post] == 0)
    # réflexions conservées à leur retard relatif, gain relatif au direct
    assert w[400] == pytest.approx(0.3 / np.sqrt(c["direct_energy"]), rel=0.05)
    assert w[1200] == pytest.approx(-0.2 / np.sqrt(c["direct_energy"]), rel=0.05)
    assert "DIRECT_PATH_UNRESOLVED" not in c["reasons"] and "BANDWIDTH_LIMITED" in c["reasons"]


def test_recrir_canonicalize_prefers_earlier_direct_over_stronger_reflection():
    h = _bandlimited_ir(direct_amp=0.5, refl=((40, 0.9),))  # réflexion 2,5 ms après, plus forte
    c = recrir.canonicalize(h)
    assert c["direct_index_raw"] == 2000 and c["global_peak_index_raw"] == 2040


def test_recrir_unresolved_direct_is_flagged_and_blocks_render(tmp_path):
    rng = np.random.default_rng(1)
    c = recrir.canonicalize(rng.standard_normal(20000))
    assert "DIRECT_PATH_UNRESOLVED" in c["reasons"]
    adr = io.load_wav(write(tmp_path / "a.wav", np.ones(100) * 0.1))
    from mimetic.domain.models import RoomProfile
    prof = RoomProfile("p", "recrir", "experimental", c["wet"], 16000, "x", {}, "energy_equivalent", None,
                       [None, 8000], 1.0, c["reasons"], {}, {})
    with pytest.raises(MimeticError) as e:
        pipeline.render(adr, "mono", prof, RenderSettings())
    assert e.value.code == "DIRECT_PATH_UNRESOLVED"


def test_recrir_auto_windows_pick_active_passages_and_medoid():
    fs = 48000
    rng = np.random.default_rng(3)
    x = rng.standard_normal(fs * 40) * 1e-4                   # 40 s de quasi-silence
    for a, b in ((5, 12), (20, 26), (31, 38)):                # trois passages parlés
        x[a * fs:b * fs] = rng.standard_normal((b - a) * fs) * 0.2
    w, rates = recrir.select_windows(x, fs)
    assert len(w) == 3 and all(e - s == 6 * fs for s, e in w) and w == sorted(w)
    for (s, e), (a, b) in zip(w, ((5, 12), (20, 26), (31, 38))):
        assert a * fs - fs // 2 <= s and e <= b * fs + fs // 2  # chaque fenêtre dans un passage actif
    assert min(rates) > 0.9
    short = rng.standard_normal(fs * 4) * 0.1
    assert recrir.select_windows(short, fs)[0] == [(0, fs * 4)]
    with pytest.raises(MimeticError):
        recrir.select_windows(short[: fs], fs)
    with pytest.raises(MimeticError):
        recrir.select_windows(np.zeros(fs * 10), fs)
    rs = [{"t20_rt60_s": 0.5, "drr_db": 3.0}, {"t20_rt60_s": 0.55, "drr_db": 3.5}, {"t20_rt60_s": 1.5, "drr_db": -4.0}]
    assert recrir.choose_medoid(rs) in (0, 1)
    x = np.sin(2 * np.pi * 1000 * np.arange(48000) / 48000)
    y = recrir.to_native(x, 48000)
    assert y.size == 16000 and np.sqrt(2 * np.mean(y[1000:-1000] ** 2)) == pytest.approx(1.0, abs=0.01)


class _FakeClient:
    """Worker simulé : renvoie une IR totale différente par fenêtre, la 2e étant le médoïde."""
    def __init__(self, root):
        self.root, self.cancelled, self.n = root, False, 0

    def request(self, payload):
        rts = [0.3, 0.5, 0.55]
        h = _bandlimited_ir(rt=rts[self.n % 3])
        self.n += 1
        np.save(payload["output_npy"], h)
        return {"ok": True, "output_npy": payload["output_npy"], "diagnostics": {"inference_seconds": 0.0}}


def test_estimate_profile_with_several_windows_chooses_non_first_window(tmp_path, monkeypatch):
    # Régression : `results.index(best)` levait « truth value of an array is ambiguous »
    # dès que la fenêtre retenue n'était pas la première.
    monkeypatch.setattr(recrir, "capabilities", lambda root=None: {"available": True})
    fs = 48000
    rng = np.random.default_rng(5)
    x = rng.standard_normal(fs * 20) * 0.1
    prof = recrir.estimate_profile(x, fs, workdir=tmp_path / "w", client=_FakeClient(tmp_path),
                                   reference_meta={"name": "s"})
    assert len(prof.parameters["windows"]) == 3
    assert prof.parameters["chosen_window"] in (1, 2)
    assert prof.wet_ir is not None and prof.extension is not None
    assert "HF_SYNTHESIZED" in prof.quality_reasons


def test_mamba_port_matches_reference_recurrence():
    torch = pytest.importorskip("torch")
    from mimetic.engines.recrir import mamba_ref
    torch.manual_seed(0)
    b, L, D, N = 3, 40, 12, 4
    u = torch.randn(b, L, D)
    delta = torch.nn.functional.softplus(torch.randn(b, L, D))
    A = -torch.arange(1, N + 1).float().repeat(D, 1)
    B, C = torch.randn(b, L, N), torch.randn(b, L, N)
    ref = mamba_ref.selective_scan_loop(u.double(), delta.double(), A.double(), B.double(), C.double())
    fast = mamba_ref.selective_scan(u.double(), delta.double(), A.double(), B.double(), C.double())
    assert torch.allclose(ref, fast, atol=1e-10)
    m = mamba_ref.Mamba(d_model=16, d_state=4, d_conv=4, expand=2)
    keys = set(m.state_dict())
    assert keys == {"in_proj.weight", "conv1d.weight", "conv1d.bias", "x_proj.weight", "dt_proj.weight",
                    "dt_proj.bias", "A_log", "D", "out_proj.weight"}
    assert m(torch.randn(2, 7, 16)).shape == (2, 7, 16)


def test_import_validation(tmp_path):
    ok = write(tmp_path / "ok.wav", np.zeros((100, 2)))
    a = io.load_wav(ok)
    assert a.channels == 2 and a.sample_rate_hz == FS and len(a.sha256) == 64
    with pytest.raises(MimeticError) as e:
        io.load_wav(write(tmp_path / "sr.wav", np.zeros(100), fs=22050))
    assert e.value.code == "UNSUPPORTED_FORMAT"
    fake = tmp_path / "fake.wav"
    fake.write_bytes(b"not a wav at all" * 10)
    with pytest.raises(MimeticError) as e:
        io.load_wav(fake)
    assert e.value.code == "INVALID_AUDIO"
    flac = tmp_path / "x.wav"
    sf.write(str(flac), np.zeros(100), FS, format="FLAC")
    with pytest.raises(MimeticError) as e:
        io.load_wav(flac)
    assert e.value.code == "UNSUPPORTED_FORMAT"
    with pytest.raises(MimeticError) as e:
        io.load_wav(write(tmp_path / "long.wav", np.zeros(FS * 3)), max_seconds=2)
    assert e.value.code == "LIMIT_EXCEEDED"


# 10. Export flottant + 11. pas de contamination : le rendu ne dépend que du profil et de l'ADR
def test_end_to_end_export_roundtrip_preserves_over_unity(tmp_path):
    x = np.zeros(FS)
    x[1000] = 1.0
    x[2000:2100] = 0.9
    adr = io.load_wav(write(tmp_path / "ADR é 01.wav", x, subtype="FLOAT"))
    h = np.zeros(2000)
    h[100] = 0.5
    h[600] = 1.2  # réflexion > direct → wet > 1
    profile = known_ir.build_profile(h, FS, source_name="ir", source_sha256="0", convention="total",
                                     direct_index=100, direct_length=1)
    settings = RenderSettings(wet_gain_db=0.0)
    r = pipeline.render(adr, "mono", profile, settings)
    assert r.wet.size == x.size + 1900 - 1
    assert "MIX_OVER_0DBFS" in r.warnings
    track = [{"index": 0, "label": "mono", "source_channel": "mono", "channel_mode": "mono",
              "result": r, "profile": profile}]
    out = pipeline.export(track, adr, settings, tmp_path / "exp")
    ir = pipeline.export(track, adr, settings, tmp_path / "exp", mode="ir_profile")
    assert ir["files"]["ir_profile_wav"] == "ADR é 01_IR_PROFILE.wav"
    wav = tmp_path / "exp" / out["files"]["wet_wav"]
    assert wav.name == "ADR é 01_IR_ONLY.wav"
    back, fs = sf.read(str(wav), dtype="float64")
    assert fs == FS and back.size == r.wet.size
    np.testing.assert_allclose(back, r.wet, atol=1e-6)
    assert back.max() > 1.0
    rep = json.loads((tmp_path / "exp" / out["files"]["report_json"]).read_text(encoding="utf-8"))
    assert rep["output"]["length_frames"] == r.wet.size
    # pas d'écrasement
    out2 = pipeline.export(track, adr, settings, tmp_path / "exp")
    assert out2["files"]["wet_wav"] == "ADR é 01_IR_ONLY_2.wav"


def test_render_with_ir_at_other_rate(tmp_path):
    adr = io.load_wav(write(tmp_path / "a.wav", np.random.default_rng(1).uniform(-0.1, 0.1, 44100), fs=44100))
    h = np.zeros(8000)
    h[0], h[1600] = 1.0, 0.3  # 100 ms à 16 kHz
    profile = known_ir.build_profile(h, 16000, source_name="ir", source_sha256="0", convention="total")
    r = pipeline.render(adr, "mono", profile, RenderSettings())
    assert r.sample_rate_hz == 44100
    assert r.wet.size == 44100 + 22050 - 1

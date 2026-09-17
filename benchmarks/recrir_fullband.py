"""Pilote pleine bande : IR vérité à 48 kHz avec absorption des aigus, estimation Rec-RIR (16 kHz),
sorties brutes sauvegardées pour évaluer l'extension hybride des aigus hors ligne.

  .venv-engine\\Scripts\\python.exe benchmarks\\recrir_fullband.py <parole_seche_16k> <sortie>

Limite : IR synthétiques ; l'absorption suit un modèle simple (RT et niveau décroissants avec la fréquence).
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from scipy import signal as sps  # noqa: E402

from mimetic.engines.recrir.adapter import to_native  # noqa: E402
from mimetic.engines.recrir.worker import PROTOCOL_OUT, Engine  # noqa: E402

FS = 48000
# Bandes d'octave approximatives pour construire la vérité.
BANDS = [(None, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 8000), (8000, 16000), (16000, None)]


def log(*a):
    PROTOCOL_OUT.write(" ".join(str(x) for x in a) + "\n")
    PROTOCOL_OUT.flush()


def band_center(lo, hi):
    lo = lo or 125.0
    hi = hi or 20000.0
    return math.sqrt(lo * hi)


def fullband_rir(rt_mid: float, drr_db: float, hf_absorb: float, tilt_db_oct: float, seed: int, length_s=1.5):
    """RT_b = rt_mid / (1 + hf_absorb·(f/4 kHz)²) ; niveau tardif incliné de tilt dB/octave au-dessus de 1 kHz."""
    rng = np.random.default_rng(seed)
    n = int(length_s * FS)
    t = np.arange(n) / FS
    wet = np.zeros(n)
    for _ in range(10):  # réflexions précoces légèrement filtrées (absorption aux parois)
        k = int(rng.uniform(0.003, 0.045) * FS)
        tap = np.zeros(64)
        tap[32] = rng.choice([-1, 1]) * rng.uniform(0.3, 1.0) * math.exp(-6.9 * k / FS / rt_mid)
        sos = sps.butter(2, min(20000, 12000 / (1 + hf_absorb)), "low", fs=FS, output="sos")
        tap = sps.sosfiltfilt(sos, tap)
        wet[k:k + 32] += tap[32:]
        wet[max(0, k - 32):k] += tap[32 - (k - max(0, k - 32)):32]
    late = np.zeros(n)
    on = t >= 0.008
    tau = np.clip(t - 0.008, 0, None)
    for lo, hi in BANDS:
        fc = band_center(lo, hi)
        rt = rt_mid / (1 + hf_absorb * (fc / 4000) ** 2)
        if lo is None:
            sos = sps.butter(4, hi, "low", fs=FS, output="sos")
        elif hi is None:
            sos = sps.butter(4, lo, "high", fs=FS, output="sos")
        else:
            sos = sps.butter(4, [lo, hi], "bandpass", fs=FS, output="sos")
        noise = sps.sosfiltfilt(sos, rng.standard_normal(n))
        gain_db = tilt_db_oct * max(0.0, math.log2(fc / 1000))
        late += noise * 10 ** (gain_db / 20) * np.exp(-6.9 * tau / rt) * on
    late *= np.sqrt(np.sum(wet ** 2) * 4 / np.sum(late ** 2))
    wet += late
    wet *= np.sqrt(10 ** (-drr_db / 10) / np.sum(wet ** 2))
    h = wet.copy()
    h[0] += 1.0
    return h, wet


def main():
    speech_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(speech_dir.glob("*.wav"))
    engine = Engine(ROOT / "third_party" / "Rec-RIR", ROOT / "third_party" / "Rec-RIR" / "ckpt" / "epoch35.tar")
    rng = np.random.default_rng(7)
    rooms = [  # (RT médium, DRR, absorption aigus, pente dB/oct)
        (0.4, 4.0, 0.5, -2.0), (0.4, 0.0, 2.0, -4.0), (0.7, 3.0, 1.0, -3.0),
        (0.7, -2.0, 0.2, -1.0), (1.0, 2.0, 1.5, -4.0), (0.3, 6.0, 0.1, 0.0),
    ]
    cases = []
    for i, (rt, drr, absorb, tilt) in enumerate(rooms):
        h, wet = fullband_rir(rt, drr, absorb, tilt, seed=300 + i)
        s, fs = sf.read(files[i % len(files)], dtype="float64")
        s48 = sps.resample_poly(s, 3, 1)
        y = np.convolve(s48, h)[: int(6.0 * FS)]
        y += rng.standard_normal(y.size) * np.sqrt(np.mean(y ** 2)) * 10 ** (-35 / 20)
        native = to_native(y, FS).astype(np.float32)
        t0 = time.perf_counter()
        raw, diag = engine.estimate(native)
        name = f"fb{i}_rt{rt}_drr{drr:+.0f}_abs{absorb}_tilt{tilt:+.0f}"
        np.savez(out_dir / f"{name}.npz", raw=raw, wet_true=wet, fs_true=FS)
        case = {"name": name, "rt_mid": rt, "drr_db": drr, "hf_absorb": absorb, "tilt_db_oct": tilt,
                "seconds": time.perf_counter() - t0}
        cases.append(case)
        log(json.dumps(case))
    (out_dir / "cases.json").write_text(json.dumps(cases, indent=2))


if __name__ == "__main__":
    main()

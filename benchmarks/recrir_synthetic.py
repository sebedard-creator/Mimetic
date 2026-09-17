"""Pilote de qualification Rec-RIR sur IR synthétiques connues (architecture §10.2).

À lancer avec l'environnement moteur :
  .venv-engine\\Scripts\\python.exe benchmarks\\recrir_synthetic.py <dossier_parole_seche_16k> <sortie>

Pour chaque pièce : reference = parole_A * h + bruit (SNR 30 dB), fenêtre de 6 s, estimation brute sauvegardée
avec la vérité (h, direct, réflexions) pour régler la canonicalisation hors ligne.
Limite : IR synthétiques (taps + queue exponentielle 2 bandes) et parole de synthèse vocale anglaise ;
ce pilote n'est pas une validation sur pièces réelles ni sur dialogue de tournage.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from scipy import signal as sps  # noqa: E402

from mimetic.engines.recrir.worker import PROTOCOL_OUT, Engine  # noqa: E402

FS = 16000
WINDOW_S = 6.0


def log(*a):
    PROTOCOL_OUT.write(" ".join(str(x) for x in a) + "\n")
    PROTOCOL_OUT.flush()


def synth_rir(rt60: float, drr_db: float, seed: int, length_s: float = 1.5, predelay_s: float = 0.02):
    rng = np.random.default_rng(seed)
    n = int(length_s * FS)
    d0 = int(predelay_s * FS)
    t = np.arange(n) / FS
    refl = np.zeros(n)
    for _ in range(8):
        k = d0 + int(rng.uniform(0.002, 0.050) * FS)
        env = np.exp(-6.9 * (k - d0) / FS / rt60)
        refl[k] += rng.choice([-1, 1]) * rng.uniform(0.3, 1.0) * env
    tau = np.clip(t - predelay_s - 0.008, 0, None)
    on = (t >= predelay_s + 0.008)
    lo = sps.sosfiltfilt(sps.butter(4, 2000, "low", fs=FS, output="sos"), rng.standard_normal(n))
    hi = sps.sosfiltfilt(sps.butter(4, 2000, "high", fs=FS, output="sos"), rng.standard_normal(n))
    late = on * (lo * np.exp(-6.9 * tau / rt60) + hi * np.exp(-6.9 * tau / (0.7 * rt60)))
    late *= np.sqrt(np.sum(refl**2) * 4 / np.sum(late**2))  # queue ≈ 4× l'énergie précoce
    refl += late
    refl *= np.sqrt(10 ** (-drr_db / 10) / np.sum(refl**2))  # DRR vrai, direct unitaire
    h = refl.copy()
    h[d0] += 1.0
    return h, d0, refl


def main():
    speech_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(speech_dir.glob("*.wav"))
    engine = Engine(ROOT / "third_party" / "Rec-RIR", ROOT / "third_party" / "Rec-RIR" / "ckpt" / "epoch35.tar")
    rng = np.random.default_rng(2026)
    cases = []
    room = 0
    for rt60 in (0.25, 0.5, 0.9):
        for drr in (8.0, 2.0, -3.0):
            h, d0, wet_true = synth_rir(rt60, drr, seed=100 + room)
            for rep in range(2):
                spf = files[(2 * room + rep) % len(files)]
                s, fs = sf.read(spf, dtype="float64")
                assert fs == FS
                y = np.convolve(s, h)[: int(WINDOW_S * FS)]
                noise = rng.standard_normal(y.size)
                y = y + noise * np.sqrt(np.mean(y**2) / np.mean(noise**2) * 10 ** (-30 / 10))
                t0 = time.perf_counter()
                raw, diag = engine.estimate(y.astype(np.float32))
                name = f"room{room}_rt{rt60}_drr{drr:+.0f}_{rep}"
                np.savez(out_dir / f"{name}.npz", raw=raw, h=h, d0=d0, wet_true=wet_true)
                case = {"name": name, "room": room, "rt60": rt60, "drr_db": drr, "speech": spf.name,
                        "seconds": time.perf_counter() - t0, **diag}
                cases.append(case)
                log(json.dumps(case))
            room += 1
    (out_dir / "cases.json").write_text(json.dumps(cases, indent=2))


if __name__ == "__main__":
    main()

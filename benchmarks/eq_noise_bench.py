"""Banc « références difficiles » : bruit de plateau et réverbération face au Match EQ.

  .venv\\Scripts\\python.exe benchmarks/eq_noise_bench.py <parole_seche_16k> [--json sortie.json] [--tag V2]

Vérité connue : une EQ est appliquée à l'ADR, jamais à la source. La correction idéale est son inverse.
Le cas `aucune` n'applique aucune EQ : tout ce que l'estimateur produit alors est une fausse correction.
La source est ensuite dégradée par du bruit (stationnaire ou évolutif) à différents rapports signal/bruit.

Mesures par cas : erreur sur l'EQ connue, fausse correction, refus, confiance annoncée. Le but du lot A1
est de **capturer l'état actuel**, échecs compris, pour mesurer ensuite l'effet des corrections.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from scipy import signal as sps  # noqa: E402

from mimetic.analysis import eq_match  # noqa: E402
from mimetic.audio import eq_filter  # noqa: E402
from mimetic.domain.errors import MimeticError  # noqa: E402
from mimetic.domain.models import RenderSettings, RoomProfile  # noqa: E402

FS = 48000
EVAL_BAND = (200.0, 8000.0)


def room(rt: float, drr_db: float, seed: int = 3) -> RoomProfile:
    rng = np.random.default_rng(seed)
    n = int(min(1.5 * rt + 0.3, 2.0) * FS)
    t = np.arange(n) / FS
    wet = rng.standard_normal(n) * np.exp(-6.9 * t / rt)
    wet[: int(0.008 * FS)] = 0.0
    wet *= np.sqrt(10 ** (-drr_db / 10) / np.sum(wet ** 2))
    return RoomProfile("p", "known_ir", "validated_dsp", wet, FS, "x", {}, "exact_impulse", None,
                       [0, FS / 2], wet.size / FS, [], {}, {})


def truth_curve(kind: str):
    f = np.geomspace(40, 20000, 240)
    if kind == "aucune":
        return f, np.zeros_like(f)
    if kind == "sourd":
        return f, -6.0 / (1 + np.exp(-(np.log(f / 4000)) * 3))
    if kind == "proximite":
        return f, 5.0 * np.exp(-((np.log(f / 180)) ** 2) / 0.6)
    return f, 4.0 * np.exp(-((np.log(f / 2500)) ** 2) / 0.3) - 2.0 * np.exp(-((np.log(f / 500)) ** 2) / 0.5)


def add_noise(x: np.ndarray, snr_db: float | None, kind: str, seed: int) -> np.ndarray:
    if snr_db is None:
        return x
    rng = np.random.default_rng(seed)
    n = sps.sosfilt(sps.butter(2, 4000, "low", fs=FS, output="sos"), rng.standard_normal(x.size))
    if kind == "evolutif":  # moteur qui passe, porte, circulation : le fond n'est pas stationnaire
        env = 0.3 + 1.7 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.12 * np.arange(x.size) / FS))
        n *= env
    n *= np.sqrt(np.mean(x ** 2) / np.mean(n ** 2)) * 10 ** (-snr_db / 20)
    return x + n


def phrased(path: Path, seconds: float) -> np.ndarray:
    x, fs = sf.read(str(path), dtype="float64")
    x = sps.resample_poly(x, 3, 1) if fs == 16000 else x
    need = int(seconds * FS)
    while x.size < need:
        x = np.concatenate([x, np.zeros(int(0.4 * FS)), x])
    return x[:need] * 0.3 / max(np.max(np.abs(x[:need])), 1e-9)


def run(speech_dir: Path) -> list[dict]:
    files = sorted(speech_dir.glob("*.wav"))
    settings = RenderSettings()
    rows = []
    for room_name, rt, drr in (("moyenne", 0.5, 6.0), ("forte", 1.0, 0.0)):
        prof = room(rt, drr)
        for kind in ("aucune", "sourd", "proximite", "presence"):
            f_t, g_t = truth_curve(kind)
            fir = eq_filter.design_fir(f_t, g_t, FS) if kind != "aucune" else {"coefficients": np.array([1.0])}
            for snr, noise_kind in ((None, "propre"), (30.0, "stationnaire"), (20.0, "stationnaire"),
                                    (10.0, "stationnaire"), (5.0, "stationnaire"), (15.0, "evolutif")):
                for pair in range(2):
                    a = phrased(files[(2 * pair) % len(files)], 9.0)
                    b = phrased(files[(2 * pair + 1) % len(files)], 6.0)
                    src = add_noise(eq_match.build_baseline(a, FS, prof, settings), snr, noise_kind, 100 + pair)
                    dst, _ = eq_filter.apply_filter(b, fir["coefficients"], None, False)
                    row = {"piece": room_name, "eq": kind, "snr_db": snr, "bruit": noise_kind, "paire": pair}
                    try:
                        out = eq_match.build_profile(src, FS, dst, FS, prof, settings)
                        c = out["curve"]
                        centers = np.asarray(c["frequencies_hz"])
                        got = np.asarray(c["gain_db"])
                        want = -np.interp(np.log(centers), np.log(f_t), g_t)
                        m = (centers >= EVAL_BAND[0]) & (centers <= EVAL_BAND[1])
                        err = got[m] - want[m]
                        err -= err.mean()
                        row.update({
                            "refus": None,
                            "err_rms_db": float(np.sqrt(np.mean(err ** 2))),
                            "amplitude_courbe_db": float(np.std(got[m])),
                            "confiance_reduite": bool(c["low_confidence"]),
                            "poids_median": float(np.median(np.asarray(c["weights"]))),
                            "confiance_absolue_mediane": float(np.median(np.asarray(c["absolute_confidence"])))
                            if "absolute_confidence" in c else None,
                            "bornes": c["bounds_db"],
                            "sature": float(c["saturated_fraction"]),
                        })
                    except MimeticError as exc:
                        row.update({"refus": exc.code, "err_rms_db": None, "amplitude_courbe_db": None})
                    rows.append(row)
    return rows


def summarise(rows: list[dict]) -> dict:
    def stat(sel, key):
        vals = [r[key] for r in rows if sel(r) and r.get(key) is not None]
        return float(np.median(vals)) if vals else None

    out = {"cas": len(rows), "refus": sum(1 for r in rows if r["refus"])}
    for label, sel in (
        ("propre", lambda r: r["bruit"] == "propre"),
        ("SNR 30", lambda r: r["snr_db"] == 30),
        ("SNR 20", lambda r: r["snr_db"] == 20),
        ("SNR 10", lambda r: r["snr_db"] == 10),
        ("SNR 5", lambda r: r["snr_db"] == 5),
        ("bruit evolutif", lambda r: r["bruit"] == "evolutif"),
    ):
        out[label] = {
            "erreur_EQ_connue": stat(lambda r: sel(r) and r["eq"] != "aucune", "err_rms_db"),
            "fausse_correction": stat(lambda r: sel(r) and r["eq"] == "aucune", "err_rms_db"),
            "amplitude_courbe": stat(sel, "amplitude_courbe_db"),
            "confiance_absolue": stat(sel, "confiance_absolue_mediane"),
            "refus": sum(1 for r in rows if sel(r) and r["refus"]),
            "confiance_reduite": sum(1 for r in rows if sel(r) and r.get("confiance_reduite")),
        }
    return out


def main():
    rows = run(Path(sys.argv[1]))
    summary = summarise(rows)
    tag = sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else "courant"
    print(f"=== {tag} : {summary['cas']} cas, {summary['refus']} refus ===")
    print(f"{'condition':<16} {'err EQ connue':>14} {'fausse corr.':>13} {'ampl. courbe':>13} "
          f"{'conf. abs.':>11} {'refus':>6} {'conf. reduite':>14}")
    for k, v in summary.items():
        if not isinstance(v, dict):
            continue
        f = lambda x, n=2: "   n/d" if x is None else f"{x:6.{n}f}"  # noqa: E731
        print(f"{k:<16} {f(v['erreur_EQ_connue']):>14} {f(v['fausse_correction']):>13} "
              f"{f(v['amplitude_courbe']):>13} {f(v['confiance_absolue']):>11} {v['refus']:>6} {v['confiance_reduite']:>14}")
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(
            json.dumps({"tag": tag, "summary": summary, "rows": rows}, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()

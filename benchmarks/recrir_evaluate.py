"""Évalue les sorties brutes de recrir_synthetic.py pour plusieurs politiques de séparation du direct.

  python benchmarks/recrir_evaluate.py <dossier_bench> [--json sortie.json]

Mesures par cas (fenêtres identiques pour vérité et estimation) :
- RT60 : T20 de Schroeder sur le wet ;
- DRR : direct unitaire vs énergie du wet (vérité : réflexions exactes connues) ;
- EDC : écart moyen (dB) des courbes de décroissance du wet sur 0–300 ms ;
- C50 du wet : énergie 0–50 ms / après 50 ms (équilibre précoce/tardif).
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from mimetic.audio import dsp  # noqa: E402
from mimetic.engines.recrir.adapter import DEFAULT_POLICY, NATIVE_SR, canonicalize  # noqa: E402

FS = NATIVE_SR


def edc_db(w, n):
    e = np.zeros(n)
    m = min(n, w.size)
    e[:m] = w[:m] ** 2
    tail = np.cumsum(e[::-1])[::-1]
    return 10 * np.log10(np.maximum(tail / max(tail[0], 1e-30), 1e-12))


def c50(w):
    k = int(0.05 * FS)
    late = np.sum(w[k:] ** 2)
    return None if late <= 0 else float(10 * np.log10(np.sum(w[:k] ** 2) / late))


def evaluate(bench: Path, policy) -> list[dict]:
    rows = []
    cases = {c["name"]: c for c in json.loads((bench / "cases.json").read_text())} if (bench / "cases.json").exists() else {}
    for f in sorted(bench.glob("*.npz")):
        z = np.load(f)
        d0 = int(z["d0"])
        wet_true = z["wet_true"][d0:d0 + int(policy.horizon_s * FS)]
        c = canonicalize(z["raw"], policy)
        w = c["wet"]
        n300 = int(0.3 * FS)
        edc_err = float(np.mean(np.abs(np.clip(edc_db(w, n300), -60, 0) - np.clip(edc_db(wet_true, n300), -60, 0))))
        rt_t, rt_e = dsp.schroeder_rt(wet_true, FS), c["t20_rt60_s"]
        rows.append({
            "case": f.stem, "rt60_nominal": cases.get(f.stem, {}).get("rt60"),
            "rt60_true_t20": rt_t, "rt60_est_t20": rt_e,
            "rt60_err_ratio": (rt_e / rt_t) if (rt_e and rt_t) else None,
            "drr_true": dsp.drr_db_of_wet(wet_true), "drr_est": c["drr_db"],
            "drr_err_db": (c["drr_db"] - dsp.drr_db_of_wet(wet_true)) if c["drr_db"] is not None else None,
            "c50_true": c50(wet_true), "c50_est": c50(w),
            "edc_err_db_0_300ms": edc_err,
            "peak_to_context_db": c["peak_to_context_db"], "direct_shift_vs_peak": c["direct_index_raw"] - c["global_peak_index_raw"],
            "reasons": c["reasons"],
        })
    return rows


def summary(rows):
    def med(key, f=lambda v: v):
        vals = [f(r[key]) for r in rows if r[key] is not None]
        return float(np.median(vals)) if vals else None
    rt_ok = [r for r in rows if r["rt60_err_ratio"] is not None and r["rt60_true_t20"]]
    rt_abs = [abs(r["rt60_est_t20"] - r["rt60_true_t20"]) for r in rt_ok]
    rt_pass = [abs(r["rt60_est_t20"] - r["rt60_true_t20"]) <= max(0.1, 0.2 * r["rt60_true_t20"]) for r in rt_ok]
    return {
        "n": len(rows),
        "median_abs_rt60_err_s": float(np.median(rt_abs)) if rt_abs else None,
        "rt60_within_target": f"{sum(rt_pass)}/{len(rt_ok)}",
        "median_abs_drr_err_db": med("drr_err_db", abs),
        "median_signed_drr_err_db": med("drr_err_db"),
        "drr_within_3db": f"{sum(1 for r in rows if r['drr_err_db'] is not None and abs(r['drr_err_db']) <= 3)}/{len(rows)}",
        "median_edc_err_db": med("edc_err_db_0_300ms"),
        "unresolved": sum("DIRECT_PATH_UNRESOLVED" in r["reasons"] for r in rows),
    }


def main():
    bench = Path(sys.argv[1])
    out = {}
    for pre, post in [(0.5, 0.5), (1.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 5.0)]:
        pol = replace(DEFAULT_POLICY, pre_ms=pre, post_ms=post)
        rows = evaluate(bench, pol)
        s = summary(rows)
        out[f"pre{pre}_post{post}"] = {"summary": s, "rows": rows}
        print(f"pre={pre} ms post={post} ms :", json.dumps(s))
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()

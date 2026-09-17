"""Évalue l'extension hybride des aigus sur les sorties de recrir_fullband.py.

  python benchmarks/hybrid_evaluate.py <dossier_bench_fb> [--json sortie.json]

Pour chaque bande : RT60 (T20) et niveau précoce 0–50 ms relatif à la bande 5–7 kHz, vérité vs hybride,
et le même niveau sans extension (IR 16 kHz simplement convertie à 48 kHz).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from mimetic.audio import dsp, hybrid  # noqa: E402
from mimetic.engines.recrir.adapter import NATIVE_SR, canonicalize  # noqa: E402

FS = 48000
BANDS = [(5000.0, 7000.0), (7800.0, 11300.0), (11300.0, 16000.0), (16000.0, 20000.0)]


def band_metrics(x, onset):
    out = []
    for lo, hi in BANDS:
        b = hybrid._band(x, FS, lo, hi)
        out.append({"rt": hybrid._rt(b, FS), "lvl": hybrid._early_density_db(b, FS, lo, hi, onset)})
    ref = out[0]["lvl"]
    for o in out:
        o["lvl_rel"] = None if (o["lvl"] is None or ref is None) else o["lvl"] - ref
    return out


def main():
    bench = Path(sys.argv[1])
    rows = []
    for f in sorted(bench.glob("*.npz")):
        z = np.load(f)
        c = canonicalize(z["raw"])
        wet16 = c["wet"]
        params = hybrid.fit(wet16, NATIVE_SR)
        ext, info = hybrid.extend(wet16, NATIVE_SR, FS, params, seed=2026)
        plain = dsp.resample_ir(wet16, NATIVE_SR, FS)
        truth = z["wet_true"][: ext.size]
        onset_e = int(np.flatnonzero(ext)[0])
        onset_t = int(np.flatnonzero(truth)[0])
        mt, me, mp = band_metrics(truth, onset_t), band_metrics(ext, onset_e), band_metrics(plain, onset_e)
        # bande estimée inchangée sous le crossover
        lo_e = hybrid._band(ext, FS, 100, 6000)
        lo_p = hybrid._band(plain, FS, 100, 6000)
        low_err = float(np.sqrt(np.sum((lo_e - lo_p) ** 2) / np.sum(lo_p ** 2)))
        row = {"case": f.stem, "low_band_rel_err": low_err, "bands": []}
        for (lo, hi), t, e, p in zip(BANDS, mt, me, mp):
            row["bands"].append({
                "band": f"{lo/1000:.1f}-{hi/1000:.1f} kHz",
                "rt_true": t["rt"], "rt_hybrid": e["rt"],
                "lvl_rel_true": t["lvl_rel"], "lvl_rel_hybrid": e["lvl_rel"], "lvl_rel_without_ext": p["lvl_rel"],
            })
        rows.append(row)
        print(f.stem, f"(bande basse inchangée : err rel {low_err:.1e})")
        f2 = lambda v, d=2: "  n/d" if v is None else f"{v:5.{d}f}"  # noqa: E731
        for b in row["bands"]:
            print(f"   {b['band']:>14}  RT vrai {f2(b['rt_true'])} hyb {f2(b['rt_hybrid'])}"
                  f"  | niveau rel. vrai {f2(b['lvl_rel_true'],1)} hyb {f2(b['lvl_rel_hybrid'],1)} sans ext {f2(b['lvl_rel_without_ext'],1)} dB")

    def collect(key_true, key_est, idx, rel=False):
        vals = []
        for r in rows:
            b = r["bands"][idx]
            if b[key_true] is None or b[key_est] is None:
                continue
            vals.append((b[key_est] / b[key_true]) if rel else (b[key_est] - b[key_true]))
        return vals

    summary = {}
    for idx in (1, 2, 3):
        name = rows[0]["bands"][idx]["band"] if rows else str(idx)
        rt = collect("rt_true", "rt_hybrid", idx, rel=True)
        lv = collect("lvl_rel_true", "lvl_rel_hybrid", idx)
        lv0 = collect("lvl_rel_true", "lvl_rel_without_ext", idx)
        summary[name] = {
            "rt_ratio_median": float(np.median(rt)) if rt else None,
            "rt_ratio_range": [float(min(rt)), float(max(rt))] if rt else None,
            "level_err_db_median_abs": float(np.median(np.abs(lv))) if lv else None,
            "level_err_db_range": [float(min(lv)), float(max(lv))] if lv else None,
            "level_err_db_without_extension_median": float(np.median(lv0)) if lv0 else None,
        }
    print(json.dumps(summary, indent=2))
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))


if __name__ == "__main__":
    main()

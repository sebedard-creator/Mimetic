"""CLI de validation : même moteur que le service web, sans interface.

Exemples :
  python -m mimetic.cli manual ADR.wav --rt60 0.8 --drr 6 --out exports
  python -m mimetic.cli known-ir ADR.wav IR.wav --direct-index 120 --out exports
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mimetic import pipeline
from mimetic.audio import io
from mimetic.domain.errors import MimeticError
from mimetic.domain.models import RenderSettings
from mimetic.engines import known_ir, parametric_manual


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mimetic")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("manual", "known-ir"):
        s = sub.add_parser(name)
        s.add_argument("adr")
        if name == "known-ir":
            s.add_argument("ir")
            s.add_argument("--convention", choices=["total", "wet_unit_direct"], default="total")
            s.add_argument("--direct-index", type=int, default=None)
            s.add_argument("--direct-length", type=int, default=1)
        else:
            s.add_argument("--rt60", type=float, default=0.6)
            s.add_argument("--bass-ratio", type=float, default=1.0)
            s.add_argument("--treble-ratio", type=float, default=0.7)
            s.add_argument("--drr", type=float, default=6.0)
            s.add_argument("--first-reflection-ms", type=float, default=8.0)
            s.add_argument("--seed", type=int, default=2026)
        s.add_argument("--channel", choices=["mono", "left", "right", "mean"], default="mono")
        s.add_argument("--gain-db", type=float, default=0.0)
        s.add_argument("--predelay-ms", type=float, default=0.0)
        s.add_argument("--out", default="exports")
        s.add_argument("--export-ir", action="store_true")
    args = ap.parse_args(argv)

    try:
        adr = io.load_wav(args.adr)
        if args.cmd == "manual":
            profile = parametric_manual.build_profile(parametric_manual.ManualParams(
                rt60_s=args.rt60, bass_ratio=args.bass_ratio, treble_ratio=args.treble_ratio,
                drr_db=args.drr, first_reflection_ms=args.first_reflection_ms, seed=args.seed,
                sample_rate_hz=adr.sample_rate_hz))
        else:
            ir = io.load_wav(args.ir, accepted_rates=(16000, 22050, 32000, 44100, 48000, 88200, 96000))
            h = ir.data[:, 0]
            idx = args.direct_index if args.direct_index is not None else known_ir.suggest_direct_index(h)
            if args.direct_index is None and args.convention == "total":
                print(f"Indice direct suggéré (non validé) : {idx}", file=sys.stderr)
            profile = known_ir.build_profile(h, ir.sample_rate_hz, source_name=ir.original_name,
                                             source_sha256=ir.sha256, convention=args.convention,
                                             direct_index=idx, direct_length=args.direct_length)
        settings = RenderSettings(args.gain_db, args.predelay_ms / 1000.0)
        result = pipeline.render(adr, args.channel, profile, settings)
        out = pipeline.export(result, adr, args.channel, profile, settings, Path(args.out))
        if args.export_ir:
            ir_out = pipeline.export(result, adr, args.channel, profile, settings, Path(args.out),
                                     mode="ir_profile")
            out["files"] = {**out["files"], **ir_out["files"]}
    except MimeticError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Exporté dans {out['directory']} : {', '.join(out['files'].values())}")
    for w in result.warnings:
        print(f"Avertissement : {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

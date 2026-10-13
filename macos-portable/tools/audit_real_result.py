"""Inspect a generated real-session ZIP without rerunning inference."""
import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "payload")]
from mimetic_portable.runtime import configure
configure()
from mimetic import pro_tools
import numpy as np
import soundfile as sf

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--windows-export", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(root)  # only a locally generated ZIP
        session_path = next(root.rglob("*.ptx"))
        session = pro_tools.api().ProToolsSession(str(session_path))
        report = json.loads((session_path.parent / "Mimetic_report.json").read_text(encoding="utf-8"))
        rows = session.get_timeline_clips()
        assert len(rows) == len(report["placements"]) == 28
        assert len(report["units"]) == 16 and len(session.get_tracks()) == 6
        assert all(u["room"]["engine"]["id"] == "recrir" for u in report["units"])
        assert not any(w.startswith("ROOM_SYNTHETIC_FALLBACK") for u in report["units"] for w in u["warnings"])
        comparisons = []
        baseline_rows = None
        if args.windows_export:
            previous_path = next(args.windows_export.glob("*.ptx"))
            previous = pro_tools.api().ProToolsSession(str(previous_path))
            baseline_rows = {r["clip_name"]: r for r in previous.get_timeline_clips()}
        for row in rows:
            mix = row["track"].startswith("MIMETIC_MIX")
            assert row["muted"] == mix and pro_tools.clip_gain(session, row["clip_name"]) == (0 if mix else 3.5)
            audio, fs = sf.read(session_path.parent / "Audio Files" / row["physical_filename"])
            assert fs == 48000 and len(audio) == row["length_samples"] and np.isfinite(audio).all()
            if args.windows_export:
                previous_row = baseline_rows[row["clip_name"]]
                assert all(row[k] == previous_row[k] for k in ("track", "start_samples", "length_samples", "muted"))
                reference = args.windows_export / "Audio Files" / row["physical_filename"]
                expected, _ = sf.read(reference)
                assert expected.shape == audio.shape
                relative = float(np.linalg.norm(audio - expected) / max(np.linalg.norm(expected), 1e-12))
                assert relative < 0.002
                comparisons.append({"clip": row["clip_name"], "relative_l2": relative,
                                    "peak_difference": float(np.max(np.abs(audio - expected)))})
        summary = {"archive": args.archive.name, "units": 16, "placements": 28, "tracks": 6,
                   "all_room_profiles": "recrir", "synthetic_room_fallbacks": 0,
                   "ir_clip_gain_db": 3.5, "mix_muted": True,
                   "comparisons_to_windows_exports": comparisons}
        (args.archive.parent / "AUDIT_RESULT.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))

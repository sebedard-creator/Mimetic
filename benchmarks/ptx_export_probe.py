"""Demonstrate two Mimetic renders placed in a PTX, without running Pro Tools.

Uses synthetic audio and a known room, not production dialogue or Rec-RIR.
Requires a pt_api checkout and a compatible template with two empty mono tracks.
This is a feasibility probe, not the application's PTX import/export feature.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mimetic import pipeline  # noqa: E402
from mimetic.audio import io  # noqa: E402
from mimetic.domain.models import RenderSettings  # noqa: E402
from mimetic.engines import known_ir  # noqa: E402


def _chunk(name: bytes, payload: bytes) -> bytes:
    return name + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) % 2 else b"")


def write_probe_bwf(path: Path, audio: np.ndarray, start: int, *,
                    description: str = "Mimetic synthetic PTX placement probe") -> None:
    """Write a mono float32 WAVE_EXTENSIBLE BWF matching pt_api's builder contract.

    The probe generates a nonzero synthetic basic UMID with UUID material.
    Production integration must separately validate its UMID allocation policy.
    """
    audio = np.asarray(audio, dtype="<f4").ravel()
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, 1, 48000, 192000, 4, 32, 22, 32, 4)
    fmt += bytes.fromhex("03000000 0000 1000 8000 00aa00389b71")
    bext = bytearray(602)
    description_bytes = description.encode("ascii")[:256]
    bext[:len(description_bytes)] = description_bytes
    bext[256:263] = b"Mimetic"
    struct.pack_into("<QH", bext, 338, start, 1)
    bext[348:380] = bytes.fromhex("060a2b340101010501010f1013000000") + uuid.uuid4().bytes
    body = b"WAVE" + _chunk(b"fmt ", fmt)
    body += _chunk(b"fact", struct.pack("<I", audio.size))
    body += _chunk(b"bext", bytes(bext)) + _chunk(b"data", audio.tobytes())
    with path.open("xb") as stream:
        stream.write(b"RIFF" + struct.pack("<I", len(body)) + body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-root", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New directory; existing output is refused")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("--out must name a new directory")
    sys.path.insert(0, str(args.api_root.resolve()))
    from pt_api import ProToolsSession, TimecodeEngine, build_audio_session

    template = ProToolsSession(str(args.template))
    status = template.validate_audio_import_template()
    if len(status["tracks"]) < 2:
        parser.error("The probe needs two existing empty template tracks")
    tracks = status["tracks"][:2]
    engine = TimecodeEngine(48000, 0x09)
    # Deliberately between frame boundaries: placement must not be rounded via timecode text.
    start = engine.timecode_to_samples(1, 2, 3, 12) + 137
    args.out.mkdir(parents=True)
    fs = 48000
    t = np.arange(fs * 2) / fs
    x = 0.12 * np.sin(2 * np.pi * 220 * t) * np.sin(np.pi * t / 2) ** 2
    source = args.out / "SYNTHETIC_ADR.wav"
    sf.write(source, x, fs, subtype="FLOAT")
    adr = io.load_wav(source)
    h = np.zeros(12001)
    h[0], h[480], h[2400], h[12000] = 1.0, 0.3, -0.12, 0.06
    profile = known_ir.build_profile(h, fs, source_name="synthetic-room", source_sha256="probe", convention="total")
    rendered = pipeline.render(adr, "mono", profile, RenderSettings())
    signals = {"IR_ONLY": rendered.wet, "MIX": rendered.dry + rendered.wet}
    specs = []
    for track_name, (kind, signal) in zip(tracks, signals.items()):
        path = args.out / f"SYNTHETIC_{kind}.wav"
        write_probe_bwf(path, signal, start)
        specs.append({"audio_path": str(path), "track_name": track_name,
                      "clip_name": f"MIMETIC_{kind}", "placement_start_samples": start})
    bundle = build_audio_session(str(args.template), specs, str(args.out / "Session"), session_name="Mimetic_PTX_probe")
    session = ProToolsSession(bundle["session_path"])
    # These are alternative deliverables: auditioning both would duplicate the wet.
    if session.mute_clip("MIMETIC_IR_ONLY") != 1:
        raise AssertionError("Expected one wet-only placement to mute")
    session.save(bundle["session_path"])
    session = ProToolsSession(bundle["session_path"])
    timeline = session.get_timeline_clips(include_fades=False)
    if len(timeline) != 2:
        raise AssertionError("Expected two output placements")
    expected = {item["clip_name"]: item for item in specs}
    for event in timeline:
        if event["start_samples"] != start or event["length_samples"] != rendered.wet.size:
            raise AssertionError("Output placement or tail length changed")
        spec = expected[event["clip_name"]]
        if event["track"] != spec["track_name"]:
            raise AssertionError("Output track mapping changed")
        if event["muted"] != (event["clip_name"] == "MIMETIC_IR_ONLY"):
            raise AssertionError("Alternative wet-only output should be muted")
        original = Path(spec["audio_path"]).read_bytes()
        copied = (Path(bundle["audio_files_directory"]) / event["physical_filename"]).read_bytes()
        if original != copied:
            raise AssertionError("Builder changed WAV bytes")
        actual, actual_fs = sf.read(Path(bundle["audio_files_directory"]) / event["physical_filename"], dtype="float32")
        kind = event["clip_name"].removeprefix("MIMETIC_")
        if actual_fs != fs or not np.array_equal(actual, signals[kind].astype(np.float32)):
            raise AssertionError("Packaged audio differs from Mimetic render")
    resave = args.out / "resaved.ptx"
    session.save(str(resave))
    if Path(bundle["session_path"]).read_bytes() != resave.read_bytes():
        raise AssertionError("No-op API save was not byte identical")
    report = {
        "status": "API round-trip verified; not opened in Pro Tools",
        "synthetic_audio": True, "mix_eq": False,
        "note": "MIX is a routing/placement probe; it has no EQ and is not an EQ_IR_MIX export.",
        "template_profile": status["profile"],
        "template_sha256": hashlib.sha256(args.template.read_bytes()).hexdigest(),
        "session_path": bundle["session_path"], "sample_rate": fs, "frame_rate_enum": 9,
        "start_samples": start, "display_timecode": engine.samples_to_timecode(start),
        "subframe_offset_samples": 137, "timeline": timeline,
    }
    (args.out / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

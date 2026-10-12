"""Offline preliminary PTX render: paired mono reference/ADR tracks, original placements.

This exports real Mimetic audio. Input track processing/automation is not bounced.
The input must have no fades/groups and uniquely paired start positions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks"))
from ptx_export_probe import write_probe_bwf
from mimetic import pipeline
from mimetic.analysis import eq_match
from mimetic.audio import io
from mimetic.domain.errors import MimeticError
from mimetic.domain.models import RenderSettings
from mimetic.engines import parametric_manual
from mimetic.engines.recrir import adapter


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, obj):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(io.json_safe(obj), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_region(session_path, row):
    path = session_path.parent / "Audio Files" / row["physical_filename"]
    with sf.SoundFile(path) as wav:
        if wav.samplerate != 48000 or wav.channels != 1:
            raise ValueError("This preliminary renderer requires mono 48 kHz media")
        offset, length = row["src_offset_samples"], row["length_samples"]
        if not 0 <= offset < offset + length <= len(wav):
            raise ValueError("Region exceeds media bounds")
        wav.seek(offset)
        x = wav.read(length, dtype="float64")
    if not np.isfinite(x).all():
        raise ValueError("Non-finite input audio")
    return x, path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-root", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.api_root.resolve()))
    from pt_api import ProToolsSession
    session_path = args.session.resolve()
    session_hash = digest(session_path)
    session = ProToolsSession(str(session_path))
    if session.sample_rate != 48000 or session.frame_rate_enum != 9:
        raise ValueError("The current export profile is 48 kHz / 23.976")
    tracks = session.get_tracks()
    if len(tracks) % 2 or len(set(tracks)) != len(tracks):
        raise ValueError("Expected uniquely named pairs of mono tracks")
    timeline = session.get_timeline_clips()
    if any(row["is_fade"] for row in timeline) or session.get_timeline_clip_groups():
        raise ValueError("This test requires consolidated clips without fades or groups")
    audio = [row for row in timeline if not row["is_fade"]]
    units = []
    for pair, i in enumerate(range(0, len(tracks), 2), 1):
        refs = [row for row in audio if row["track"] == tracks[i]]
        destinations = [row for row in audio if row["track"] == tracks[i+1]]
        if len(refs) != len(destinations) or len({row["start_samples"] for row in refs}) != len(refs):
            raise ValueError("Ambiguous reference-to-ADR pairing")
        refs_by_start = {row["start_samples"]: row for row in refs}
        for index, dst in enumerate(destinations, 1):
            if dst["muted"]:
                raise ValueError("Muted input placements need an explicit policy")
            ref = refs_by_start[dst["start_samples"]]
            if ref["muted"]:
                raise ValueError("Reference placement is muted")
            units.append((pair, index, ref, dst))
    args.out.mkdir(parents=True, exist_ok=True)
    work = args.out / "Work"
    media = args.out / "Rendered_Audio"
    work.mkdir(exist_ok=True)
    media.mkdir(exist_ok=True)
    manifest_path = args.out / "render_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["source_session_sha256"] != session_hash:
            raise ValueError("Resume session does not match the checkpoint")
    else:
        manifest = {"source_session": str(session_path), "source_session_sha256": session_hash,
                    "input_tracks": tracks, "status": "rendering", "units": [],
                    "settings": {"wet_gain_db": 0.0, "predelay_ms": 0.0, "eq_amount": 1.0},
                    "input_audio_mode": "media samples; no Clip Gain/Track Volume/plugin bounce",
                    "fallback_policy": "short reference attempted; invalid room uses explicit conservative synthetic fallback; unavailable EQ uses identity"}
    save_json(manifest_path, manifest)
    done = {u["id"] for u in manifest["units"]}
    client = adapter.WorkerClient(ROOT)
    profiles = {}
    settings = RenderSettings()
    previous_minimum = adapter.MIN_WINDOW_SECONDS
    # Only this offline process changes the selection gate; no installed application file changes.
    adapter.MIN_WINDOW_SECONDS = 0.032
    try:
        for pair, index, ref_row, dst_row in units:
            ident = f"P{pair:02d}_R{index:02d}"
            if ident in done:
                print(f"{ident} checkpoint already complete", flush=True)
                continue
            started = time.perf_counter()
            ref, ref_path = load_region(session_path, ref_row)
            x, dst_path = load_region(session_path, dst_row)
            print(f"{ident} reference {len(ref)/48000:.2f}s, ADR {len(x)/48000:.2f}s", flush=True)
            notes = []
            ref_key = hashlib.sha256(ref.tobytes()).hexdigest()
            short = len(ref) < 2*48000
            if short:
                notes.append("SHORT_REFERENCE_LOW_CONFIDENCE")
            if ref_key in profiles:
                profile = profiles[ref_key]
            else:
                try:
                    profile = adapter.estimate_profile(ref, 48000, workdir=work/ident/"engine", client=client,
                        reference_meta={"name": ref_row["clip_name"], "track": ref_row["track"],
                                        "start_samples": ref_row["start_samples"], "original_length_samples":len(ref),
                                        "short_reference_allowed":short},
                        progress=lambda message: print(f"{ident} {message}", flush=True))
                    if "DIRECT_PATH_UNRESOLVED" in profile.quality_reasons:
                        raise MimeticError("DIRECT_PATH_UNRESOLVED", "estimated direct path is unusable")
                except MimeticError as exc:
                    notes.append(f"ROOM_SYNTHETIC_FALLBACK:{exc.code}")
                    profile = parametric_manual.build_profile(parametric_manual.ManualParams(
                        rt60_s=0.3, drr_db=12.0, seed=2026+pair*100+index))
                    profile.parameters["fallback_reason"] = exc.to_dict()
                if short:
                    profile.quality_reasons.append("SHORT_REFERENCE_LOW_CONFIDENCE")
                profiles[ref_key] = profile
            adr_path = work/ident/"adr.wav"
            adr_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(adr_path, x, 48000, subtype="FLOAT")
            adr = io.load_wav(adr_path, original_name=dst_row["clip_name"]+".wav")
            try:
                eq = eq_match.build_profile(ref, 48000, x, 48000, profile, settings)
                eq_status = "estimated"
            except MimeticError as exc:
                eq = None
                eq_status = "identity_fallback"
                notes.append(f"EQ_IDENTITY_FALLBACK:{exc.code}")
            rendered = pipeline.render(adr, "mono", profile, settings, eq)
            if eq is not None and eq["filter"].get("identity"):
                eq_status = "estimated_identity"
            h, h_info = pipeline.profile_ir_and_info(profile, 48000)
            np.save(work/ident/"room_wet_48k.npy", h, allow_pickle=False)
            outputs = {}
            for kind, signal in [("IR_ONLY", rendered.wet), ("EQ_IR_MIX", rendered.dry+rendered.wet)]:
                wav_path = media/f"{ident}_{kind}.wav"
                if wav_path.exists():
                    raise FileExistsError(f"Partial output exists: {wav_path}; no overwrite performed")
                write_probe_bwf(wav_path, signal, dst_row["start_samples"],
                                description=f"Mimetic preliminary real ADR render {ident} {kind}")
                actual, actual_fs = sf.read(wav_path, dtype="float32")
                if actual_fs != 48000 or not np.array_equal(actual, signal.astype(np.float32)):
                    raise AssertionError("Output BWF differs from rendered audio")
                outputs[kind] = {"audio_path":str(wav_path.resolve()), "length_samples":signal.size,
                                 "sha256":digest(wav_path), "peak":float(np.abs(signal).max())}
            manifest["units"].append({"id":ident,"pair":pair,"region":index,
                "reference":ref_row,"destination":dst_row,"placement_start_samples":dst_row["start_samples"],
                "reference_media_sha256":digest(ref_path),"destination_media_sha256":digest(dst_path),
                "room":profile.manifest(),"room_method":profile.engine_id,"eq_status":eq_status,
                "eq":rendered.eq_info,"warnings":sorted(set(notes+rendered.warnings)),
                "outputs":outputs,"elapsed_seconds":round(time.perf_counter()-started,3)})
            save_json(manifest_path, manifest)
            print(f"{ident} COMPLETE room={profile.engine_id} eq={eq_status} warnings={notes}", flush=True)
        if digest(session_path) != session_hash:
            raise AssertionError("Input session changed during rendering; outputs require review")
        manifest["status"] = "renders_complete"
        save_json(manifest_path, manifest)
    finally:
        adapter.MIN_WINDOW_SECONDS = previous_minimum
        client.stop()
    print(f"COMPLETE {len(manifest['units'])} ADR renders: {manifest_path}", flush=True)


if __name__ == "__main__":
    main()

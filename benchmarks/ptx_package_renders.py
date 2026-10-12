"""Package completed offline Mimetic renders in supported template-based PTX sessions."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))
from ptx_export_probe import write_probe_bwf


def enable_test05_originator_profile(api_root, template_path):
    """Bounded, process-only compatibility for the user's native test template.

    Its 0x2106 originator is a seven-byte length-prefixed string. After
    removing that string, every byte outside the known variable fields agrees
    with the verified 142-byte native header. Native opening remains required.
    This does not alter pt_api or accept arbitrary new binary layouts.
    """
    import pt_api
    from pt_api import PTBlock, ProToolsSession
    expected_hash = "68b7339b601e9a425aabffe5f5e78c3e7fae93d58fb258a22c32ab6c54e3bfd3"
    if pt_api.__version__ != "1.6.3" or hashlib.sha256(template_path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError("Originator compatibility is limited to the inspected test-05 template and pt_api 1.6.3")
    reference = api_root / "pfx_import_reference/01_A_imported_cliplist/01_A_imported_cliplist.ptx"
    if hashlib.sha256(reference.read_bytes()).hexdigest() != "1fba4fdc2873e6d25bf1f37f173f16ae033c7c96bae5f8fd62c4063b2ce85661":
        raise ValueError("Native comparison fixture changed")

    def header(path):
        session = ProToolsSession(str(path))
        entries = session._validated_physical_audio_catalog()[1]
        if len(entries) != 1:
            raise ValueError("Expected one native prototype medium")
        detail = next(b for b in entries[0].items if isinstance(b, PTBlock) and b.content_type == 0x2106)
        if len(detail.items) != 3 or not isinstance(detail.items[0], (bytes, bytearray)):
            raise ValueError("Unexpected native detail structure")
        return bytes(detail.items[0])

    actual, known = header(template_path), header(reference)
    if len(actual) != 149 or len(known) != 142 or actual[48:60] != b"\x01\x07\x00\x00\x00Mimetic":
        raise ValueError("Unexpected native originator encoding")
    normalized = actual[:49] + bytes(4) + actual[60:]
    variable = set(range(29, 37)) | set(range(39, 43)) | set(range(91, 95)) | set(range(96, 104)) | set(range(126, 142))
    if any(normalized[i] != known[i] for i in range(142) if i not in variable):
        raise ValueError("Unexplained native header differences")
    base = next(p for p in pt_api._AUDIO_IMPORT_TEMPLATE_PROFILES if p["name"] == "native_float_15_142")
    profile = dict(base, name="test05_native_float_15_149_mimetic_originator", detail_header_length=149)
    for field in ("detail_time_reference_offset", "detail_previous_filetime_offset", "detail_uuid_offset"):
        profile[field] += 7
    pt_api._AUDIO_IMPORT_TEMPLATE_PROFILES = (profile,) + tuple(
        p for p in pt_api._AUDIO_IMPORT_TEMPLATE_PROFILES if p["name"] != base["name"])
    # The standard validator still checks clip/media shape, length and timing.
    ProToolsSession(str(template_path)).validate_audio_import_template()
    return {"profile":profile["name"], "template_sha256":expected_hash,
            "scope":"offline test-05 process only; pt_api checkout unchanged",
            "evidence":"native originator length 7; normalized fixed header matches verified fixture",
            "native_validation":"Pro Tools opening and native save pending"}


def island_groups(units):
    groups = []
    for unit in sorted(units, key=lambda u: u["placement_start_samples"]):
        start = unit["placement_start_samples"]
        end = start + max(o["length_samples"] for o in unit["outputs"].values())
        if groups and start <= groups[-1]["end"]:
            groups[-1]["units"].append(unit)
            groups[-1]["end"] = max(groups[-1]["end"], end)
        else:
            groups.append({"start":start,"end":end,"units":[unit]})
    return groups


def read_static_clip_gain(session, name):
    """Inspect the exact bounded gain dictionary/index profile used by pt_api's writer."""
    from pt_api import PTBlock
    clip_id = session._resolve_unique_clip_id(name)
    definitions = [b for b in session._root_blocks(0x262a)[0].items
                   if isinstance(b, PTBlock) and b.content_type == 0x2629]
    payload = next(b for b in definitions[clip_id].items
                   if isinstance(b, PTBlock) and b.content_type == 0x2628).items[0]
    index = struct.unpack_from("<i", payload, len(payload)-6)[0]
    if index == -1:
        return 0.0
    _, _, records = session._validated_clip_gain_dictionary()
    if not 0 <= index < len(records) or len(records[index][1]) != 30:
        raise ValueError("Expected a supported static Clip Gain point")
    return struct.unpack_from("<f", records[index][1], 26)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--separate-pairs", action="store_true")
    parser.add_argument("--mute-kind", choices=["IR_ONLY", "EQ_IR_MIX"], default="EQ_IR_MIX")
    parser.add_argument("--ir-clip-gain-db", type=float, default=3.5)
    parser.add_argument("--keep-unused-tracks", action="store_true")
    parser.add_argument("--mimetic-originator-compat", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.api_root.resolve()))
    from pt_api import ProToolsSession, build_audio_session
    compatibility = enable_test05_originator_profile(args.api_root, args.template) if args.mimetic_originator_compat else None
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest["status"] != "renders_complete":
        raise ValueError("Rendering is not complete")
    pairs = sorted({u["pair"] for u in manifest["units"]})
    scopes = [[p] for p in pairs] if args.separate_pairs else [pairs]
    template_probe = ProToolsSession(str(args.template))
    slots = template_probe.validate_audio_import_template()["tracks"]
    # Fail before creating a package if native Clip Gain metadata is unavailable.
    template_probe._validated_clip_gain_dictionary()
    if len(slots) < max(len(scope)*2 for scope in scopes):
        raise ValueError("Template has insufficient existing tracks")
    if args.out.exists():
        raise FileExistsError("Package directory already exists")
    args.out.mkdir(parents=True)
    work = args.out / "Package_Work"
    work.mkdir()
    reports = []
    for scope in scopes:
        suffix = "_".join(f"P{p:02d}" for p in scope)
        template = ProToolsSession(str(args.template))
        names = []
        for pair in scope:
            names.extend([f"MIMETIC_IR_P{pair:02d}",f"MIMETIC_MIX_P{pair:02d}"])
        for old,new in zip(slots,names):
            template.rename_track(old,new)
        if len(slots) > len(names) and not args.keep_unused_tracks:
            template.set_visible_tracks(names)
        prepared_template = work/f"template_{suffix}.ptx"
        template.save(str(prepared_template))
        ProToolsSession(str(prepared_template)).validate_audio_import_template()
        specs = []
        checks = []
        mute_names = []
        for pair in scope:
            units = [u for u in manifest["units"] if u["pair"]==pair]
            for number,island in enumerate(island_groups(units),1):
                for kind,track_name in [("IR_ONLY",f"MIMETIC_IR_P{pair:02d}"),("EQ_IR_MIX",f"MIMETIC_MIX_P{pair:02d}")]:
                    summed = np.zeros(island["end"]-island["start"],dtype=np.float64)
                    for unit in island["units"]:
                        signal,fs=sf.read(unit["outputs"][kind]["audio_path"],dtype="float64")
                        if fs!=48000 or signal.ndim!=1:
                            raise ValueError("Expected mono 48 kHz renders")
                        offset=unit["placement_start_samples"]-island["start"]
                        summed[offset:offset+len(signal)]+=signal
                    name=f"P{pair:02d}_I{number:02d}_{kind}"
                    wav=work/f"{name}.wav"
                    write_probe_bwf(wav,summed,island["start"],description=f"Mimetic real ADR preliminary {name}")
                    specs.append({"audio_path":str(wav.resolve()),"track_name":track_name,
                                  "clip_name":name,"placement_start_samples":island["start"]})
                    checks.append({"clip_name":name,"track":track_name,"start_samples":island["start"],
                                   "length_samples":len(summed),"units":[u["id"] for u in island["units"]],
                                   "peak":float(np.abs(summed).max()),"over_0dbfs":bool(np.abs(summed).max()>1.0),
                                   "muted":kind==args.mute_kind,
                                   "clip_gain_db":args.ir_clip_gain_db if kind=="IR_ONLY" else 0.0})
                    if kind==args.mute_kind:mute_names.append(name)
        dest=args.out/f"pasmorte_Mimetic_{suffix}"
        result=build_audio_session(str(prepared_template),specs,str(dest),session_name=f"pasmorte_Mimetic_{suffix}")
        output_session=ProToolsSession(result["session_path"])
        for check in checks:
            if output_session.mute_clip(check["clip_name"], mute=check["muted"]) != 1:
                raise AssertionError("Expected one output placement")
            output_session.set_clip_gain(check["clip_name"], check["clip_gain_db"])
        output_session.save(result["session_path"])
        reread=ProToolsSession(result["session_path"])
        rows=reread.get_timeline_clips()
        expected={c["clip_name"]:c for c in checks}
        if len(rows)!=len(expected):raise AssertionError("Output placement count mismatch")
        for row in rows:
            want=expected[row["clip_name"]]
            if (row["track"],row["start_samples"],row["length_samples"],row["muted"]) != (
                    want["track"],want["start_samples"],want["length_samples"],row["clip_name"] in mute_names):
                raise AssertionError("Output placement or mute mismatch")
            if abs(read_static_clip_gain(reread, row["clip_name"]) - want["clip_gain_db"]) > 1e-6:
                raise AssertionError("Output Clip Gain mismatch")
            generated=work/f"{row['clip_name']}.wav"
            copied=Path(result["audio_files_directory"])/row["physical_filename"]
            if generated.read_bytes()!=copied.read_bytes():raise AssertionError("WAV bytes changed")
        cycle=work/f"{suffix}_API_cycle.ptx"
        reread.save(str(cycle))
        if Path(result["session_path"]).read_bytes()!=cycle.read_bytes():raise AssertionError("No-op cycle changed bytes")
        report={"session_path":result["session_path"],"pairs":scope,"tracks":names,
                "template_compatibility":compatibility,
                "all_template_tracks":reread.get_tracks(),"unused_tracks_kept":args.keep_unused_tracks,
                "mute_kind":args.mute_kind,"ir_clip_gain_db":args.ir_clip_gain_db,
                "input_ADR_units":sum(u["pair"] in scope for u in manifest["units"]),
                "placements":checks,"verified_timeline":rows,
                "verification":"API reload, exact sample placement, wave bytes, mute, static Clip Gain and no-op cycle verified; native opening pending"}
        (dest/"verification_export.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        reports.append(report)
    (args.out/"package_manifest.json").write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps([{k:r[k] for k in ["session_path","pairs","tracks","input_ADR_units"]} for r in reports],ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()

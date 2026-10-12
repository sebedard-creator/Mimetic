"""PTX v1.5: native paired mono sessions, full ADR tails and self-contained exports.

The bundled template and its originator layout were opened/saved in Pro Tools
by the user on 2026-10-12. The API compatibility module is isolated: no global
pt_api registry or installed source is changed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import sys
import uuid
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf

from mimetic import __version__, pipeline
from mimetic.analysis import eq_match
from mimetic.audio import io
from mimetic.domain.errors import MimeticError
from mimetic.domain.models import MAX_FILE_SECONDS, RenderSettings
from mimetic.engines import parametric_manual

TEMPLATE = Path(__file__).parent / "resources" / "Mimetic_Template.ptx"
TEMPLATE_SHA256 = "68b7339b601e9a425aabffe5f5e78c3e7fae93d58fb258a22c32ab6c54e3bfd3"
TEMPLATE_AUDIO = TEMPLATE.parent / "Audio Files" / "Mimetic_Prototype_Float32_TC_60s.wav"
TEMPLATE_AUDIO_SHA256 = "26f2a271b36a707d55e6e44cc875885afe1033f4efc505b6d1a1675b494462c9"
API_SHA256 = "a160557878de7920a46f3d8e280f3e1e7100fad2555cd8f718de2995e10f832e"


@lru_cache(maxsize=1)
def api():
    try:
        import pt_api
    except ImportError as exc:
        raise MimeticError("PTX_UNAVAILABLE", "Installer les dépendances v1.5 avec setup.cmd.") from exc
    source_hash = hashlib.sha256(Path(pt_api.__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if pt_api.__version__ != "1.6.3" or source_hash != API_SHA256:
        raise MimeticError("PTX_UNAVAILABLE", "Version pt_api différente du profil validé (1.6.3).")
    if not TEMPLATE.is_file() or io.sha256_file(TEMPLATE) != TEMPLATE_SHA256:
        raise MimeticError("PTX_UNAVAILABLE", "Template natif absent ou modifié.")
    if not TEMPLATE_AUDIO.is_file() or io.sha256_file(TEMPLATE_AUDIO) != TEMPLATE_AUDIO_SHA256:
        raise MimeticError("PTX_UNAVAILABLE", "WAV prototype du template absent ou modifié.")
    name = "mimetic._pt_api_native"
    spec = importlib.util.spec_from_file_location(name, pt_api.__file__)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    base = next(p for p in module._AUDIO_IMPORT_TEMPLATE_PROFILES if p["name"] == "native_float_15_142")
    native = dict(base, name="mimetic_native_float_15_149", detail_header_length=149,
                  detail_time_reference_offset=98, detail_previous_filetime_offset=103, detail_uuid_offset=133)
    module._AUDIO_IMPORT_TEMPLATE_PROFILES = (native,) + tuple(
        p for p in module._AUDIO_IMPORT_TEMPLATE_PROFILES if p["name"] != base["name"])
    session = module.ProToolsSession(str(TEMPLATE))
    session.validate_audio_import_template()
    session._validated_clip_gain_dictionary()
    metadata = module._inspect_template_audio_wave(str(TEMPLATE_AUDIO))
    entries = session._validated_physical_audio_catalog()[1]
    detail = next(b for b in entries[0].items if isinstance(b, module.PTBlock) and b.content_type == 0x2106)
    if (metadata["length_samples"] != 2880000 or metadata["bwf_time_reference"] != struct.unpack_from("<I", detail.items[0], 98)[0]
            or metadata["umid"][:32] != bytes(detail.items[2][18:50])):
        raise MimeticError("PTX_UNAVAILABLE", "Le prototype audio ne correspond pas au template natif.")
    return module


def capabilities():
    try:
        api()
        return {"available": True, "max_pairs": 3, "sample_rate_hz": 48000, "frame_rate": "23.976"}
    except MimeticError as exc:
        return {"available": False, "detail": exc.detail}


def read_plan(path: Path) -> dict:
    """Only unambiguous sample-start pairing; names never assign the roles."""
    try:
        session = api().ProToolsSession(str(path))
        if session.sample_rate != 48000 or session.frame_rate_enum != 9:
            raise MimeticError("PTX_UNSUPPORTED", "Session requise : 48 kHz / 23,976 images/s.")
        tracks = session.get_tracks()
        if not tracks or len(tracks) % 2 or len(set(tracks)) != len(tracks):
            raise MimeticError("PTX_UNSUPPORTED", "Nombre pair de pistes audio, noms uniques requis.")
        if len(tracks) > 6:
            raise MimeticError("PTX_UNSUPPORTED", "Trois couples maximum pour le template v1.5.")
        rows = session.get_timeline_clips(include_fades=True)
        if any(r["is_fade"] for r in rows) or session.get_timeline_clip_groups():
            raise MimeticError("PTX_UNSUPPORTED", "Consolider les clips avec leurs fades et groupes avant export.")
        units, pairs = [], []
        for i in range(0, len(tracks), 2):
            pair = i // 2 + 1
            refs = [r for r in rows if r["track"] == tracks[i]]
            destinations = sorted((r for r in rows if r["track"] == tracks[i+1]), key=lambda r: r["start_samples"])
            if not refs and not destinations:
                continue
            ref_map = {r["start_samples"]: r for r in refs}
            dst_starts = [r["start_samples"] for r in destinations]
            if (len(ref_map) != len(refs) or len(set(dst_starts)) != len(destinations)
                    or set(ref_map) != set(dst_starts)):
                raise MimeticError("PTX_PAIRING", f"Couple {pair} : chaque ADR doit avoir une référence au même début exact.")
            pairs.append({"pair": pair, "source": tracks[i], "destination": tracks[i+1], "clips": len(destinations)})
            for number, dst in enumerate(destinations, 1):
                ref = ref_map[dst["start_samples"]]
                if ref["muted"] or dst["muted"]:
                    raise MimeticError("PTX_UNSUPPORTED", "Activer les clips d'entrée à traiter avant export.")
                for row in (ref, dst):
                    physical = row["physical_filename"]
                    if not physical or Path(physical).name != physical or any(c in physical for c in "\\/:"):
                        raise MimeticError("PTX_UNSUPPORTED", "Média physique WAV non résolu.")
                    if row["length_samples"] <= 0 or row["length_samples"] > MAX_FILE_SECONDS * 48000:
                        raise MimeticError("LIMIT_EXCEEDED", "Chaque région doit être non vide et durer au maximum 10 min.")
                units.append({"id": f"P{pair:02d}_R{number:02d}", "pair": pair, "reference": ref,
                              "destination": dst, "placement_start_samples": dst["start_samples"]})
        if not units:
            raise MimeticError("PTX_UNSUPPORTED", "Aucun couple audio à traiter.")
        media = sorted({r[role]["physical_filename"] for r in units for role in ("reference", "destination")})
        if len({name.casefold() for name in media}) != len(media):
            raise MimeticError("PTX_UNSUPPORTED", "Noms de médias identiques à la casse près.")
        return {"tracks": tracks, "pairs": pairs, "units": units, "media": media,
                "session_sha256": io.sha256_file(path)}
    except MimeticError:
        raise
    except (ValueError, NotImplementedError, KeyError, IndexError, struct.error, OSError) as exc:
        raise MimeticError("PTX_UNSUPPORTED", str(exc)) from exc


def validate_media(plan: dict, media: dict[str, Path]):
    for name in plan["media"]:
        path = media.get(name.casefold())
        if path is None:
            raise MimeticError("PTX_MEDIA_MISSING", name)
        try:
            info = sf.info(path)
        except Exception as exc:
            raise MimeticError("INVALID_AUDIO", name) from exc
        if info.format not in ("WAV", "WAVEX") or info.samplerate != 48000 or info.channels != 1 or info.subtype not in ("PCM_16", "PCM_24", "FLOAT"):
            raise MimeticError("PTX_UNSUPPORTED", f"{name} : WAV mono 48 kHz requis.")
        for unit in plan["units"]:
            for role in ("reference", "destination"):
                row = unit[role]
                if row["physical_filename"].casefold() == name.casefold() and not (
                        0 <= row["src_offset_samples"] < row["src_offset_samples"] + row["length_samples"] <= info.frames):
                    raise MimeticError("INVALID_AUDIO", f"{name} : région hors des limites du média.")


def load_region(row: dict, media: dict[str, Path]) -> np.ndarray:
    with sf.SoundFile(media[row["physical_filename"].casefold()]) as wav:
        wav.seek(row["src_offset_samples"])
        x = wav.read(row["length_samples"], dtype="float64")
    if len(x) != row["length_samples"] or not np.isfinite(x).all():
        raise MimeticError("INVALID_AUDIO", row["physical_filename"])
    return x


def _chunk(tag, body):
    return tag + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"")


def write_bwf(path: Path, signal: np.ndarray, start: int):
    signal = np.asarray(signal, dtype="<f4").ravel()
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, 1, 48000, 192000, 4, 32, 22, 32, 4)
    fmt += bytes.fromhex("03000000 0000 1000 8000 00aa00389b71")
    bext = bytearray(602)
    description = b"Mimetic real ADR render"
    bext[:len(description)] = description
    bext[256:263] = b"Mimetic"
    struct.pack_into("<QH", bext, 338, start, 1)
    bext[348:380] = bytes.fromhex("060a2b340101010501010f1013000000") + uuid.uuid4().bytes
    body = b"WAVE" + _chunk(b"fmt ", fmt) + _chunk(b"fact", struct.pack("<I", signal.size))
    body += _chunk(b"bext", bytes(bext)) + _chunk(b"data", signal.tobytes())
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def render_units(plan, media, work: Path, *, estimator, client, progress, check_cancel):
    units, profiles = [], {}
    settings = RenderSettings()
    for index, item in enumerate(plan["units"], 1):
        check_cancel()
        ident = item["id"]
        suffix = f"ADR {index}/{len(plan['units'])} · {ident}"
        progress(f"Analyse de la pièce — {suffix}", index - 1)
        ref, adr = load_region(item["reference"], media), load_region(item["destination"], media)
        warnings = []
        if len(ref) < 2 * 48000:
            warnings.append("SHORT_REFERENCE_LOW_CONFIDENCE")
        key = hashlib.sha256(ref.tobytes()).hexdigest()
        if key not in profiles:
            try:
                profile = estimator(ref, 48000, workdir=work / ident / "engine", client=client,
                                    reference_meta={"name":item["reference"]["clip_name"], "asset_sha256":key,
                                                    "track":item["reference"]["track"], "short_reference_allowed":True},
                                    progress=lambda step: progress(f"{step} — {suffix}", index - 1))
                if "DIRECT_PATH_UNRESOLVED" in profile.quality_reasons:
                    raise MimeticError("DIRECT_PATH_UNRESOLVED")
            except MimeticError as exc:
                check_cancel()
                if exc.code not in ("DIRECT_PATH_UNRESOLVED", "INSUFFICIENT_SPEECH", "EMPTY_REFERENCE"):
                    raise
                profile = parametric_manual.build_profile(parametric_manual.ManualParams(
                    rt60_s=0.3, drr_db=12.0, seed=2026 + index))
                profile.parameters["fallback_reason"] = exc.to_dict()
                warnings.append(f"ROOM_SYNTHETIC_FALLBACK:{exc.code}")
            profiles[key] = profile
        profile = profiles[key]
        if profile.parameters.get("fallback_reason") and not any(w.startswith("ROOM_SYNTHETIC_FALLBACK") for w in warnings):
            warnings.append("ROOM_SYNTHETIC_FALLBACK:" + profile.parameters["fallback_reason"]["code"])
        check_cancel()
        progress(f"Match EQ — {suffix}", index - 1)
        try:
            eq = eq_match.build_profile(ref, 48000, adr, 48000, profile, settings)
            eq_status = "estimated_identity" if eq["filter"].get("identity") else "estimated"
        except MimeticError as exc:
            if not exc.code.startswith("EQ_"):
                raise
            eq, eq_status = None, "identity_fallback"
            warnings.append(f"EQ_IDENTITY_FALLBACK:{exc.code}")
        check_cancel()
        progress(f"Rendu — {suffix}", index - 1)
        adr_path = work / f"{ident}_ADR.wav"
        sf.write(adr_path, adr, 48000, subtype="FLOAT")
        asset = io.load_wav(adr_path, item["destination"]["clip_name"] + ".wav")
        rendered = pipeline.render(asset, "mono", profile, settings, eq)
        outputs = {}
        for kind, signal in (("IR_ONLY", rendered.wet), ("EQ_IR_MIX", rendered.dry + rendered.wet)):
            path = work / f"{ident}_{kind}.wav"
            write_bwf(path, signal, item["placement_start_samples"])
            outputs[kind] = {"path":str(path), "length_samples":len(signal)}
        units.append({**item, "outputs":outputs, "room":profile.manifest(), "eq_status":eq_status,
                      "eq":rendered.eq_info, "warnings":sorted(set(warnings + rendered.warnings))})
        progress(f"ADR rendu — {suffix}", index)
    return units


def island_groups(units):
    groups = []
    for unit in sorted(units, key=lambda u: u["placement_start_samples"]):
        start = unit["placement_start_samples"]
        end = start + max(o["length_samples"] for o in unit["outputs"].values())
        if groups and start <= groups[-1]["end"]:
            groups[-1]["units"].append(unit)
            groups[-1]["end"] = max(groups[-1]["end"], end)
        else:
            groups.append({"start":start, "end":end, "units":[unit]})
    return groups


def clip_gain(session, name):
    mod = api()
    clip_id = session._resolve_unique_clip_id(name)
    defs = [b for b in session._root_blocks(0x262a)[0].items if isinstance(b, mod.PTBlock) and b.content_type == 0x2629]
    payload = next(b for b in defs[clip_id].items if isinstance(b, mod.PTBlock) and b.content_type == 0x2628).items[0]
    index = struct.unpack_from("<i", payload, len(payload) - 6)[0]
    if index == -1:
        return 0.0
    records = session._validated_clip_gain_dictionary()[2]
    if not 0 <= index < len(records) or len(records[index][1]) != 30:
        raise MimeticError("PTX_EXPORT_FAILED", "Gain de clip statique non vérifié.")
    return struct.unpack_from("<f", records[index][1], 26)[0]


def package_units(units, work: Path, destination: Path, session_name: str, check_cancel):
    mod = api()
    specs, checks = [], []
    for pair in sorted({u["pair"] for u in units}):
        for number, island in enumerate(island_groups([u for u in units if u["pair"] == pair]), 1):
            for kind, track in (("IR_ONLY",f"MIMETIC_IR_P{pair:02d}"), ("EQ_IR_MIX",f"MIMETIC_MIX_P{pair:02d}")):
                check_cancel()
                summed = np.zeros(island["end"] - island["start"], dtype=np.float64)
                for unit in island["units"]:
                    signal, fs = sf.read(unit["outputs"][kind]["path"], dtype="float64")
                    if fs != 48000 or signal.ndim != 1 or not np.isfinite(signal).all():
                        raise MimeticError("INVALID_AUDIO", "Rendu PTX invalide.")
                    offset = unit["placement_start_samples"] - island["start"]
                    summed[offset:offset + len(signal)] += signal
                name = f"P{pair:02d}_I{number:02d}_{kind}"
                path = work / f"{name}.wav"
                write_bwf(path, summed, island["start"])
                specs.append({"audio_path":str(path), "track_name":track, "clip_name":name,
                              "placement_start_samples":island["start"]})
                checks.append({"clip_name":name, "track":track, "kind":kind, "start_samples":island["start"],
                               "length_samples":len(summed), "units":[u["id"] for u in island["units"]],
                               "muted":kind == "EQ_IR_MIX", "clip_gain_db":3.5 if kind == "IR_ONLY" else 0.0})
    result = mod.build_audio_session(str(TEMPLATE), specs, str(destination), session_name=session_name)
    session = mod.ProToolsSession(result["session_path"])
    for check in checks:
        check_cancel()
        if session.mute_clip(check["clip_name"], mute=check["muted"]) != 1:
            raise MimeticError("PTX_EXPORT_FAILED", "Placement de clip inattendu.")
        session.set_clip_gain(check["clip_name"], check["clip_gain_db"])
    session.save(result["session_path"])
    reread = mod.ProToolsSession(result["session_path"])
    rows = reread.get_timeline_clips()
    if len(rows) != len(checks):
        raise MimeticError("PTX_EXPORT_FAILED", "Nombre de clips différent après sauvegarde.")
    expected = {c["clip_name"]:c for c in checks}
    for row in rows:
        want = expected[row["clip_name"]]
        if any(row[key] != want[key] for key in ("track", "start_samples", "length_samples", "muted")) or clip_gain(reread, row["clip_name"]) != want["clip_gain_db"]:
            raise MimeticError("PTX_EXPORT_FAILED", "Position, mute ou Clip Gain modifié.")
        generated = work / f"{row['clip_name']}.wav"
        copied = destination / "Audio Files" / row["physical_filename"]
        if io.sha256_file(generated) != io.sha256_file(copied):
            raise MimeticError("PTX_EXPORT_FAILED", "Média modifié pendant l'export.")
    report = {"app_version":__version__, "template_sha256":TEMPLATE_SHA256, "tracks":reread.get_tracks(),
              "placements":checks, "verification":"API positions, mutes, Clip Gain and unchanged WAV verified",
              "input_audio_mode":"consolidated media samples; plugins, automation and input mix gains are not bounced",
              "settings":{"wet_gain_db":0.0, "predelay_ms":0.0, "eq_amount":1.0, "ir_clip_gain_db":3.5},
              "units":[{k:v for k,v in u.items() if k != "outputs"} for u in units]}
    (destination / "Mimetic_report.json").write_text(json.dumps(io.json_safe(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return report

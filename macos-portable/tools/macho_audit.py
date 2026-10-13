"""Read Intel Mach-O load commands on any OS; no Apple compiler is needed."""
from __future__ import annotations

import struct
from pathlib import Path

INTEL = 0x01000007
DYLIBS = {0xC, 0x80000018, 0x8000001F, 0x80000023}


def version(value):
    return (value >> 16, (value >> 8) & 255, value & 255)


def slices(data):
    magic = data[:4]
    if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return [(0, len(data), "<")]
    if magic in (b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"):
        return [(0, len(data), ">")]
    if magic not in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf", b"\xbe\xba\xfe\xca", b"\xbf\xba\xfe\xca"):
        return []
    endian = ">" if magic[:2] == b"\xca\xfe" else "<"
    count = struct.unpack_from(endian + "I", data, 4)[0]
    wide = magic in (b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")
    result = []
    for index in range(count):
        pos = 8 + index * (32 if wide else 20)
        cpu = struct.unpack_from(endian + "I", data, pos)[0]
        offset, length = struct.unpack_from(endian + ("QQ" if wide else "II"), data, pos + 8)
        if cpu == INTEL:
            result.extend((offset + o, n, e) for o, n, e in slices(data[offset:offset + length]))
    if not result:
        raise ValueError("Mach-O sans architecture Intel")
    return result


def inspect(path: Path):
    data = path.read_bytes()
    variants = slices(data)
    if not variants:
        return None
    results = []
    for start, size, endian in variants:
        cpu, _, _, count, command_size = struct.unpack_from(endian + "IIIII", data, start + 4)
        if cpu != INTEL:
            raise ValueError(f"Architecture non Intel : {path}")
        offset = start + (32 if data[start:start+4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf") else 28)
        limit = offset + command_size
        minimums, libraries, rpaths = [], [], []
        signature = None
        for _ in range(count):
            command, length = struct.unpack_from(endian + "II", data, offset)
            if length < 8 or offset + length > limit:
                raise ValueError(f"Commande Mach-O invalide : {path}")
            if command == 0x24:
                minimums.append(version(struct.unpack_from(endian + "I", data, offset + 8)[0]))
            elif command == 0x32:
                plat, minimum = struct.unpack_from(endian + "II", data, offset + 8)
                if plat == 1:
                    minimums.append(version(minimum))
            elif command in DYLIBS or command == 0x8000001C:
                string_start = struct.unpack_from(endian + "I", data, offset + 8)[0]
                value = data[offset + string_start:offset + length].split(b"\0", 1)[0].decode("utf-8")
                (rpaths if command == 0x8000001C else libraries).append(value)
            elif command == 0x1D:
                sig_offset, sig_size = struct.unpack_from(endian + "II", data, offset + 8)
                signature = (start + sig_offset, sig_size)
            offset += length
        results.append({"minimums": minimums, "libraries": libraries, "rpaths": rpaths,
                        "has_signature": signature is not None})
    return results


def expand(value, file, executable):
    if value.startswith("@loader_path"):
        return file.parent / value[len("@loader_path/"):]
    if value.startswith("@executable_path"):
        return executable.parent / value[len("@executable_path/"):]
    if value.startswith("/"):
        return Path(value)
    return None


def audit(bundle: Path):
    executable = bundle / "Contents" / "MacOS" / "Mimetic"
    python = bundle / "Contents" / "Resources" / "bin" / "python3.11"
    files, problems = [], []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file():
            continue
        with path.open("rb") as source:
            magic = source.read(4)
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",
                         b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf", b"\xbe\xba\xfe\xca", b"\xbf\xba\xfe\xca"):
            continue
        records = inspect(path)
        for record in records:
            if not record["minimums"]:
                problems.append(f"Pas de version minimale macOS : {path}")
            if any(v > (12, 7, 6) for v in record["minimums"]):
                problems.append(f"Version macOS trop récente : {path} {record['minimums']}")
            for library in record["libraries"]:
                if library.startswith(("/System/Library/", "/usr/lib/")):
                    continue
                if library.startswith("@rpath/"):
                    found = any((base / library[len("@rpath/"):]).is_file()
                                for candidate in record["rpaths"]
                                for exe in (executable, python)
                                if (base := expand(candidate, path, exe)) is not None)
                else:
                    found = any(target is not None and target.is_file()
                                for exe in (executable, python) for target in [expand(library, path, exe)])
                if not found:
                    problems.append(f"Bibliothèque non résolue : {path.relative_to(bundle)} -> {library}")
        files.append({"path": path.relative_to(bundle).as_posix(), "intel": records})
    if problems:
        raise ValueError("\n".join(problems))
    return files

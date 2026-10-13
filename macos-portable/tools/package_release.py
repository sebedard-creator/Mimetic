"""Publish a checked app-only ZIP locally; committing/pushing remains separate."""
from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import shutil
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package(destination: Path):
    source = ROOT / "dist" / "Mimetic-Mac-Intel-Monterey.zip"
    checksum = sha(source)
    assembly = json.loads((ROOT / "dist" / "ASSEMBLY_REPORT.json").read_text(encoding="utf-8"))
    if assembly["zip"]["sha256"] != checksum:
        raise ValueError("L'archive diffère du rapport d'assemblage")
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or not all(name.startswith("Mimetic.app/") for name in names):
            raise ValueError("La livraison doit contenir uniquement Mimetic.app, sans doublons")
        if archive.testzip() is not None:
            raise ValueError("ZIP corrompu")
        info = plistlib.loads(archive.read("Mimetic.app/Contents/Info.plist"))
        for name in ("Contents/MacOS/Mimetic", "Contents/MacOS/python", "Contents/Resources/bin/python3.11"):
            if not archive.getinfo("Mimetic.app/" + name).external_attr >> 16 & 0o111:
                raise ValueError("Permission d'exécution absente : " + name)
        prefix = "Mimetic.app/Contents/Resources/payload/mimetic/resources/"
        for name in ("Mimetic_Template.ptx", "Audio Files/Mimetic_Prototype_Float32_TC_60s.wav"):
            if prefix + name not in names:
                raise ValueError("Template ou WAV requis absent")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / source.name
    pending = destination / (source.name + "." + uuid.uuid4().hex[:8] + ".partial")
    shutil.copyfile(source, pending)
    if sha(pending) != checksum:
        raise ValueError("Copie de livraison altérée")
    if target.exists():
        shutil.copy2(target, ROOT / "dist" / ("previous-release-" + uuid.uuid4().hex[:8] + ".zip"))
    pending.replace(target)
    report = {"filename": source.name, "version": info["CFBundleShortVersionString"],
              "build": info["CFBundleVersion"], "bytes": target.stat().st_size,
              "sha256": checksum, "contents": "Mimetic.app only", "target": "Intel macOS Monterey 12.7.6",
              "python": assembly["python"], "gui": assembly["gui"],
              "native_binaries_checked": assembly["binary_count"],
              "code_pages_checked": assembly["verified_code_pages"],
              "resource_hashes_checked": assembly["verified_resource_hashes"],
              "signature": "ad hoc; native Apple signing needed on the target Mac",
              "native_validation": "build 1504 startup confirmed on Intel Monterey; current build needs target validation"}
    if destination.resolve() == (ROOT / "releases").resolve():
        (destination / "release.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=ROOT / "releases")
    package(parser.parse_args().destination)

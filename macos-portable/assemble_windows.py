"""Assemble a ready-to-run Intel Mac .app from signed/precompiled components."""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
from macho_audit import audit, inspect
from signature_audit import verify as verify_signature

COMPONENTS = {
    "python-mac.tar.gz": (
        "https://github.com/astral-sh/python-build-standalone/releases/download/20241016/cpython-3.11.10%2B20241016-x86_64-apple-darwin-install_only.tar.gz",
        "1e23ffe5bc473e1323ab8f51464da62d77399afb423babf67f8e13c82b69c674"),
    "rcodesign-win.zip": (
        "https://github.com/indygreg/apple-platform-rs/releases/download/apple-codesign/0.29.0/apple-codesign-0.29.0-x86_64-pc-windows-msvc.zip",
        "54bb500e2da7a8de02fcae0f331d1cac6e6d7173b4281042ff9c528ba3159aaa"),
}


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024**2), b""):
            digest.update(data)
    return digest.hexdigest()


def run(args, **kwargs):
    print("> " + " ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def fetch(path, url, checksum):
    if not path.is_file() or sha(path) != checksum:
        urllib.request.urlretrieve(url, path)
    if sha(path) != checksum:
        raise ValueError(f"Empreinte inattendue : {path.name}")


def validate_wheel(path):
    from packaging.utils import parse_wheel_filename
    name, version, _, _ = parse_wheel_filename(path.name)
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url) as stream:
        metadata = json.load(stream)
    expected = next(file["digests"]["sha256"] for file in metadata["urls"] if file["filename"] == path.name)
    if sha(path) != expected:
        raise ValueError(f"Wheel modifié : {path}")
    return {"filename": path.name, "sha256": expected, "metadata": url}


def safe_relative(name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or any(":" in part for part in relative.parts):
        raise ValueError("Chemin de composant invalide : " + name)
    return relative


def copy_runtime(archive_path, resources, modes):
    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            parts = safe_relative(member.name).parts
            if parts[0] != "python":
                raise ValueError("Préfixe Python inattendu")
            target = resources.joinpath(*parts[1:])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not (member.isfile() or member.issym() or member.islnk()):
                raise ValueError("Type de fichier runtime inattendu")
            # Materialize trusted runtime aliases: no symlink creation privilege
            # is required on Windows and the resulting ZIP is self-contained.
            if member.issym() or member.islnk():
                import posixpath
                link = member.linkname if member.islnk() else posixpath.join(posixpath.dirname(member.name), member.linkname)
                safe = posixpath.normpath(link)
                if not safe.startswith("python/"):
                    raise ValueError("Lien runtime hors de son dossier")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Fichier runtime absent")
            target.parent.mkdir(parents=True, exist_ok=True)
            with stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output, 1024**2)
            modes[target] = member.mode & 0o777


def install_wheel(wheel, site, modes):
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            parts = safe_relative(member.filename).parts
            if not parts or member.is_dir():
                continue
            if parts[0].endswith(".data"):
                if parts[1] not in ("purelib", "platlib"):
                    continue
                parts = parts[2:]
            target = site.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
            modes[target] = ((member.external_attr >> 16) & 0o777) or 0o644


def icon(resources):
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (1024, 1024))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((52, 52, 972, 972), radius=215, fill=(20, 40, 51, 255))
    draw.line([(290, 752), (290, 290), (512, 566), (734, 290), (734, 752)],
              fill=(135, 225, 200, 255), width=70, joint="curve")
    image.save(resources / "Mimetic.icns", format="ICNS")
    image.resize((256, 256)).save(ROOT / "build" / "Mimetic-icon.png")


def bundle_zip(bundle, output, modes):
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for file in sorted(bundle.rglob("*")):
            relative = "Mimetic.app/" + file.relative_to(bundle).as_posix()
            if file.is_dir():
                info = zipfile.ZipInfo(relative + "/")
                info.create_system = 3
                info.external_attr = (stat.S_IFDIR | 0o755) << 16 | 0x10
                archive.writestr(info, b"")
                continue
            info = zipfile.ZipInfo(relative)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = modes.get(file, 0o644)
            with file.open("rb") as stream:
                if stream.read(4) in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
                    mode = 0o755
            info.external_attr = (stat.S_IFREG | mode) << 16
            with file.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                shutil.copyfileobj(source, target, 1024**2)


def main(reuse=None):
    if sys.platform != "win32":
        raise RuntimeError("Ce script assemble depuis la station Windows.")
    from prepare_snapshot import verify_payload
    verify_payload()
    cache = ROOT / "build" / "cross-components"
    cache.mkdir(parents=True, exist_ok=True)
    tools = ROOT / "build" / "assembly-tools"
    run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", tools,
         "Pillow==11.1.0", "packaging==24.2"])
    sys.path.insert(0, str(tools))
    for name, (url, checksum) in COMPONENTS.items():
        fetch(cache / name, url, checksum)
    # Explicit complete dependency list avoids target/host environment-marker
    # confusion during cross-platform pip download. No dependencies are built.
    wheel_dir = ROOT / "build" / "mac-wheel-check"
    run([sys.executable, "-m", "pip", "download", "--no-deps", "--only-binary=:all:",
         "--platform", "macosx_12_0_x86_64", "--python-version", "3.11", "--implementation", "cp", "--abi", "cp311",
         "-r", ROOT / "requirements-mac.txt", "pyobjc-core==10.3.2", "pyobjc-framework-Cocoa==10.3.2", "--dest", wheel_dir])
    run([sys.executable, "-m", "pip", "download", "--no-deps", "py2app==0.28.8", "--dest", cache])
    wheels = sorted(wheel_dir.glob("*.whl"))
    provenance = [validate_wheel(path) for path in wheels]
    launcher_wheel = cache / "py2app-0.28.8-py2.py3-none-any.whl"
    provenance.append(validate_wheel(launcher_wheel))
    workspace = ROOT / "build" / ("assembled-" + uuid.uuid4().hex[:10]) if reuse is None else Path(reuse).resolve()
    if workspace.parent != (ROOT / "build").resolve() or not workspace.name.startswith("assembled-"):
        raise ValueError("Dossier de travail hors de la zone d'assemblage")
    bundle = workspace / "Mimetic.app"
    contents, modes = bundle / "Contents", {}
    resources, macos = contents / "Resources", contents / "MacOS"
    resources.mkdir(parents=True, exist_ok=True)
    macos.mkdir(exist_ok=True)
    (contents / "Frameworks").mkdir(exist_ok=True)
    if reuse is None:
        copy_runtime(cache / "python-mac.tar.gz", resources, modes)
    site = resources / "lib" / "python3.11" / "site-packages"
    if reuse is None:
        for wheel in wheels:
            install_wheel(wheel, site, modes)
    with zipfile.ZipFile(launcher_wheel) as archive:
        (macos / "Mimetic").write_bytes(archive.read("py2app/apptemplate/prebuilt/main-x86_64"))
        source = resources / "Source" / "py2app"
        source.mkdir(parents=True, exist_ok=True)
        (source / "main.c").write_bytes(archive.read("py2app/apptemplate/src/main.c"))
        for name in archive.namelist():
            if ".dist-info/" in name and Path(name).name.lower().startswith(("license", "copying", "metadata")):
                (source / Path(name).name).write_bytes(archive.read(name))
    auxiliary = macos / "python"
    # The launcher uses this auxiliary path for Py_SetProgramName and sets
    # PYTHONHOME to Resources. The GUI bootstrap points sys.executable at the
    # original, relocatable binary in Resources/bin. A shell trampoline also
    # makes this auxiliary usable directly, without modifying any Mach-O code.
    auxiliary.write_text('#!/bin/sh\nTASK_ROOT="$(cd "$(dirname "$0")/../Resources" && pwd)"\n'
                         'exec "$TASK_ROOT/bin/python3.11" "$@"\n', encoding="utf-8", newline="\n")
    modes[auxiliary] = modes[macos / "Mimetic"] = 0o755
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    # A reused staging folder must not retain modules removed from the sources.
    clean_app = (resources / "app").resolve()
    if clean_app.parent != resources.resolve() or not clean_app.is_relative_to(workspace.resolve()):
        raise ValueError("Dossier app hors de l'assemblage")
    if clean_app.exists():
        shutil.rmtree(clean_app)
    shutil.copytree(ROOT / "app", resources / "app", ignore=ignore, dirs_exist_ok=True)
    shutil.copytree(ROOT / "payload", resources / "payload", ignore=ignore, dirs_exist_ok=True)
    shutil.copy2(ROOT / "app" / "mac_boot.py", resources / "__boot__.py")
    icon(resources)
    info = {
        "CFBundleExecutable": "Mimetic", "CFBundleName": "Mimetic", "CFBundleDisplayName": "Mimetic",
        "CFBundleIdentifier": "com.sebedard.mimetic.portable", "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0", "CFBundleShortVersionString": "1.5.0", "CFBundleVersion": "1506",
        "CFBundleIconFile": "Mimetic.icns", "CFBundleDevelopmentRegion": "fr", "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True, "NSPrincipalClass": "NSApplication",
        "NSHumanReadableCopyright": "© 2026 Sébastien Bédard",
        "PyMainFileNames": ["__boot__"], "PyExecutableName": "python",
        "PyRuntimeLocations": ["@executable_path/../Resources/lib/libpython3.11.dylib"],
        "PyResourcePackages": ["lib/python3.11", "lib/python3.11/lib-dynload", "lib/python3.11/site-packages", "app", "payload"],
        "PyOptions": {"use_pythonpath": False, "optimize": 0},
        "LSEnvironment": {"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
        "CFBundleDocumentTypes": [{"CFBundleTypeName": "Séquence Pro Tools ZIP", "CFBundleTypeRole": "Editor",
                                   "LSHandlerRank": "Alternate", "LSItemContentTypes": ["public.zip-archive"],
                                   "CFBundleTypeExtensions": ["zip"]}],
    }
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    (contents / "PkgInfo").write_bytes(b"APPL????")
    print("Audit des binaires Intel et des dépendances…", flush=True)
    binaries = audit(bundle)
    signing_dir = cache / "sign-tool"
    if not signing_dir.exists():
        with zipfile.ZipFile(cache / "rcodesign-win.zip") as archive:
            archive.extractall(signing_dir)
    signer = next(signing_dir.rglob("rcodesign.exe"))
    config = workspace / "empty.toml"
    config.write_text("", encoding="utf-8")
    signature_log = workspace / "signing.log"
    with signature_log.open("w", encoding="utf-8") as log:
        run([signer, "-C", config, "sign", "--binary-identifier", "com.sebedard.mimetic.portable", bundle],
            stdout=log, stderr=subprocess.STDOUT)
    seal_path = contents / "_CodeSignature" / "CodeResources"
    info_bytes = (contents / "Info.plist").read_bytes()
    resources_bytes = seal_path.read_bytes()
    signed_pages = 0
    for entry in binaries:
        main_binary = entry["path"] == "Contents/MacOS/Mimetic"
        entry["signature_checks"] = verify_signature(bundle / entry["path"],
            info=info_bytes if main_binary else None, resources=resources_bytes if main_binary else None)
        signed_pages += sum(item["pages"] for item in entry["signature_checks"])
    with seal_path.open("rb") as stream:
        seal = plistlib.load(stream)
    verified_resources = 0
    for relative, item in seal.get("files2", {}).items():
        digest = item.get("hash2") if isinstance(item, dict) else None
        if digest:
            file = contents / relative
            if bytes.fromhex(sha(file)) != digest:
                raise ValueError("Ressource signée altérée : " + relative)
            verified_resources += 1
    report = {"assembled_on": "Windows", "method": "precompiled Intel components; no compilation on the target Mac",
              "target": "Intel macOS Monterey 12.7.6", "components": COMPONENTS, "wheels": provenance,
              "binary_count": len(binaries), "binaries": binaries, "signature": "ad hoc rcodesign 0.29.0",
              "verified_resource_hashes": verified_resources,
              "verified_code_pages": signed_pages,
              "signature_validation": "code pages and bundle hashes checked here; Apple codesign validation pending",
              "mac_runtime_test": "pending on the target Mac; not executable on Windows",
              "python": "3.11.10", "gui": "native AppKit via PyObjC 10.3.2"}
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / "Mimetic-Mac-Intel-Monterey.zip"
    if output.exists():
        output.rename(dist / ("Mimetic-previous-" + uuid.uuid4().hex[:8] + ".zip"))
    print("Archivage de l'application…", flush=True)
    bundle_zip(bundle, output, modes)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise ValueError("ZIP de l'application corrompu")
        for name in ("Mimetic.app/Contents/MacOS/Mimetic", "Mimetic.app/Contents/MacOS/python",
                     "Mimetic.app/Contents/Resources/bin/python3.11"):
            if not (archive.getinfo(name).external_attr >> 16) & 0o111:
                raise ValueError("Permission d'exécution absente : " + name)
    report["zip"] = {"bytes": output.stat().st_size, "sha256": sha(output)}
    (dist / "ASSEMBLY_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"app": str(bundle), "zip": str(output), "binary_count": len(binaries),
                      "zip_bytes": output.stat().st_size, "sha256": sha(output)}, indent=2))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-staging", type=Path)
    args = parser.parse_args()
    main(args.reuse_staging)

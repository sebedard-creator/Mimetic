"""Import one normal Pro Tools archive with bounded, cancellable streaming."""
from __future__ import annotations

import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from mimetic.domain.errors import MimeticError

MAX_ZIP_BYTES = 2 * 1024**3
MAX_BUNDLE_BYTES = 4 * 1024**3
MAX_MEDIA_BYTES = 2 * 1024**3
MAX_MEMBERS = 20000
MAX_RELEVANT = 4096


def unpack(path: Path, root: Path, check_cancel=lambda: None):
    if path.suffix.lower() != ".zip" or not path.is_file():
        raise MimeticError("PTX_UNSUPPORTED", "Déposer un ZIP contenant un PTX et son dossier Audio Files.")
    if path.stat().st_size > MAX_ZIP_BYTES:
        raise MimeticError("LIMIT_EXCEEDED", "ZIP supérieur à 2 Go.")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS:
                raise MimeticError("LIMIT_EXCEEDED", "Trop d'éléments dans le ZIP.")
            entries, total = [], 0
            for info in infos:
                check_cancel()
                parts = PurePosixPath(info.filename.replace("\\", "/"))
                if parts.is_absolute() or ".." in parts.parts or any(":" in p for p in parts.parts) or "\x00" in info.filename:
                    raise MimeticError("PTX_UNSUPPORTED", "Chemin ZIP invalide.")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise MimeticError("PTX_UNSUPPORTED", "Lien symbolique ZIP refusé.")
                if info.is_dir() or parts.name.startswith("._") or any(
                        p.casefold() in ("session file backups", "__macosx") for p in parts.parts):
                    continue
                if parts.suffix.lower() in (".ptx", ".wav"):
                    total += info.file_size
                    if total > MAX_BUNDLE_BYTES or info.file_size > MAX_MEDIA_BYTES or len(entries) >= MAX_RELEVANT:
                        raise MimeticError("LIMIT_EXCEEDED", "Archive PTX trop volumineuse.")
                    entries.append((info, parts))
            sessions = [(i, p) for i, p in entries if p.suffix.lower() == ".ptx"]
            if len(sessions) != 1:
                raise MimeticError("PTX_UNSUPPORTED", "Le ZIP doit contenir un seul PTX, hors backups.")
            media, session_path = {}, root / "session.ptx"
            folder = tuple(p.casefold() for p in sessions[0][1].parent.parts) + ("audio files",)
            for info, parts in entries:
                check_cancel()
                if parts.suffix.lower() == ".ptx":
                    target = session_path
                else:
                    if tuple(p.casefold() for p in parts.parent.parts) != folder:
                        continue
                    key = parts.name.casefold()
                    if key in media:
                        raise MimeticError("PTX_UNSUPPORTED", f"Nom WAV ambigu : {parts.name}")
                    target = root / (uuid.uuid4().hex + ".wav")
                    media[key] = target
                with archive.open(info) as src, target.open("xb") as dst:
                    actual = 0
                    while chunk := src.read(1024**2):
                        check_cancel()
                        actual += len(chunk)
                        if actual > info.file_size or actual > MAX_MEDIA_BYTES:
                            raise MimeticError("LIMIT_EXCEEDED", "Média décompressé trop volumineux.")
                        dst.write(chunk)
                if actual != info.file_size:
                    raise MimeticError("PTX_UNSUPPORTED", "Média incomplet dans le ZIP.")
            return session_path, sessions[0][1].name, media
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError) as exc:
        raise MimeticError("PTX_UNSUPPORTED", "ZIP illisible, incomplet ou protégé.") from exc

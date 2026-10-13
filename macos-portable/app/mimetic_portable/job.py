"""One ZIP -> verified PTX/audio ZIP. The input archive is never modified."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path

from mimetic import pro_tools
from mimetic.audio.io import json_safe
from mimetic.domain.errors import MimeticError
from mimetic.engines.recrir import adapter
from mimetic_portable import PORTABLE_VERSION
from mimetic_portable.publish import publish_exclusive
from mimetic_portable.zip_import import unpack


def hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024**2), b""):
            h.update(chunk)
    return h.hexdigest()


class Job:
    def __init__(self, *, estimator=None, client=None, emit=lambda obj: None):
        self.client = client or adapter.WorkerClient()
        self.estimator = estimator or self._estimate
        self.emit = emit
        self.cancelled = threading.Event()
        self.total = 0

    @staticmethod
    def _estimate(*args, **kwargs):
        return adapter.estimate_profile(*args, **kwargs, minimum_seconds=0.032)

    def cancel(self):
        self.cancelled.set()
        self.client.cancelled = True
        self.client.stop()

    def check_cancel(self):
        if self.cancelled.is_set():
            raise MimeticError("CANCELLED")

    def progress(self, step, completed=0):
        self.check_cancel()
        self.emit({"event": "progress", "step": step, "completed": completed, "total": self.total})

    def run(self, input_path: Path, output_dir: Path | None = None) -> Path:
        input_path = input_path.expanduser().resolve(strict=True)
        output_dir = (output_dir or input_path.parent).expanduser().resolve(strict=True)
        if not output_dir.is_dir():
            raise MimeticError("PTX_EXPORT_FAILED", "Dossier de sortie introuvable.")
        staged = None
        try:
            # Work on the output volume: one final rename publishes a complete
            # archive. A private random staging directory cannot collide with a
            # previously exported ZIP, including on case-insensitive filesystems.
            with tempfile.TemporaryDirectory(prefix=".mimetic-", dir=output_dir) as temporary:
                work = Path(temporary)
                self.emit({"event": "workspace", "path": str(work)})
                self.progress("Lecture du ZIP")
                upload = work / "input"
                upload.mkdir()
                session, source_name, media = unpack(input_path, upload, self.check_cancel)
                plan = pro_tools.read_plan(session)
                missing = [name for name in plan["media"] if name.casefold() not in media]
                if missing:
                    raise MimeticError("PTX_MEDIA_MISSING", "Audio Files : " + ", ".join(missing[:12]))
                pro_tools.validate_media(plan, media)
                self.total = len(plan["units"])
                self.emit({"event": "plan", "session": source_name, "pairs": plan["pairs"], "total": self.total})
                render = work / "render"
                render.mkdir()
                units = pro_tools.render_units(plan, media, render, estimator=self.estimator,
                                              client=self.client, progress=self.progress, check_cancel=self.check_cancel)
                self.progress("Assemblage et vérification du PTX", self.total)
                stem = input_path.stem
                name = "Mimetic_PTX_" + uuid.uuid4().hex[:12]
                bundle = work / name
                report = pro_tools.package_units(units, render, bundle, name, self.check_cancel)
                report["portable"] = {"version": PORTABLE_VERSION, "target": "macOS 12 Intel",
                                      "input_zip_name": input_path.name,
                                      "audio_processing": "same Mimetic 1.5 DSP; CPU engine; offline"}
                (bundle / "Mimetic_report.json").write_text(json.dumps(json_safe(report), ensure_ascii=False, indent=2), encoding="utf-8")
                self.progress("Création du ZIP", self.total)
                staged = work / "result.zip"
                # WAV float is already large; stored ZIP avoids a second long CPU
                # pass. Streaming writes keep cancellation responsive.
                with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    for file in sorted(bundle.rglob("*")):
                        self.check_cancel()
                        if not file.is_file():
                            continue
                        info = zipfile.ZipInfo.from_file(file, arcname=f"{name}/{file.relative_to(bundle).as_posix()}")
                        with file.open("rb") as src, archive.open(info, "w", force_zip64=True) as dst:
                            while chunk := src.read(1024**2):
                                self.check_cancel()
                                dst.write(chunk)
                self.check_cancel()
                # Publish atomically without reserving an empty visible ZIP.
                for index in range(1, 10000):
                    suffix = "" if index == 1 else f"_{index:02d}"
                    target = output_dir / f"{stem}_Mimetic{suffix}.zip"
                    self.check_cancel()
                    try:
                        publish_exclusive(staged, target)
                    except FileExistsError:
                        continue
                    except OSError as exc:
                        raise MimeticError("PTX_EXPORT_FAILED", "Impossible de publier le ZIP dans ce dossier : " + str(exc)) from exc
                    break
                else:
                    raise MimeticError("PTX_EXPORT_FAILED", "Trop de résultats du même nom.")
                warnings = [{"id": u["id"], "warnings": u["warnings"]} for u in units if u["warnings"]]
                self.emit({"event": "ready", "path": str(target), "total": self.total,
                           "placements": len(report["placements"]), "warnings": warnings})
                return target
        finally:
            self.client.stop()

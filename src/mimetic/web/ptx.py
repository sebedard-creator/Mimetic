"""Local PTX import lifecycle. Uploaded media live under opaque internal names."""
from __future__ import annotations

import shutil
import stat
import threading
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from mimetic import pro_tools
from mimetic.domain.errors import MimeticError
from mimetic.engines.recrir import adapter

MAX_BUNDLE_BYTES = 4 * 1024**3
MAX_MEDIA_BYTES = 2 * 1024**3


class PtxProject:
    def __init__(self, project):
        self.project = project
        self.client = adapter.WorkerClient(project.client.root)
        self.estimator = project.estimator
        if self.estimator is adapter.estimate_profile:
            self.estimator = lambda *args, **kwargs: adapter.estimate_profile(*args, **kwargs, minimum_seconds=0.032)
        self.thread = None
        self.cancel_event = threading.Event()
        self.id = None
        self.path = None
        self.name = None
        self.plan = None
        self.media = {}
        self.job = {"state":"idle", "step":None, "completed":0}
        self.error = None
        self.export = None

    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def _check_idle(self):
        if self.running() or (self.project.worker_thread and self.project.worker_thread.is_alive()):
            raise MimeticError("PTX_BUSY", "Annuler le calcul en cours avant de changer de séquence.")

    def snapshot(self):
        return {"id":self.id, "name":self.name, "pairs":self.plan["pairs"] if self.plan else [],
                "total":len(self.plan["units"]) if self.plan else 0,
                "job":dict(self.job), "error":self.error, "export":self.export,
                "capabilities":pro_tools.capabilities()}

    def ingest(self, path: Path, name: str):
        with self.project.lock:
            self._check_idle()
            root = self.project.upload_dir / f"ptx_{uuid.uuid4().hex}"
            root.mkdir()
            media = {}
            try:
                if not name.lower().endswith(".zip"):
                    raise MimeticError("PTX_UNSUPPORTED", "Déposer un ZIP contenant le PTX et son dossier Audio Files.")
                session_path, session_name, media = self._unpack(path, root)
                plan = pro_tools.read_plan(session_path)
                media = {k:v for k,v in media.items() if k in {n.casefold() for n in plan["media"]}}
                missing = [n for n in plan["media"] if n.casefold() not in media]
                if missing:
                    raise MimeticError("PTX_MEDIA_MISSING", "ZIP incomplet — Audio Files : " + ", ".join(missing[:12]))
                pro_tools.validate_media(plan, media)
                self.id, self.path, self.name = root.name, session_path, session_name
                self.plan, self.media = plan, media
                self.job = {"state":"idle", "step":None, "completed":0}
                self.error = self.export = None
                self.project.revision += 1
                self.start_if_ready()
            except Exception:
                shutil.rmtree(root)
                raise
            finally:
                path.unlink(missing_ok=True)

    def _unpack(self, path, root):
        try:
            with zipfile.ZipFile(path) as archive:
                entries, total = [], 0
                for info in archive.infolist():
                    parts = PurePosixPath(info.filename.replace("\\", "/"))
                    if parts.is_absolute() or ".." in parts.parts or any(":" in p for p in parts.parts):
                        raise MimeticError("PTX_UNSUPPORTED", "Chemin ZIP invalide.")
                    if stat.S_ISLNK(info.external_attr >> 16):
                        raise MimeticError("PTX_UNSUPPORTED", "Lien symbolique ZIP refusé.")
                    if info.is_dir() or parts.name.startswith("._") or any(
                            p.casefold() in ("session file backups", "__macosx") for p in parts.parts):
                        continue
                    if parts.suffix.lower() in (".ptx", ".wav"):
                        total += info.file_size
                        if total > MAX_BUNDLE_BYTES or info.file_size > MAX_MEDIA_BYTES or len(entries) >= 4096:
                            raise MimeticError("LIMIT_EXCEEDED", "Archive PTX trop volumineuse.")
                        entries.append((info, parts))
                sessions = [(i,p) for i,p in entries if p.suffix.lower() == ".ptx"]
                if len(sessions) != 1:
                    raise MimeticError("PTX_UNSUPPORTED", "Le ZIP doit contenir un seul PTX, hors backups.")
                media, session_path = {}, root / "session.ptx"
                audio_folder = tuple(p.casefold() for p in sessions[0][1].parent.parts) + ("audio files",)
                for info, parts in entries:
                    if parts.suffix.lower() == ".ptx":
                        target = session_path
                    else:
                        if tuple(p.casefold() for p in parts.parent.parts) != audio_folder:
                            continue
                        key = parts.name.casefold()
                        if key in media:
                            raise MimeticError("PTX_UNSUPPORTED", f"Nom WAV ambigu dans le ZIP : {parts.name}")
                        target = root / f"{uuid.uuid4().hex}.wav"
                        media[key] = target
                    # No extract()/filesystem path derived from an archive member.
                    with archive.open(info) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst, 1024**2)
                return session_path, sessions[0][1].name, media
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise MimeticError("PTX_UNSUPPORTED", "ZIP illisible ou protégé.") from exc

    def start_if_ready(self):
        if not self.plan or self.running():
            return
        self._check_idle()
        pro_tools.validate_media(self.plan, self.media)
        if not self.project.capabilities().get("available"):
            self.error = MimeticError("MODEL_UNAVAILABLE").to_dict()
            self.job = {"state":"failed", "step":None, "completed":0}
            return
        self.cancel_event.clear()
        self.client.cancelled = False
        self.error = self.export = None
        self.job = {"state":"running", "step":"Préparation PTX", "completed":0}
        self.thread = threading.Thread(target=self._run, name="mimetic-ptx", daemon=True)
        self.thread.start()

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise MimeticError("CANCELLED")

    def _progress(self, step, completed):
        with self.project.lock:
            self.job = {"state":"running", "step":step, "completed":completed}
            self.project.revision += 1

    def _run(self):
        work = self.project.work_dir / f"ptx_{uuid.uuid4().hex}"
        staging = zip_staging = target = zip_target = None
        try:
            work.mkdir()
            units = pro_tools.render_units(self.plan, self.media, work, estimator=self.estimator,
                                          client=self.client, progress=self._progress, check_cancel=self._check_cancel)
            self._check_cancel()
            self._progress("Assemblage et vérification du PTX", len(units))
            name = f"Mimetic_PTX_{uuid.uuid4().hex[:12]}"
            bundle = work / name
            report = pro_tools.package_units(units, work, bundle, name, self._check_cancel)
            zipped = work / f"{name}.zip"
            with zipfile.ZipFile(zipped, "w", compression=zipfile.ZIP_STORED) as archive:
                for file in sorted(bundle.rglob("*")):
                    self._check_cancel()
                    if file.is_file():
                        archive.write(file, arcname=f"{name}/{file.relative_to(bundle).as_posix()}")
            # Stage on the export volume, including when --export-dir is elsewhere.
            staging = self.project.export_dir / f".{name}.staging"
            zip_staging = self.project.export_dir / f".{name}.zip.staging"
            shutil.copytree(bundle, staging)
            self._check_cancel()
            shutil.copyfile(zipped, zip_staging)
            with self.project.lock:
                self._check_cancel()
                target = self.project.export_dir / name
                zip_target = self.project.export_dir / zipped.name
                staging.replace(target)
                zip_staging.replace(zip_target)
                self.export = {"zip":zipped.name, "directory":str(target), "session":f"{name}.ptx",
                               "adr_units":len(units), "placements":len(report["placements"]),
                               "warnings":[{"id":u["id"], "warnings":u["warnings"]} for u in units if u["warnings"]]}
                self.job = {"state":"ready", "step":"PTX prêt", "completed":len(units)}
                self.project.revision += 1
        except Exception as exc:
            if self.cancel_event.is_set():
                exc = MimeticError("CANCELLED")
            elif isinstance(exc, MemoryError):
                exc = MimeticError("OUT_OF_MEMORY")
            elif not isinstance(exc, MimeticError):
                import traceback
                try:
                    with (self.project.data_dir / "errors.log").open("a", encoding="utf-8") as log:
                        traceback.print_exc(file=log)
                except OSError:
                    pass
                exc = MimeticError("PTX_EXPORT_FAILED", str(exc))
            if self.export is None:
                if target and target.exists():
                    shutil.rmtree(target)
                if zip_target:
                    zip_target.unlink(missing_ok=True)
            with self.project.lock:
                self.error = None if exc.code == "CANCELLED" else exc.to_dict()
                self.job = {**self.job, "state":"cancelled" if exc.code == "CANCELLED" else "failed"}
                self.project.revision += 1
        finally:
            self.client.stop()
            shutil.rmtree(work, ignore_errors=True)
            if staging and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if zip_staging:
                zip_staging.unlink(missing_ok=True)
            with self.project.lock:
                self.thread = None
            if not self.cancel_event.is_set():
                self.project.kick()

    def cancel(self):
        thread = self.thread
        if thread and thread.is_alive():
            self.cancel_event.set()
            self.client.cancelled = True
            self.client.stop()
            thread.join(timeout=10)
            if thread.is_alive():
                raise MimeticError("PTX_BUSY", "L'annulation est en cours ; réessayer dans quelques secondes.")

    def reset(self):
        self.cancel()
        with self.project.lock:
            self.id = self.path = self.name = self.plan = self.export = self.error = None
            self.media = {}
            self.job = {"state":"idle", "step":None, "completed":0}
            self.project.revision += 1

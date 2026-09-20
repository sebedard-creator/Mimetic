"""Service web local (LAN) de la beta Mimetic — flux en une seule page.

L'utilisateur fournit deux fichiers :
- SOURCE : dialogue de tournage, analysé pour estimer l'acoustique ;
- DESTINATION : voix ADR, qui reçoit la reverb 100 % wet.

Dès que les deux sont présents, un job en arrière-plan fait ce qui manque : analyse (si la source ou son
canal a changé), puis rendu (si la destination, son canal, le profil ou les réglages ont changé).
Une session de projet unique en mémoire ; un seul calcul à la fois. Traitement 100 % local.
"""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Callable

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from mimetic import DSP_VERSION, __version__, pipeline
from mimetic.audio import io
from mimetic.domain.errors import MimeticError
from mimetic.analysis import eq_match
from mimetic.domain.models import (
    MAX_FILE_SECONDS,
    AudioAsset,
    EqSettings,
    RenderResult,
    RenderSettings,
    RoomProfile,
)
from mimetic.engines.recrir import adapter as recrir

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 256 * 1024**2
SLOTS = ("source", "destination")


class NoCacheStatic(StaticFiles):
    """Interface toujours relue : sans ça, un navigateur garde l'ancien CSS/JS après une mise à jour."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


def default_data_dir() -> Path:
    """Tout dans le dossier du projet : l'installation est autonome et déplaçable (décision D10)."""
    return recrir.PROJECT_ROOT / "data"


class Project:
    def __init__(self, data_dir: Path, export_dir: Path, engine_root: Path,
                 estimator: Callable | None = None, capabilities: Callable | None = None):
        self.data_dir = data_dir
        self.upload_dir = data_dir / "uploads"
        self.work_dir = data_dir / "jobs"
        self.export_dir = export_dir
        self.client = recrir.WorkerClient(engine_root)
        self.estimator = estimator or recrir.estimate_profile
        self.capabilities = capabilities or (lambda: recrir.capabilities(engine_root))

        self.lock = threading.RLock()
        self.revision = 0
        self.assets: dict[str, AudioAsset | None] = {s: None for s in SLOTS}
        self.channels: dict[str, str] = {s: "mono" for s in SLOTS}
        self.settings = RenderSettings()
        self.eq = EqSettings()
        self.eq_profile: dict | None = None
        self.eq_deps: tuple | None = None
        self.eq_error: dict | None = None
        self.eq_error_deps: tuple | None = None
        self.profile: RoomProfile | None = None
        self.profile_deps: tuple | None = None
        self.render: RenderResult | None = None
        self.last_export: dict | None = None

        self.job = {"state": "idle", "step": None}
        self.last_error: dict | None = None
        self.worker_thread: threading.Thread | None = None
        self.cancel_requested = False

    def clear_session(self) -> None:
        """Vide les deux fichiers et tout ce qui en dépend. N'efface ni les exports ni les réglages.

        Sert à enchaîner deux couples : sans ça, remplacer la SOURCE relance aussitôt un calcul
        avec l'ancienne DESTINATION, avant même d'avoir eu le temps de la remplacer.
        """
        self.cancel()
        with self.lock:
            for slot in SLOTS:
                asset = self.assets[slot]
                if asset is not None:
                    Path(asset.path).unlink(missing_ok=True)
                self.assets[slot] = None
                self.channels[slot] = "mono"
            self.profile = self.profile_deps = None
            self.eq_profile = self.eq_deps = self.eq_error = self.eq_error_deps = None
            self.render = None
            self.last_export = None
            self.last_error = None
            self.job = {"state": "idle", "step": None}
            self.revision += 1

    def reset_dirs(self) -> None:
        # Purge uniquement les dossiers créés par l'application sous sa racine.
        for d in (self.upload_dir, self.work_dir):
            if d.exists() and d.resolve().parent == self.data_dir.resolve():
                shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    # --- dépendances -------------------------------------------------------------------------

    def cache_bytes(self) -> int:
        """Taille des fichiers importés et des fichiers de travail (hors exports)."""
        total = 0
        for d in (self.upload_dir, self.work_dir):
            if d.exists():
                total += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        return total

    def export_stats(self) -> dict:
        files = [f for f in self.export_dir.glob("*") if f.is_file()] if self.export_dir.exists() else []
        return {"count": len(files), "bytes": sum(f.stat().st_size for f in files)}

    def delete_exports(self) -> dict:
        """Supprime les fichiers exportés. Destructif : uniquement sur demande explicite de l'utilisateur."""
        stats = self.export_stats()
        for f in self.export_dir.glob("*"):
            if f.is_file():
                f.unlink(missing_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        return stats

    def source_deps(self) -> tuple | None:
        a = self.assets["source"]
        return None if a is None else (a.sha256, self.channels["source"], recrir.ADAPTER_VERSION)

    def profile_ok(self) -> bool:
        return self.profile is not None and self.profile_deps == self.source_deps()

    def eq_target_deps(self) -> tuple | None:
        """Ce dont dépend la courbe de raccord : les deux fichiers, la pièce et le dosage de reverb
        (la cible inclut la reverb rendue), plus l'intensité et la politique de niveau."""
        src, dst = self.assets["source"], self.assets["destination"]
        if src is None or dst is None or not self.profile_ok():
            return None
        return (src.sha256, self.channels["source"], dst.sha256, self.channels["destination"],
                self.profile.profile_id, self.settings.wet_gain_db,
                self.settings.additional_predelay_seconds, self.eq.amount, self.eq.preserve_adr_level,
                eq_match.MATCH_VERSION)

    def eq_failed(self) -> bool:
        """Échec encore valable : si les entrées ont changé, on retentera."""
        if self.eq_error is None:
            return False
        if self.eq_error_deps != self.eq_target_deps():
            self.eq_error, self.eq_error_deps = None, None
            return False
        return True

    def eq_ok(self) -> bool:
        return bool(self.eq.enabled and self.eq_profile is not None and self.eq_deps == self.eq_target_deps())

    def eq_pending(self) -> bool:
        return bool(self.eq.enabled and not self.eq_ok() and not self.eq_failed())

    def active_eq(self) -> dict | None:
        return self.eq_profile if self.eq_ok() else None

    def render_key(self) -> str | None:
        d = self.assets["destination"]
        if d is None or not self.profile_ok():
            return None
        if self.eq_pending():
            return None  # la courbe doit être calculée avant le rendu
        return pipeline.dependency_key(d, self.channels["destination"], self.profile, self.settings,
                                       self.active_eq())

    def render_ok(self) -> bool:
        return self.render is not None and self.render.dependency_key == self.render_key()

    # --- job automatique ---------------------------------------------------------------------

    def kick(self) -> None:
        """Lance le job si du travail reste à faire et qu'aucun job ne tourne ; sinon il reprendra la main."""
        with self.lock:
            if self.worker_thread is not None and self.worker_thread.is_alive():
                return
            if self.assets["source"] is None or self.assets["destination"] is None:
                return
            if self.profile_ok() and self.render_ok() and not self.eq_pending():
                return
            if self.job["state"] == "failed" and self.job.get("failed_key") == self._work_key():
                return  # même entrées que l'échec précédent : pas de relance en boucle
            self.cancel_requested = False
            self.client.cancelled = False
            self.last_error = None
            self.worker_thread = threading.Thread(target=self._run, name="mimetic-job", daemon=True)
            self.worker_thread.start()

    def _work_key(self):
        return (self.source_deps(), self.render_key() if self.profile_ok() else None,
                self.assets["destination"].sha256 if self.assets["destination"] else None,
                self.channels["destination"], self.settings, self.eq)

    def _set_job(self, state, step=None, **extra):
        self.job = {"state": state, "step": step, **extra}
        self.revision += 1

    def _run(self) -> None:
        try:
            while True:
                if self.cancel_requested:
                    raise MimeticError("CANCELLED")
                with self.lock:
                    src, dst = self.assets["source"], self.assets["destination"]
                    if src is None or dst is None:
                        break
                    need_profile = not self.profile_ok()
                    src_deps, src_ch = self.source_deps(), self.channels["source"]
                    dst_ch, settings, profile = self.channels["destination"], self.settings, self.profile
                if need_profile:
                    caps = self.capabilities()
                    if not caps.get("available"):
                        raise MimeticError("MODEL_UNAVAILABLE", caps.get("detail"))
                    self._set_job("running", "Analyse de la pièce", kind="analysis")
                    signal = io.select_channel(src, src_ch)
                    meta = {"asset_sha256": src.sha256, "sample_rate_hz": src.sample_rate_hz,
                            "frame_count": src.frame_count, "channel_mode": src_ch, "name": src.original_name}
                    workdir = self.work_dir / uuid.uuid4().hex
                    try:
                        new_profile = self.estimator(
                            signal, src.sample_rate_hz, workdir=workdir, client=self.client, reference_meta=meta,
                            progress=lambda step: self._set_job("running", step, kind="analysis"))
                    finally:
                        shutil.rmtree(workdir, ignore_errors=True)
                    with self.lock:
                        # Résultat ignoré si la source a changé pendant l'analyse (§8) ; la boucle recommence.
                        if self.source_deps() == src_deps:
                            self.profile, self.profile_deps = new_profile, src_deps
                        self.revision += 1
                    continue
                if self.eq_pending():
                    self._set_job("running", "Analyse du timbre", kind="eq")
                    eq_deps = self.eq_target_deps()
                    try:
                        eq_profile = eq_match.build_profile(
                            io.select_channel(src, src_ch), src.sample_rate_hz,
                            io.select_channel(dst, dst_ch), dst.sample_rate_hz,
                            profile, settings, amount=self.eq.amount,
                            preserve_level=self.eq.preserve_adr_level)
                    except MimeticError as exc:
                        if not exc.code.startswith("EQ_"):
                            raise
                        # Un échec d'EQ ne détruit pas la pièce : on rend sans correction et on le dit.
                        with self.lock:
                            self.eq_error, self.eq_error_deps = exc.to_dict(), eq_deps
                            self.eq_profile, self.eq_deps = None, None
                            self.revision += 1
                        continue
                    with self.lock:
                        if self.eq_target_deps() == eq_deps:
                            self.eq_profile, self.eq_deps, self.eq_error = eq_profile, eq_deps, None
                        self.revision += 1
                    continue
                if not self.render_ok():
                    self._set_job("running", "Calcul de la reverb", kind="render")
                    result = pipeline.render(dst, dst_ch, profile, settings, self.active_eq())
                    with self.lock:
                        if result.dependency_key == self.render_key():
                            self.render = result
                            self.last_export = None
                        self.revision += 1
                    continue
                # Vérification finale atomique : un changement arrivé pendant le dernier calcul relance la boucle ;
                # sinon le job se termine et kick() pourra en démarrer un nouveau.
                with self.lock:
                    if (self.assets["source"] is not None and self.assets["destination"] is not None
                            and not (self.profile_ok() and self.render_ok() and not self.eq_pending())
                            and not self.cancel_requested):
                        continue
                    self.worker_thread = None
                    self._set_job("idle")
                    return
            with self.lock:
                self.worker_thread = None
                self._set_job("idle")
        except MimeticError as exc:
            with self.lock:
                self.worker_thread = None
                if exc.code == "CANCELLED":
                    self._set_job("cancelled")
                else:
                    self.last_error = exc.to_dict()
                    self._set_job("failed", failed_key=self._work_key())
        except MemoryError:
            with self.lock:
                self.worker_thread = None
                self.last_error = MimeticError("OUT_OF_MEMORY").to_dict()
                self._set_job("failed", failed_key=self._work_key())
        except Exception as exc:  # noqa: BLE001 — erreur structurée pour l'interface
            # Trace complète dans un journal local, pour diagnostiquer sans reproduire.
            try:
                import traceback
                from datetime import datetime
                with open(self.data_dir / "errors.log", "a", encoding="utf-8") as log:
                    log.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} ===\n")
                    traceback.print_exc(file=log)
            except OSError:
                pass
            with self.lock:
                self.worker_thread = None
                self.last_error = MimeticError("ENGINE_FAILURE", f"{type(exc).__name__}: {exc}").to_dict()
                self._set_job("failed", failed_key=self._work_key())

    def cancel(self) -> None:
        self.cancel_requested = True
        self.client.cancelled = True
        self.client.stop()

    # --- état --------------------------------------------------------------------------------

    def eq_snapshot(self) -> dict:
        state = ("disabled" if not self.eq.enabled else
                 "failed" if self.eq_failed() else
                 "ready" if self.eq_ok() else "pending")
        curve = None
        if self.eq_profile is not None and self.eq_ok():
            c = self.eq_profile["curve"]
            curve = {"frequencies_hz": np.asarray(c["frequencies_hz"]).round(1).tolist(),
                     "gain_db": np.asarray(c["gain_db"]).round(2).tolist(),
                     "level_offset_db": c["level_offset_db"],
                     "saturated_fraction": c["saturated_fraction"],
                     "bounds_db": c["bounds_db"],
                     "reference_active_seconds": c["reference_active_seconds"],
                     "destination_active_seconds": c["destination_active_seconds"],
                     "filter_taps": self.eq_profile["filter"]["taps"],
                     "filter_fit_error_db": self.eq_profile["filter"]["fit_error_db"],
                     "warnings": c["warnings"]}
        return {"enabled": self.eq.enabled, "amount": self.eq.amount,
                "preserve_adr_level": self.eq.preserve_adr_level, "state": state,
                "error": self.eq_error if self.eq_failed() else None, "curve": curve,
                "applied_common_gain_db": (self.render.eq_info or {}).get("applied_common_gain_db")
                if self.render is not None else None}

    def snapshot(self) -> dict:
        def asset(slot):
            a = self.assets[slot]
            return None if a is None else {**a.summary(), "channel_mode": self.channels[slot]}

        prof = None
        if self.profile is not None:
            p = self.profile
            prof = {**p.manifest(), "valid": self.profile_ok(),
                    "rt60_s": p.parameters.get("t20_rt60_s"), "envelope_db": _envelope_db(p.wet_ir, p.sample_rate_hz)}
        render = None
        if self.render is not None:
            r = self.render
            render = {"sample_rate_hz": r.sample_rate_hz, "length_frames": int(r.wet.size),
                      "wet_peak_dbfs": _db(r.wet_peak), "mix_peak_dbfs": _db(r.mix_peak), "warnings": r.warnings,
                      "dependency_key": r.dependency_key, "stale": not self.render_ok(), "render_ir": r.ir_info,
                      "eq_applied": bool(r.eq_info)}
        return io.json_safe({
            "app_version": __version__, "dsp_version": DSP_VERSION, "revision": self.revision,
            "job": self.job, "last_error": self.last_error,
            "limits": {"max_file_seconds": MAX_FILE_SECONDS},
            "engine": self.capabilities(),
            "source": asset("source"), "destination": asset("destination"),
            "settings": {"wet_gain_db": self.settings.wet_gain_db,
                         "additional_predelay_ms": self.settings.additional_predelay_seconds * 1000.0},
            "profile": prof, "render": render, "eq": self.eq_snapshot(),
            "last_export": self.last_export, "export_dir": str(self.export_dir),
            "cache_bytes": self.cache_bytes(), "data_dir": str(self.data_dir),
            "exports": self.export_stats(),
        })


def _db(v: float) -> float | None:
    return None if v <= 0 else float(20 * np.log10(v))


def _envelope_db(h: np.ndarray, fs: int, points: int = 300) -> dict:
    if h.size == 0:
        return {"seconds": 0.0, "db": []}
    edges = np.linspace(0, h.size, min(points, h.size) + 1).astype(np.int64)
    peaks = np.maximum.reduceat(np.abs(h), edges[:-1])
    return {"seconds": h.size / fs, "db": np.round(20 * np.log10(np.maximum(peaks, 1e-6)), 2).tolist()}


def _num(payload: dict, key: str, default):
    try:
        v = float(payload.get(key, default))
    except (TypeError, ValueError):
        raise MimeticError("INVALID_PARAMETER", f"{key} invalide")
    if not np.isfinite(v):
        raise MimeticError("INVALID_PARAMETER", f"{key} non fini")
    return v


def create_app(data_dir: Path | None = None, export_dir: Path | None = None, engine_root: Path | None = None,
               estimator: Callable | None = None, capabilities: Callable | None = None) -> FastAPI:
    data_dir = data_dir or default_data_dir()
    project = Project(data_dir, export_dir or data_dir / "exports", engine_root or recrir.PROJECT_ROOT,
                      estimator, capabilities)
    project.reset_dirs()

    app = FastAPI(title="Mimetic beta", version=__version__)
    app.state.project = project

    @app.exception_handler(MimeticError)
    async def _mimetic_error(_req, exc: MimeticError):
        return JSONResponse({"error": exc.to_dict()}, status_code=409 if exc.code == "STALE_RESULT" else 400)

    @app.get("/")
    def index():
        # La page elle-même ne doit pas être mise en cache : sinon un navigateur garde une ancienne
        # interface (anciens boutons, anciennes versions de CSS/JS) après une mise à jour.
        return FileResponse(STATIC_DIR / "index.html",
                            headers={"Cache-Control": "no-store, must-revalidate"})

    @app.get("/api/state")
    def state():
        with project.lock:
            return project.snapshot()

    @app.post("/api/upload/{slot}")
    async def upload(slot: str, request: Request, name: str = "audio.wav"):
        if slot not in SLOTS:
            raise MimeticError("INVALID_PARAMETER", "emplacement inconnu")
        declared = request.headers.get("content-length")
        if declared and int(declared) > MAX_UPLOAD_BYTES:
            raise MimeticError("LIMIT_EXCEEDED", "fichier trop volumineux")
        tmp_path = project.upload_dir / f"{uuid.uuid4().hex}.upload"  # nom interne, jamais le nom utilisateur
        size = 0
        with open(tmp_path, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    f.close()
                    tmp_path.unlink(missing_ok=True)
                    raise MimeticError("LIMIT_EXCEEDED", "fichier trop volumineux")
                f.write(chunk)
        try:
            return await run_in_threadpool(_ingest, slot, tmp_path, os.path.basename(name)[:200])
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _ingest(slot: str, path: Path, name: str):
        a = io.load_wav(path, name)
        with project.lock:
            old = project.assets[slot]
            project.assets[slot] = a
            project.channels[slot] = "mono" if a.channels == 1 else "left"
            if project.job["state"] in ("failed", "cancelled"):
                project.job = {"state": "idle", "step": None}
                project.last_error = None
            project.revision += 1
            if old is not None:
                Path(old.path).unlink(missing_ok=True)
        project.kick()
        with project.lock:
            return project.snapshot()

    @app.get("/api/waveform/{slot}")
    def waveform(slot: str):
        a = project.assets.get(slot)
        if a is None:
            raise MimeticError("INVALID_PARAMETER", "aucun fichier")
        return io.waveform_overview(a.data)

    @app.post("/api/channel/{slot}")
    def set_channel(slot: str, payload: dict | None = None):
        mode = (payload or {}).get("mode")
        with project.lock:
            a = project.assets.get(slot)
            if a is None:
                raise MimeticError("INVALID_PARAMETER", "aucun fichier")
            allowed = ("mono",) if a.channels == 1 else ("left", "right", "mean")
            if mode not in allowed:
                raise MimeticError("INVALID_PARAMETER", f"canal {mode!r} non valide")
            project.channels[slot] = mode
            project.revision += 1
        project.kick()
        with project.lock:
            return project.snapshot()

    @app.post("/api/settings")
    def set_settings(payload: dict | None = None):
        payload = payload or {}
        gain = _num(payload, "wet_gain_db", 0.0)
        delay_ms = _num(payload, "additional_predelay_ms", 0.0)
        if not -60.0 <= gain <= 24.0 or not 0.0 <= delay_ms <= 200.0:
            raise MimeticError("INVALID_PARAMETER", "niveau [-60, 24] dB, délai [0, 200] ms")
        with project.lock:
            project.settings = RenderSettings(gain, delay_ms / 1000.0)
            project.revision += 1
        project.kick()
        with project.lock:
            return project.snapshot()

    @app.post("/api/eq/settings")
    def set_eq(payload: dict | None = None):
        payload = payload or {}
        unknown = set(payload) - {"enabled", "amount", "preserve_adr_level"}
        if unknown:
            raise MimeticError("INVALID_PARAMETER", f"champs inconnus : {', '.join(sorted(unknown))}")
        with project.lock:
            enabled = bool(payload.get("enabled", project.eq.enabled))
            amount = _num(payload, "amount", project.eq.amount)
            preserve = bool(payload.get("preserve_adr_level", project.eq.preserve_adr_level))
            if not 0.0 <= amount <= 1.0:
                raise MimeticError("INVALID_PARAMETER", "intensité hors [0, 1]")
            project.eq = EqSettings(enabled, amount, preserve)
            if not enabled:
                project.eq_error, project.eq_error_deps = None, None
            project.revision += 1
        project.kick()
        with project.lock:
            return project.snapshot()

    @app.post("/api/retry")
    def retry():
        with project.lock:
            project.job = {"state": "idle", "step": None}
            project.last_error = None
        project.kick()
        with project.lock:
            return project.snapshot()

    @app.post("/api/cache/clear")
    def clear_cache():
        """Vide les fichiers importés, les fichiers de travail **et les exports**, puis réinitialise la session."""
        project.cancel()
        with project.lock:
            freed = project.cache_bytes()
            exports = project.delete_exports()
            for slot in SLOTS:
                project.assets[slot] = None
                project.channels[slot] = "mono"
            project.profile = None
            project.profile_deps = None
            project.eq_profile = None
            project.eq_deps = None
            project.eq_error = None
            project.eq_error_deps = None
            project.render = None
            project.last_export = None
            project.last_error = None
            project.job = {"state": "idle", "step": None}
            project.reset_dirs()
            project.revision += 1
            return {**project.snapshot(), "freed_bytes": freed + exports["bytes"],
                    "deleted_exports": exports["count"]}

    @app.post("/api/session/new")
    def new_session():
        project.clear_session()
        with project.lock:
            return project.snapshot()

    @app.post("/api/cancel")
    def cancel():
        project.cancel()
        with project.lock:
            return project.snapshot()

    @app.get("/api/pcm/{kind}")
    def pcm(kind: str):
        """Float32 LE brut pour l'écoute : mêmes tableaux que l'export, sans normalisation."""
        with project.lock:
            if kind in SLOTS:
                a = project.assets[kind]
                if a is None:
                    raise MimeticError("INVALID_PARAMETER", "aucun fichier")
                sig, fs = io.select_channel(a, project.channels[kind]), a.sample_rate_hz
            elif kind in ("dry", "wet", "original"):
                r = project.render
                if r is None:
                    raise MimeticError("INVALID_PARAMETER", "aucun rendu")
                # "dry" = branche directe du rendu courant (corrigée si l'EQ est active),
                # "original" = ADR non corrigé, pour la comparaison avant/après.
                sig = {"dry": r.dry, "wet": r.wet}.get(kind)
                if sig is None:
                    sig = r.original if r.original is not None else r.dry
                fs = r.sample_rate_hz
            else:
                raise MimeticError("INVALID_PARAMETER", "flux inconnu")
        return Response(np.asarray(sig, dtype="<f4").tobytes(), media_type="application/octet-stream",
                        headers={"X-Sample-Rate": str(fs), "Cache-Control": "no-store"})

    @app.post("/api/export")
    def do_export(payload: dict | None = None):
        with project.lock:
            r, dst, prof = project.render, project.assets["destination"], project.profile
            ch, st = project.channels["destination"], project.settings
            if r is None:
                raise MimeticError("INVALID_PARAMETER", "aucun rendu à exporter")
            if not project.render_ok():
                raise MimeticError("STALE_RESULT")
        mode = (payload or {}).get("mode", "wet")
        out = pipeline.export(r, dst, ch, prof, st, project.export_dir, mode=mode)
        with project.lock:
            project.last_export = {"files": out["files"], "directory": out["directory"], "mode": mode}
            project.revision += 1
            return project.snapshot()

    @app.get("/api/exports/{filename}")
    def download(filename: str):
        root = project.export_dir.resolve()
        target = (root / filename).resolve()
        if target.parent != root or not target.is_file() or target.suffix not in (".wav", ".json"):
            raise MimeticError("INVALID_PARAMETER", "fichier introuvable")
        return FileResponse(target, filename=target.name)

    app.mount("/static", NoCacheStatic(directory=STATIC_DIR), name="static")
    return app


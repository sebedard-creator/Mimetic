"""Locate read-only bundled resources and launch the isolated CPU engine."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2] / "payload"


def logs_dir() -> Path:
    configured = os.environ.get("MIMETIC_PORTABLE_LOG_DIR")
    if configured:
        result = Path(configured)
    elif sys.platform == "darwin":
        result = Path.home() / "Library" / "Logs" / "Mimetic"
    else:
        result = Path.home() / ".mimetic-portable" / "logs"
    result.mkdir(parents=True, exist_ok=True)
    return result


def engine_paths() -> dict:
    root = resource_root()
    return {"python": Path(sys.executable), "repo": root / "Rec-RIR",
            "ckpt": root / "Rec-RIR" / "ckpt" / "epoch35.tar", "src": root}


def command(*args) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *map(str, args)]
    return [sys.executable, "-I", "-B", str(Path(__file__).resolve().parents[1] / "app_entry.py"), *map(str, args)]


def launch_engine():
    paths = engine_paths()
    env = dict(os.environ, MIMETIC_RECRIR_REPO=str(paths["repo"]),
               MIMETIC_RECRIR_CKPT=str(paths["ckpt"]), PYTHONUNBUFFERED="1")
    # A frozen executable started as an independent worker must not inherit the
    # outer PyInstaller bootloader's process-level bookkeeping.
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    handle = (logs_dir() / "engine.log").open("ab")
    try:
        proc = subprocess.Popen(command("--engine"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=handle, env=env, text=True, encoding="utf-8", bufsize=1,
                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        return proc, handle
    except BaseException:
        handle.close()
        raise


def configure():
    # pt_api performs validated source-profile loading. Ship its original .py
    # separately rather than pointing that loader at PyInstaller's .pyc archive.
    path = resource_root() / ("vendor/pt_api.py" if getattr(sys, "frozen", False) else "pt_api.py")
    spec = importlib.util.spec_from_file_location("pt_api", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Source pt_api embarqué absent")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pt_api"] = module
    spec.loader.exec_module(module)

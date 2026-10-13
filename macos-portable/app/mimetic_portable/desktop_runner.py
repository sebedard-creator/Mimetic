"""Desktop subprocess protocol, independent of the Cocoa bridge."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import uuid
from pathlib import Path


class Runner:
    def __init__(self, command, deliver, logs: Path):
        self.command, self.deliver, self.logs = command, deliver, logs
        self.proc = None
        self.token = uuid.uuid4().hex
        self.cancelled = False
        self.log_path = logs / f"job-{self.token}.log"
        self.done = threading.Event()

    def start(self, archive: Path, output: Path):
        self.logs.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab")
        env = dict(os.environ, MIMETIC_PORTABLE_LOG_DIR=str(self.logs),
                   PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
        for key in ("PYTHONPATH", "VIRTUAL_ENV", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH"):
            env.pop(key, None)
        try:
            self.proc = subprocess.Popen([*self.command, "--process", str(archive), "--output-dir", str(output)],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log,
                                         env=env, start_new_session=os.name == "posix")
        except BaseException:
            log.close()
            raise
        def read():
            try:
                for line in iter(self.proc.stdout.readline, b""):
                    try:
                        message = json.loads(line.decode("utf-8"))
                    except (ValueError, UnicodeError):
                        self.deliver(self.token, {"event": "error", "error": {
                            "message": "Réponse du moteur illisible.", "detail": str(self.log_path)}})
                        self.cancel()
                        break
                    self.deliver(self.token, message)
            finally:
                code = self.proc.wait()
                self.proc.stdout.close()
                log.close()
                self.deliver(self.token, {"event": "exit", "return_code": code, "cancelled": self.cancelled})
                self.done.set()
        threading.Thread(target=read, name="mimetic-desktop-reader", daemon=True).start()

    def _signal(self, value):
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(proc.pid, value)
            elif value == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except ProcessLookupError:
            pass

    def cancel(self):
        if self.proc is None or self.proc.poll() is not None:
            return
        self.cancelled = True
        self._signal(signal.SIGTERM)
        timer = threading.Timer(3.0, lambda: self._signal(signal.SIGKILL if os.name == "posix" else signal.SIGTERM))
        timer.daemon = True
        timer.start()

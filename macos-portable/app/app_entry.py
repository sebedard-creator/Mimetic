"""Frozen executable entry: engine, batch processing, self-test, or diagnostics."""
from __future__ import annotations

import os
import sys
from pathlib import Path

for stream in (sys.stdin, sys.stdout, sys.stderr):
    if stream is not None and hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")

# Establish a job-owned process group before importing native numeric libraries.
# The Mac launcher can terminate this job and its engine without touching Pro Tools.
if sys.platform == "darwin" and "--process" in sys.argv:
    try:
        os.setpgid(0, 0)
    except PermissionError:
        pass  # the native parent may already have established the group

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))


def main():
    import argparse
    import json
    import signal
    import traceback
    from mimetic_portable.runtime import configure, engine_paths, logs_dir

    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--engine", action="store_true")
    modes.add_argument("--process", type=Path, metavar="SESSION.zip")
    modes.add_argument("--self-test", action="store_true")
    modes.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    configure()
    if args.engine:
        paths = engine_paths()
        os.environ["MIMETIC_RECRIR_REPO"] = str(paths["repo"])
        os.environ["MIMETIC_RECRIR_CKPT"] = str(paths["ckpt"])
        from mimetic.engines.recrir.worker import main as engine_main
        engine_main()
        return 0
    protocol = sys.stdout
    sys.stdout = sys.stderr

    def emit(message):
        protocol.write(json.dumps(message, ensure_ascii=False) + "\n")
        protocol.flush()

    try:
        if args.diagnostics:
            from mimetic_portable.selftest import diagnostics
            emit({"event": "diagnostics", **diagnostics()})
            return 0
        if args.self_test:
            from mimetic_portable.selftest import run
            run(emit)
            return 0
        from mimetic_portable.job import Job
        from mimetic.domain.errors import MimeticError
        job = Job(emit=emit)

        def cancel(signum, frame):
            job.cancelled.set()
            job.client.cancelled = True
            raise MimeticError("CANCELLED")

        signal.signal(signal.SIGTERM, cancel)
        signal.signal(signal.SIGINT, cancel)
        job.run(args.process, args.output_dir)
        return 0
    except BaseException as exc:
        from mimetic.domain.errors import MimeticError
        if isinstance(exc, MimeticError) and exc.code == "CANCELLED":
            emit({"event": "cancelled"})
            return 130
        traceback.print_exc(file=sys.stderr)
        with (logs_dir() / "jobs.log").open("a", encoding="utf-8") as log:
            traceback.print_exc(file=log)
        error = exc.to_dict() if isinstance(exc, MimeticError) else {
            "code": "PTX_EXPORT_FAILED", "message": "Le traitement a échoué.",
            "detail": f"{type(exc).__name__}: {exc}"}
        emit({"event": "error", "error": error, "log": str(logs_dir())})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

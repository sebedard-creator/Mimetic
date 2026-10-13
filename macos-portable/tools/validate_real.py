"""Private real-session smoke test; its media stay in build/, never in transfer ZIP."""
import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    args = parser.parse_args()
    session = args.session.resolve(strict=True)
    test_dir = ROOT / "build" / "real-session-check"
    test_dir.mkdir(parents=True, exist_ok=True)
    archive = test_dir / "RealSession.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.write(session, "Dialogue/" + session.name)
        for file in (session.parent / "Audio Files").glob("*.wav"):
            bundle.write(file, "Dialogue/Audio Files/" + file.name)
    env = dict(os.environ, MIMETIC_PORTABLE_LOG_DIR=str(test_dir / "logs"))
    env["PYTHONIOENCODING"] = "utf-8"
    log_path = test_dir / "events.jsonl"
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run([sys.executable, str(ROOT / "app" / "app_entry.py"), "--process", str(archive)],
                                env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
    (test_dir / "exit_code.txt").write_text(str(result.returncode), encoding="utf-8")
    print(log_path.read_text(encoding="utf-8"))
    raise SystemExit(result.returncode)

"""Exercise current CLI protocol, including French/Unicode names, in UTF-8."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "payload")]
from mimetic_portable.runtime import configure
configure()
from mimetic_portable.selftest import build_fixture

if __name__ == "__main__":
    work = ROOT / "build" / "cli-check"
    work.mkdir(parents=True, exist_ok=True)
    original = build_fixture(work / "fixture")
    archive = original.with_name("Scène de contrôle.zip")
    if archive.exists():
        archive.unlink()
    original.rename(archive)
    env = dict(os.environ, MIMETIC_PORTABLE_LOG_DIR=str(work / "logs"))
    result = subprocess.run([sys.executable, str(ROOT / "app" / "app_entry.py"), "--process", str(archive)],
                            capture_output=True, env=env, timeout=180)
    text = result.stdout.decode("utf-8", errors="strict")
    messages = [json.loads(line) for line in text.splitlines()]
    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert any(m.get("event") == "workspace" for m in messages)
    assert messages[-1]["event"] == "ready" and messages[-1]["total"] == 1
    assert "Scène de contrôle_Mimetic" in messages[-1]["path"]
    assert Path(messages[-1]["path"]).is_file()
    (work / "events.jsonl").write_text(text, encoding="utf-8")
    print(json.dumps({"utf8_protocol": True, "unicode_filename": True, "exit_code": result.returncode,
                      "ready": messages[-1]}, ensure_ascii=True))

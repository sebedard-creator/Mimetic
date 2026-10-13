import json
import os
import sys
import threading
from pathlib import Path

from mimetic_portable.desktop_runner import Runner


def test_desktop_drains_ready_before_exit_and_preserves_unicode(tmp_path):
    script = """
import json,sys
sys.stdout.reconfigure(encoding='utf-8')
for m in [{'event':'progress','step':'Création du ZIP'}, {'event':'ready','path':'Scène_Mimetic.zip'}]:
    print(json.dumps(m,ensure_ascii=False),flush=True)
"""
    received = []
    runner = Runner([sys.executable, "-I", "-c", script], lambda token, event: received.append(event), tmp_path)
    runner.start(tmp_path / "unused.zip", tmp_path)
    assert runner.done.wait(10)
    assert [m["event"] for m in received] == ["progress", "ready", "exit"]
    assert received[1]["path"] == "Scène_Mimetic.zip"
    assert received[-1]["return_code"] == 0 and received[-1]["cancelled"] is False


def test_desktop_cancels_its_own_process_without_reporting_success(tmp_path):
    script = "import json,time; print(json.dumps({'event':'progress'}),flush=True); time.sleep(60)"
    entered = threading.Event()
    received = []
    def deliver(token, event):
        received.append(event)
        if event["event"] == "progress":
            entered.set()
    runner = Runner([sys.executable, "-I", "-c", script], deliver, tmp_path)
    runner.start(tmp_path / "unused.zip", tmp_path)
    assert entered.wait(5)
    runner.cancel()
    assert runner.done.wait(10)
    assert received[-1]["event"] == "exit" and received[-1]["cancelled"] is True
    assert not any(m["event"] == "ready" for m in received)
    assert runner.proc.poll() is not None

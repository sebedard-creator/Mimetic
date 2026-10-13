import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "payload")]
os.environ.setdefault("MIMETIC_PORTABLE_LOG_DIR", str(ROOT / "build" / "test-logs"))
from mimetic_portable.runtime import configure
configure()

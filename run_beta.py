"""Lanceur de la beta sans installation : python run_beta.py [--port 8765] [--host 127.0.0.1]."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from mimetic.web.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()

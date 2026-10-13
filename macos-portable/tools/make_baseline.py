"""Record an inference reference with the currently validated Windows engine."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "payload")]
from mimetic_portable.runtime import configure
configure()
from mimetic_portable.selftest import raw_engine
import numpy as np

if __name__ == "__main__":
    target = ROOT / "validation"
    target.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        rir, diagnostic = raw_engine(Path(temporary))
    np.save(target / "engine_baseline.npy", rir, allow_pickle=False)
    (target / "engine_baseline.json").write_text(json.dumps({
        "input": "mimetic_portable.selftest.synthetic_input, 2 seconds at 16kHz; no production audio",
        "source": "validated Windows CPU environment", "diagnostics": diagnostic,
        "relative_l2_tolerance": 0.002,
    }, indent=2), encoding="utf-8")
    manifest_file = ROOT / "payload" / "snapshot.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    import shutil
    for file in target.iterdir():
        bundled = ROOT / "payload" / "validation" / file.name
        shutil.copy2(file, bundled)
        manifest["files"][bundled.relative_to(ROOT / "payload").as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(diagnostic, indent=2))

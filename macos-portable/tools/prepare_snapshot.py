"""Create an offline source payload; never copy production media or environments."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PT_API_SHA = "a160557878de7920a46f3d8e280f3e1e7100fad2555cd8f718de2995e10f832e"
WEIGHTS_SHA = "afe36e477f6fb3656d162d906a47da2d40a789634b476564266fc1ba36037478"
REVISION = "b3ac6fc1421bd58e017022037feb14f30366b46f"
PT_API_REVISION = "c31147bf427e763f7be900e6077368b615d47e24"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_payload():
    payload = ROOT / "payload"
    manifest = json.loads((payload / "snapshot.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        path = (payload / relative).resolve()
        if not path.is_relative_to(payload.resolve()) or not path.is_file():
            raise RuntimeError(f"Ressource absente : {relative}")
        if sha(path) != expected:
            raise RuntimeError(f"Ressource modifiée depuis le snapshot : {relative}")
    if "validation/engine_baseline.npy" not in manifest["files"]:
        raise RuntimeError("Référence synthétique de comparaison absente.")
    return manifest


def pinned_repository(name: str, url: str, revision: str) -> Path:
    target = ROOT / "build" / "vendor" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        subprocess.run(["git", "clone", "--no-checkout", url, str(target)], check=True)
        subprocess.run(["git", "-C", str(target), "checkout", "--detach", revision], check=True)
    actual = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(target), "status", "--porcelain", "--untracked-files=no"], text=True)
    if actual != revision or dirty:
        raise ValueError(f"Cache {name} modifié ou à une autre révision : {target}")
    return target


def prepare(project: Path, api_source: Path, api_repo: Path, engine: Path | None = None):
    payload = ROOT / "payload"
    payload.mkdir(exist_ok=True)
    if hashlib.sha256(api_source.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != PT_API_SHA:
        raise ValueError("Le source pt_api doit être celui validé de la version 1.6.3.")
    engine = engine or project / "third_party" / "Rec-RIR"
    if sha(engine / "ckpt" / "epoch35.tar") != WEIGHTS_SHA:
        raise ValueError("Poids Rec-RIR différents des poids validés.")
    actual = subprocess.check_output(["git", "-C", str(engine), "rev-parse", "HEAD"], text=True).strip()
    if actual != REVISION:
        raise ValueError(f"Révision Rec-RIR inattendue : {actual}")
    for name in ("engine_baseline.npy", "engine_baseline.json"):
        source = ROOT / "validation" / name
        target = payload / "validation" / name
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(source, target)
    # Rebuilding the payload is scoped to this exact, resolved private directory.
    for name in ("mimetic", "Rec-RIR", "licenses"):
        target = (payload / name).resolve()
        if target.parent != payload.resolve():
            raise ValueError("Cible du snapshot invalide")
        if target.exists():
            shutil.rmtree(target)
    mimetic = payload / "mimetic"
    sources = project / "src" / "mimetic"
    original = {}
    for source in sources.rglob("*.py"):
        rel = source.relative_to(sources)
        if rel.parts[0] in ("web", "cli") or rel.name == "__main__.py":
            continue
        target = mimetic / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        original[rel.as_posix()] = sha(source)
    shutil.copytree(sources / "resources", mimetic / "resources")
    adapter = mimetic / "engines" / "recrir" / "adapter.py"
    content = adapter.read_text(encoding="utf-8")
    start = content.index("def paths(root: Path = PROJECT_ROOT) -> dict:")
    end = content.index("\n\n_hash_cache", start)
    content = content[:start] + '''def paths(root: Path = PROJECT_ROOT) -> dict:
    from mimetic_portable.runtime import engine_paths
    return engine_paths()
''' + content[end:]
    start = content.index("    def _start(self) -> subprocess.Popen:")
    end = content.index("\n    def request(", start)
    content = content[:start] + '''    def _start(self) -> subprocess.Popen:
        from mimetic_portable.runtime import launch_engine
        proc, self._stderr = launch_engine()
        return proc
''' + content[end:]
    # The upstream reader protocol is retained; close handles after every job.
    start = content.index("    def stop(self) -> None:")
    end = content.index("\n\ndef estimate_profile", start)
    content = content[:start] + '''    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                proc.stdin.close()
                proc.wait(timeout=2)
            except Exception:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)
            finally:
                for handle in (proc.stdin, proc.stdout):
                    if handle is not None:
                        handle.close()
        handle = getattr(self, "_stderr", None)
        if handle is not None:
            handle.close()
            self._stderr = None
''' + content[end:]
    adapter.write_text(content, encoding="utf-8", newline="\n")
    for folder in ("model", "acoustics", "method"):
        for source in (engine / folder).rglob("*.py"):
            target = payload / "Rec-RIR" / source.relative_to(engine)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for rel in ("ckpt/epoch35.tar", "LICENSE", "README.md"):
        target = payload / "Rec-RIR" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(engine / rel, target)
    shutil.copy2(api_source, payload / "pt_api.py")
    licenses = payload / "licenses"
    licenses.mkdir()
    shutil.copy2(project / "LICENSE", licenses / "Mimetic-MIT.txt")
    shutil.copy2(engine / "LICENSE", licenses / "Rec-RIR-MIT.txt")
    for name in ("LICENSE", "COPYING", "NOTICE.md", "changelog.md"):
        shutil.copy2(api_repo / name, licenses / ("pt_api-" + name))
    manifest = {
        "mimetic_version": "1.5.0", "pt_api_version": "1.6.3",
        "pt_api_commit": "c31147bf427e763f7be900e6077368b615d47e24",
        "recrir_commit": REVISION,
        "local_changes": ["mimetic/engines/recrir/adapter.py: paths, launcher and process cleanup only"],
        "original_mimetic_sha256": original,
        "files": {p.relative_to(payload).as_posix(): sha(p) for p in sorted(payload.rglob("*"))
                  if p.is_file() and p.name != "snapshot.json" and "__pycache__" not in p.parts},
    }
    (payload / "snapshot.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Snapshot prêt : {payload}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=ROOT.parent)
    parser.add_argument("--api-source", type=Path)
    parser.add_argument("--api-repo", type=Path)
    parser.add_argument("--engine-repo", type=Path)
    args = parser.parse_args()
    api_repo = args.api_repo or pinned_repository(
        "pt_api", "https://github.com/sebedard-creator/pt_api.git", PT_API_REVISION)
    api_source = args.api_source or api_repo / "pt_api.py"
    engine = args.engine_repo or args.project / "third_party" / "Rec-RIR"
    if not engine.exists():
        engine = pinned_repository("Rec-RIR", "https://github.com/Audio-WestlakeU/Rec-RIR.git", REVISION)
    prepare(args.project.resolve(), api_source.resolve(), api_repo.resolve(), engine.resolve())
    verify_payload()

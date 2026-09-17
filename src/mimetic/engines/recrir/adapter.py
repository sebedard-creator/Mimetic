"""Adaptateur Rec-RIR côté application (sans PyTorch) : préparation, sous-processus, canonicalisation.

Architecture §6.2–6.5, §8. L'inférence tourne dans `.venv-engine` via `worker.py`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy import signal as sps

from mimetic import DSP_VERSION
from mimetic.audio import dsp, hybrid
from mimetic.domain.errors import MimeticError
from mimetic.domain.models import RoomProfile

ENGINE_ID = "recrir"
ADAPTER_VERSION = "0.1.0"
NATIVE_SR = 16000
CODE_REVISION = "b3ac6fc1421bd58e017022037feb14f30366b46f"
WEIGHTS_SHA256 = "afe36e477f6fb3656d162d906a47da2d40a789634b476564266fc1ba36037478"
PROJECT_ROOT = Path(__file__).resolve().parents[4]

# Fenêtres d'analyse : le modèle a été entraîné sur des segments de 4 à 6 s (config/Rec-RIR.toml).
WINDOW_SECONDS = 6.0
MIN_WINDOW_SECONDS = 2.0
MAX_WINDOWS = 3
WORKER_TIMEOUT_S = 900.0


@dataclass(frozen=True)
class DirectPolicy:
    """Séparation direct / réflexions pour l'IR totale de Rec-RIR (cas 3 du §6.4). Réglée sur le pilote synthétique."""
    name: str = "recrir-direct-v1"
    search_ms: float = 5.0       # recherche d'un pic direct antérieur au maximum global
    strong_ratio: float = 0.5    # un pic antérieur ≥ 50 % du maximum est pris comme direct
    pre_ms: float = 1.0          # étendue du paquet direct avant son pic (lobe du filtre à bande limitée)
    post_ms: float = 1.0         # étendue après le pic, mise à zéro dans le wet
    taper_ms: float = 0.5        # remontée douce après la fenêtre directe
    horizon_s: float = 1.0       # horizon utile (CTF 60 trames × 256 / 16 kHz + fenêtre)
    min_peak_to_context_db: float = 6.0  # direct < contexte proche + 6 dB → DIRECT_PATH_UNRESOLVED


DEFAULT_POLICY = DirectPolicy()


# ---------------------------------------------------------------------------------------- disponibilité

def paths(root: Path = PROJECT_ROOT) -> dict:
    return {
        "python": root / ".venv-engine" / "Scripts" / "python.exe",
        "repo": root / "third_party" / "Rec-RIR",
        "ckpt": root / "third_party" / "Rec-RIR" / "ckpt" / "epoch35.tar",
        "src": root / "src",
    }


_hash_cache: dict[str, str] = {}


def _sha256(path: Path) -> str:
    key = f"{path}:{path.stat().st_mtime_ns}:{path.stat().st_size}"
    if key not in _hash_cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _hash_cache[key] = h.hexdigest()
    return _hash_cache[key]


def capabilities(root: Path = PROJECT_ROOT) -> dict:
    p = paths(root)
    base = {
        "engine_id": ENGINE_ID, "code_revision": CODE_REVISION, "adapter_version": ADAPTER_VERSION,
        "native_sample_rate_hz": NATIVE_SR, "native_bandwidth_hz": NATIVE_SR / 2,
        "rir_horizon_seconds": DEFAULT_POLICY.horizon_s, "supported_channels": 1,
        "direct_representation": "total_ir_estimated_direct", "gain_calibration": "energy_equivalent",
        "status": "experimental", "device": "cpu",
    }
    missing = [k for k in ("python", "repo", "ckpt") if not p[k].exists()]
    if missing:
        return {**base, "available": False, "reason": "MODEL_UNAVAILABLE", "detail": f"absent : {', '.join(missing)}"}
    if _sha256(p["ckpt"]) != WEIGHTS_SHA256:
        return {**base, "available": False, "reason": "MODEL_UNAVAILABLE", "detail": "empreinte des poids inattendue"}
    return {**base, "available": True, "weights_sha256": WEIGHTS_SHA256}


# ---------------------------------------------------------------------------------------- préparation

def to_native(x: np.ndarray, fs: int) -> np.ndarray:
    """Conversion anti-repliement vers 16 kHz (copie d'analyse uniquement)."""
    g = math.gcd(NATIVE_SR, fs)
    return sps.resample_poly(np.asarray(x, dtype=np.float64), NATIVE_SR // g, fs // g)


ACTIVITY_FRAME_S = 0.020
ACTIVITY_RANGE_DB = 35.0
MIN_ACTIVITY = 0.15


def activity_profile(x: np.ndarray, fs: int) -> np.ndarray:
    """Trames de 20 ms « actives » : énergie à moins de 35 dB de la trame la plus forte du fichier.
    Indice simple d'activité, pas un détecteur de parole : musique ou bruit fort comptent aussi."""
    k = max(1, int(ACTIVITY_FRAME_S * fs))
    n = x.size // k
    if n == 0:
        return np.zeros(0, dtype=bool)
    e = np.mean(x[: n * k].reshape(n, k) ** 2, axis=1)
    peak = float(e.max())
    if peak <= 0:
        return np.zeros(n, dtype=bool)
    return e >= peak * 10 ** (-ACTIVITY_RANGE_DB / 10)


def select_windows(x: np.ndarray, fs: int) -> tuple[list[tuple[int, int]], list[float]]:
    """Choisit jusqu'à 3 fenêtres de 6 s sans recouvrement parmi les plus actives du fichier source.

    Les fenêtres gardent pauses et queues (aucune concaténation de segments parlés, §6.2).
    Retourne (fenêtres en indices à fs, taux d'activité de chacune), triées dans le temps.
    """
    total = x.size / fs
    if total < MIN_WINDOW_SECONDS:
        raise MimeticError("INSUFFICIENT_SPEECH", f"source de {total:.2f} s < {MIN_WINDOW_SECONDS:.0f} s")
    act = activity_profile(x, fs)
    k = max(1, int(ACTIVITY_FRAME_S * fs))
    if total <= WINDOW_SECONDS:
        rate = float(act.mean()) if act.size else 0.0
        if rate < MIN_ACTIVITY:
            raise MimeticError("INSUFFICIENT_SPEECH", "trop peu de signal actif dans la source")
        return [(0, x.size)], [rate]
    w_frames = int(WINDOW_SECONDS / ACTIVITY_FRAME_S)
    csum = np.concatenate([[0], np.cumsum(act)])
    hop = max(1, int(0.5 / ACTIVITY_FRAME_S))
    starts = np.arange(0, act.size - w_frames + 1, hop)
    rates = (csum[starts + w_frames] - csum[starts]) / w_frames
    chosen: list[int] = []
    for i in np.argsort(-rates, kind="stable"):
        s = int(starts[i])
        if rates[i] < MIN_ACTIVITY:
            break
        if all(abs(s - c) >= w_frames for c in chosen):
            chosen.append(s)
        if len(chosen) == MAX_WINDOWS:
            break
    if not chosen:
        raise MimeticError("INSUFFICIENT_SPEECH", "trop peu de signal actif dans la source")
    chosen.sort()
    rate_of = {int(s): float(r) for s, r in zip(starts, rates)}
    return [(s * k, s * k + w_frames * k) for s in chosen], [rate_of[s] for s in chosen]


# ---------------------------------------------------------------------------------------- canonicalisation

def canonicalize(raw: np.ndarray, policy: DirectPolicy = DEFAULT_POLICY) -> dict:
    """IR totale brute → wet aligné (direct à n=0, retiré), gain relatif à un direct équivalent unitaire."""
    h = np.asarray(raw, dtype=np.float64)
    if h.size == 0 or not np.all(np.isfinite(h)) or not np.any(h):
        raise MimeticError("DIRECT_PATH_UNRESOLVED", "IR estimée vide ou non finie")
    fs = NATIVE_SR
    ms = lambda v: int(round(v * fs / 1000))  # noqa: E731
    a = np.abs(h)
    peak = int(np.argmax(a))
    lo = max(0, peak - ms(policy.search_ms))
    earlier = np.where(a[lo:peak + 1] >= policy.strong_ratio * a[peak])[0]
    # pic local le plus précoce parmi les candidats forts
    d = lo + int(earlier[0])
    while d + 1 < h.size and a[d + 1] > a[d]:
        d += 1
    reasons: list[str] = []

    p0, p1 = max(0, d - ms(policy.pre_ms)), d + ms(policy.post_ms) + 1
    e_direct = float(np.sum(h[p0:p1] ** 2))
    ctx = np.concatenate([h[max(0, p0 - ms(20)):p0], h[p1:p1 + ms(20)]])
    ctx_peak = float(np.max(np.abs(ctx))) if ctx.size else 0.0
    peak_to_ctx_db = 20 * math.log10(a[d] / ctx_peak) if ctx_peak > 0 else float("inf")
    if e_direct <= 0 or peak_to_ctx_db < policy.min_peak_to_context_db:
        reasons.append("DIRECT_PATH_UNRESOLVED")

    n = int(policy.horizon_s * fs)
    seg = h[d:d + n]
    wet = np.zeros(n)
    wet[:seg.size] = seg
    post = ms(policy.post_ms) + 1
    taper = max(1, ms(policy.taper_ms))
    wet[:post] = 0.0
    wet[post:post + taper] *= 0.5 - 0.5 * np.cos(np.pi * (np.arange(min(taper, n - post)) + 1) / (taper + 1))
    wet /= math.sqrt(e_direct) if e_direct > 0 else 1.0

    # Énergie hors fenêtre utile : avant le paquet direct et après l'horizon (diagnostics, pas de correction).
    e_total = float(np.sum(h**2))
    pre_ratio = float(np.sum(h[:p0] ** 2) / e_total)
    beyond_ratio = float(np.sum(h[d + n:] ** 2) / e_total)
    # Queue encore significative en fin d'horizon ?
    env = np.sqrt(np.convolve(wet**2, np.ones(ms(50)) / ms(50), mode="same"))
    ref = float(np.max(env)) if np.any(env) else 0.0
    end_db = 20 * math.log10(max(float(np.mean(env[-ms(100):])), 1e-12) / ref) if ref > 0 else None
    if end_db is not None and end_db > -40.0:
        reasons.append("TAIL_TRUNCATED")
    reasons.append("BANDWIDTH_LIMITED")

    return {
        "wet": wet,
        "direct_index_raw": d,
        "global_peak_index_raw": peak,
        "direct_window_frames": [p0 - d, p1 - d],
        "direct_energy": e_direct,
        "peak_to_context_db": peak_to_ctx_db,
        "pre_direct_energy_ratio": pre_ratio,
        "beyond_horizon_energy_ratio": beyond_ratio,
        "tail_end_level_db": end_db,
        "drr_db": dsp.drr_db_of_wet(wet),
        "t20_rt60_s": dsp.schroeder_rt(wet, fs),
        "reasons": reasons,
    }


def choose_medoid(results: list[dict]) -> int:
    """Médoïde selon (RT60 T20, DRR) normalisés ; aucune moyenne d'échantillons (§6.3)."""
    if len(results) == 1:
        return 0
    feats = []
    for r in results:
        rt = r["t20_rt60_s"] if r["t20_rt60_s"] is not None else np.nan
        feats.append([math.log(rt) if rt and rt > 0 else np.nan, r["drr_db"] if r["drr_db"] is not None else np.nan])
    f = np.array(feats, dtype=float)
    scale = np.array([0.2, 3.0])  # ~20 % de RT60, 3 dB de DRR
    cost = []
    for i in range(len(f)):
        diff = np.abs(f - f[i]) / scale
        diff = np.where(np.isnan(diff), 10.0, diff)
        cost.append(float(np.sum(diff)))
    return int(np.argmin(cost))


# ---------------------------------------------------------------------------------------- sous-processus

class WorkerClient:
    """Sous-processus persistant démarré à la demande ; un seul job à la fois ; annulation par arrêt borné."""

    def __init__(self, root: Path = PROJECT_ROOT):
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.cancelled = False

    def _start(self) -> subprocess.Popen:
        p = paths(self.root)
        env = {**os.environ, "PYTHONPATH": str(p["src"]), "MIMETIC_RECRIR_REPO": str(p["repo"]),
               "MIMETIC_RECRIR_CKPT": str(p["ckpt"]), "PYTHONUNBUFFERED": "1"}
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        log_dir = self.root / ".engine-logs"
        log_dir.mkdir(exist_ok=True)
        self._stderr = open(log_dir / "recrir-worker.log", "ab")
        return subprocess.Popen([str(p["python"]), "-m", "mimetic.engines.recrir.worker"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr,
                                env=env, creationflags=flags, text=True, encoding="utf-8", bufsize=1)

    def request(self, payload: dict, timeout: float = WORKER_TIMEOUT_S) -> dict:
        if self.proc is None or self.proc.poll() is not None:
            self.proc = self._start()
        proc = self.proc
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()
        box: dict = {}
        reader = threading.Thread(target=lambda: box.setdefault("line", proc.stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout)
        if self.cancelled:
            raise MimeticError("CANCELLED")
        if reader.is_alive():
            self.stop()
            raise MimeticError("ENGINE_FAILURE", "délai dépassé")
        line = box.get("line", "")
        if not line:
            self.proc = None
            raise MimeticError("ENGINE_FAILURE", "le worker s'est arrêté (voir .engine-logs/recrir-worker.log)")
        return json.loads(line)

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


def estimate_profile(signal: np.ndarray, fs: int, *, workdir: Path, client: WorkerClient,
                     reference_meta: dict, progress=lambda step: None,
                     policy: DirectPolicy = DEFAULT_POLICY) -> RoomProfile:
    """Sélection mono à fs → fenêtres 16 kHz → inférence → canonicalisation → médoïde → RoomProfile."""
    import soundfile as sf

    caps = capabilities(client.root)
    if not caps["available"]:
        raise MimeticError("MODEL_UNAVAILABLE", caps.get("detail"))
    x = np.asarray(signal, dtype=np.float64)
    if not np.any(x):
        raise MimeticError("EMPTY_REFERENCE", "sélection silencieuse")
    windows, activity = select_windows(x, fs)
    workdir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, (s, e) in enumerate(windows):
        if client.cancelled:
            raise MimeticError("CANCELLED")
        progress(f"Analyse de la pièce — passage {i + 1}/{len(windows)}")
        native = to_native(x[s:e], fs)
        if not np.any(native):
            continue
        in_path = workdir / f"win{i}.wav"
        out_path = workdir / f"win{i}.npy"
        sf.write(str(in_path), native.astype(np.float32), NATIVE_SR, subtype="FLOAT")
        resp = client.request({"cmd": "estimate", "job_id": uuid.uuid4().hex,
                               "input_wav": str(in_path), "output_npy": str(out_path)})
        if not resp.get("ok"):
            err = resp.get("error") or {}
            raise MimeticError(err.get("code", "ENGINE_FAILURE"), err.get("detail"))
        raw = np.load(out_path, allow_pickle=False)
        c = canonicalize(raw, policy)
        c["window_frames"] = [int(s), int(e)]
        c["activity_rate"] = activity[i]
        c["worker"] = resp.get("diagnostics", {})
        results.append(c)
    if not results:
        raise MimeticError("INSUFFICIENT_SPEECH", "fenêtres silencieuses")

    # Indices plutôt qu'objets : comparer ces dictionnaires (qui contiennent des tableaux) avec == échoue.
    usable = [i for i, r in enumerate(results) if "DIRECT_PATH_UNRESOLVED" not in r["reasons"]]
    pool = usable or list(range(len(results)))
    best_index = pool[choose_medoid([results[i] for i in pool])]
    best = results[best_index]
    reasons = sorted(set(best["reasons"]))
    spread = None
    rts = [r["t20_rt60_s"] for r in results if r["t20_rt60_s"]]
    drrs = [r["drr_db"] for r in results if r["drr_db"] is not None]
    if len(results) > 1 and rts and drrs:
        spread = {"rt60_ratio": max(rts) / min(rts), "drr_db_range": max(drrs) - min(drrs)}
        if spread["rt60_ratio"] > 1.5 or spread["drr_db_range"] > 6.0:
            reasons.append("REFERENCE_INCONSISTENT")

    per_window = [{k: v for k, v in r.items() if k != "wet"} for r in results]
    extension = hybrid.fit(best["wet"], NATIVE_SR)
    reasons = [r for r in reasons if r != "BANDWIDTH_LIMITED"] + ["HF_SYNTHESIZED"]
    return RoomProfile(
        profile_id=uuid.uuid4().hex,
        engine_id=ENGINE_ID,
        status="experimental",
        wet_ir=best["wet"],
        sample_rate_hz=NATIVE_SR,
        time_origin="estimated_direct_arrival",
        direct_removal={"method": policy.name, "window_frames": best["direct_window_frames"],
                        "validated": False, "policy": asdict(policy)},
        gain_calibration="energy_equivalent",
        drr_db=best["drr_db"],
        usable_band_hz=[None, NATIVE_SR / 2],
        tail_horizon_seconds=policy.horizon_s,
        quality_reasons=sorted(set(reasons)),
        parameters={"reference": reference_meta, "windows": per_window,
                    "chosen_window": best_index, "window_spread": spread,
                    "t20_rt60_s": best["t20_rt60_s"]},
        provenance={"code_revision": CODE_REVISION, "weights_sha256": WEIGHTS_SHA256,
                    "adapter_version": ADAPTER_VERSION, "dsp_version": DSP_VERSION,
                    "mamba": "pytorch_cpu_reference_port"},
        seed=2026,
        extension=extension,
    )

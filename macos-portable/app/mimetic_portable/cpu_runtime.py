"""Choose a CPU thread count using the actual bundled Torch build.

Only the portable worker calls this module. Model weights, precision and
analysis windows are unchanged; the web service keeps its existing policy.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

CACHE_VERSION = 2
MAX_THREADS = 8


def configure(cache_dir: Path | None = None):
    import torch
    from mimetic.engines.recrir import mamba_ref

    scan = mamba_ref.selective_scan
    logical = max(1, os.cpu_count() or 1)
    cpu = platform.processor()
    if sys.platform == "darwin":
        try:
            result = subprocess.run(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                                    capture_output=True, text=True, timeout=2, check=True)
            cpu = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    fingerprint = {"version": CACHE_VERSION, "torch": torch.__version__,
                   "system": platform.platform(), "machine": platform.machine(),
                   "cpu": cpu, "logical_cpus": logical}
    ident = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:20]
    cache_root = cache_dir or (Path.home() / "Library/Caches/Mimetic" if sys.platform == "darwin"
                              else Path.home() / ".cache/mimetic-portable")
    cache_file = cache_root / f"cpu-{ident}.json"
    try:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        threads = cached["threads"]
        if (cached["fingerprint"] == fingerprint and cached["scan"] == "bmm"
                and type(threads) is int and 1 <= threads <= min(logical, MAX_THREADS)):
            torch.set_num_threads(threads)
            return {**cached, "cache_hit": True}
    except (OSError, ValueError, KeyError, TypeError):
        pass

    # Shape of a Rec-RIR narrow-band Mamba call, with a short sequence so this
    # first-run calibration remains small. Local RNG does not touch model RNG.
    start = time.perf_counter()
    torch.set_num_threads(1)
    generator = torch.Generator().manual_seed(2026)
    b, length, inner, state = 257, 24, 192, 16
    u = torch.randn(b, length, inner, generator=generator)
    delta = torch.nn.functional.softplus(torch.randn(b, length, inner, generator=generator))
    A = -torch.arange(1, state + 1).float().repeat(inner, 1)
    B = torch.randn(b, length, state, generator=generator)
    C = torch.randn(b, length, state, generator=generator)
    candidates = []
    with torch.no_grad():
        expected = scan(u, delta, A, B, C)
        counts = sorted({min(logical, n) for n in (1, 2, 4, MAX_THREADS)})
        for threads in counts:
            torch.set_num_threads(threads)
            actual = scan(u, delta, A, B, C)
            if not torch.allclose(actual, expected, rtol=2e-5, atol=1e-5):
                continue
            times = []
            for _ in range(3):
                begin = time.perf_counter()
                scan(u, delta, A, B, C)
                times.append(time.perf_counter() - begin)
            candidates.append({"scan": "bmm", "threads": threads,
                               "seconds": statistics.median(times)})
    winner = min(candidates, key=lambda c: c["seconds"])
    torch.set_num_threads(winner["threads"])
    policy = {"fingerprint": fingerprint, "scan": winner["scan"], "threads": winner["threads"],
              "candidates": candidates, "tuning_seconds": time.perf_counter() - start}
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        temporary = cache_file.with_suffix("." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(policy, indent=2), encoding="utf-8")
        temporary.replace(cache_file)
    except OSError:
        pass  # An unavailable cache must not prevent audio processing.
    return {**policy, "cache_hit": False}

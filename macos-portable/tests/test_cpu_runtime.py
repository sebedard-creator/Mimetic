"""Numerical and cache checks for the portable CPU policy, with real Torch."""
import torch
import json

from mimetic.engines.recrir import mamba_ref
from mimetic_portable import cpu_runtime


def test_cpu_tuning_preserves_rng_and_reuses_cache_without_benchmark(tmp_path, monkeypatch):
    monkeypatch.setattr(cpu_runtime.os, "cpu_count", lambda: 2)
    previous_scan, previous_threads = mamba_ref.selective_scan, torch.get_num_threads()
    before = torch.random.get_rng_state().clone()
    try:
        policy = cpu_runtime.configure(tmp_path)
        assert policy["cache_hit"] is False and policy["threads"] in (1, 2)
        assert policy["scan"] == "bmm"
        assert policy["candidates"] and torch.equal(before, torch.random.get_rng_state())
        assert torch.get_num_threads() == policy["threads"]
        assert mamba_ref.selective_scan is previous_scan
        def unexpected_benchmark(*args, **kwargs):
            raise AssertionError("A valid cache must avoid running the calibration again")
        monkeypatch.setattr(torch, "randn", unexpected_benchmark)
        cached = cpu_runtime.configure(tmp_path)
        assert cached["cache_hit"] is True
        assert (cached["scan"], cached["threads"]) == (policy["scan"], policy["threads"])
    finally:
        mamba_ref.selective_scan = previous_scan
        torch.set_num_threads(previous_threads)


def test_cpu_cache_is_recalibrated_when_configuration_is_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(cpu_runtime.os, "cpu_count", lambda: 1)
    previous_threads = torch.get_num_threads()
    try:
        cpu_runtime.configure(tmp_path)
        cache = next(tmp_path.glob("cpu-*.json"))
        invalid = json.loads(cache.read_text())
        invalid["threads"] = 99
        cache.write_text(json.dumps(invalid))
        policy = cpu_runtime.configure(tmp_path)
        assert policy["cache_hit"] is False and policy["threads"] == 1
    finally:
        torch.set_num_threads(previous_threads)


def test_cpu_calibration_works_without_writable_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cpu_runtime.os, "cpu_count", lambda: 1)
    previous_threads = torch.get_num_threads()
    cache = tmp_path / "occupied"
    cache.write_text("This file prevents creating a cache directory.")
    try:
        policy = cpu_runtime.configure(cache)
        assert policy["threads"] == 1 and policy["cache_hit"] is False
    finally:
        torch.set_num_threads(previous_threads)

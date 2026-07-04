#!/usr/bin/env python3
"""Hidden eval for ttl-lru-cache."""
import importlib
import inspect
import json
import random
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class ReferenceTTLCache:
    def __init__(self, capacity, ttl_seconds, clock):
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.data = OrderedDict()

    def _purge_expired(self):
        now = self.clock()
        for key in list(self.data.keys()):
            if now >= self.data[key][1]:
                del self.data[key]

    def get(self, key):
        self._purge_expired()
        if key not in self.data:
            return None
        value, expires_at = self.data.pop(key)
        self.data[key] = (value, expires_at)
        return value

    def set(self, key, value):
        if self.capacity <= 0:
            return
        self._purge_expired()
        if key in self.data:
            del self.data[key]
        self.data[key] = (value, self.clock() + self.ttl_seconds)
        while len(self.data) > self.capacity:
            self.data.popitem(last=False)

    def delete(self, key):
        self.data.pop(key, None)


def import_cache(workdir):
    sys.path.insert(0, str(workdir))
    try:
        sys.modules.pop("cache", None)
        return importlib.import_module("cache")
    finally:
        sys.path.pop(0)


def run_check(results, name, weight, fn):
    try:
        fn()
        results[name] = {"passed": True, "weight": weight}
        return weight
    except Exception as exc:
        results[name] = {
            "passed": False,
            "weight": weight,
            "error": f"{type(exc).__name__}: {exc}",
        }
        return 0.0


def assert_equal(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: got {actual!r}, expected {expected!r}")


def main():
    workdir = Path(sys.argv[1])
    results = {}
    score = 0.0

    try:
        cache_mod = import_cache(workdir)
    except Exception as exc:
        print(json.dumps({
            "score": 0.0,
            "details": {"import": {"passed": False, "error": repr(exc)}},
            "summary": f"import failed: {exc}",
        }))
        return

    def check_api():
        if not hasattr(cache_mod, "TTLCache"):
            raise AssertionError("TTLCache class missing")
        sig = inspect.signature(cache_mod.TTLCache.__init__)
        params = list(sig.parameters.keys())[1:]
        if params != ["capacity", "ttl_seconds", "clock"]:
            raise AssertionError(f"bad __init__ signature: {sig}")
        if sig.parameters["clock"].default is inspect.Signature.empty:
            raise AssertionError(f"clock must be optional: {sig}")

        expected_methods = {
            "get": ["key"],
            "set": ["key", "value"],
            "delete": ["key"],
        }
        for method, expected_params in expected_methods.items():
            if not hasattr(cache_mod.TTLCache, method):
                raise AssertionError(f"{method} missing")
            method_sig = inspect.signature(getattr(cache_mod.TTLCache, method))
            actual_params = list(method_sig.parameters.keys())[1:]
            if actual_params != expected_params:
                raise AssertionError(f"bad {method} signature: {method_sig}")

    def check_visible_basic():
        proc = subprocess.run(
            [sys.executable, "test_basic.py"],
            cwd=workdir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        if proc.returncode != 0:
            raise AssertionError((proc.stdout + proc.stderr).strip()[-1000:])

    def check_ttl_boundary_and_fake_clock():
        clock = FakeClock()
        cache = cache_mod.TTLCache(2, 10, clock=clock.now)
        cache.set("a", "value")
        clock.advance(9.999)
        assert_equal(cache.get("a"), "value", "key should be valid before boundary")
        cache.set("b", "other")
        clock.advance(0.001)
        assert_equal(cache.get("a"), None, "key should expire at exact boundary")

    def check_lru_get_refresh():
        clock = FakeClock()
        cache = cache_mod.TTLCache(2, 100, clock=clock.now)
        cache.set("a", 1)
        cache.set("b", 2)
        assert_equal(cache.get("a"), 1, "a should be readable")
        cache.set("c", 3)
        assert_equal(cache.get("a"), 1, "get(a) should make a most recently used")
        assert_equal(cache.get("b"), None, "b should be evicted as least recently used")
        assert_equal(cache.get("c"), 3, "c should remain")

    def check_overwrite_refreshes_lru_and_ttl():
        clock = FakeClock()
        cache = cache_mod.TTLCache(2, 5, clock=clock.now)
        cache.set("a", 1)
        cache.set("b", 2)
        clock.advance(4)
        cache.set("a", 10)
        clock.advance(2)
        assert_equal(cache.get("a"), 10, "overwrite should reset TTL")
        cache.set("c", 3)
        assert_equal(cache.get("a"), 10, "overwrite should refresh LRU order")
        assert_equal(cache.get("b"), None, "b should be evicted after refreshed a")

    def check_expired_keys_do_not_consume_capacity():
        clock = FakeClock()
        cache = cache_mod.TTLCache(2, 5, clock=clock.now)
        cache.set("a", 1)
        clock.advance(4)
        cache.set("b", 2)
        assert_equal(cache.get("a"), 1, "a should be valid and become MRU")
        clock.advance(2)
        cache.set("c", 3)
        assert_equal(cache.get("a"), None, "a should be expired")
        assert_equal(cache.get("b"), 2, "valid b should not be evicted by expired a")
        assert_equal(cache.get("c"), 3, "c should remain")

    def check_capacity_zero_and_delete():
        clock = FakeClock()
        cache = cache_mod.TTLCache(0, 10, clock=clock.now)
        cache.set("a", 1)
        assert_equal(cache.get("a"), None, "capacity 0 should store nothing")
        cache.delete("missing")
        cache = cache_mod.TTLCache(1, 10, clock=clock.now)
        cache.set("a", 1)
        cache.delete("a")
        assert_equal(cache.get("a"), None, "delete should remove existing key")

    def check_randomized_against_reference():
        rng = random.Random(12345)
        clock = FakeClock()
        ref_clock = FakeClock()
        cache = cache_mod.TTLCache(4, 7, clock=clock.now)
        ref = ReferenceTTLCache(4, 7, ref_clock.now)
        keys = ["a", "b", "c", "d", "e", "f"]

        for step in range(160):
            op = rng.choice(["set", "get", "delete", "advance"])
            key = rng.choice(keys)
            if op == "set":
                value = rng.randint(1, 1000)
                cache.set(key, value)
                ref.set(key, value)
            elif op == "get":
                assert_equal(cache.get(key), ref.get(key), f"random get mismatch at step {step}")
            elif op == "delete":
                cache.delete(key)
                ref.delete(key)
            else:
                delta = rng.choice([0, 1, 2, 6, 7])
                clock.advance(delta)
                ref_clock.advance(delta)

            if step % 10 == 0:
                for probe in keys:
                    assert_equal(
                        cache.get(probe),
                        ref.get(probe),
                        f"random snapshot mismatch for {probe} at step {step}",
                    )

    checks = [
        ("api", 0.10, check_api),
        ("visible_basic", 0.10, check_visible_basic),
        ("ttl_boundary_and_fake_clock", 0.15, check_ttl_boundary_and_fake_clock),
        ("lru_get_refresh", 0.15, check_lru_get_refresh),
        ("overwrite_refreshes_lru_and_ttl", 0.15, check_overwrite_refreshes_lru_and_ttl),
        ("expired_keys_do_not_consume_capacity", 0.15, check_expired_keys_do_not_consume_capacity),
        ("capacity_zero_and_delete", 0.10, check_capacity_zero_and_delete),
        ("randomized_against_reference", 0.10, check_randomized_against_reference),
    ]

    for name, weight, fn in checks:
        score += run_check(results, name, weight, fn)

    passed = [name for name, item in results.items() if item["passed"]]
    failed = [name for name, item in results.items() if not item["passed"]]
    summary = f"passed {len(passed)}/{len(results)} checks"
    if failed:
        summary += f"; failed: {', '.join(failed)}"

    print(json.dumps({
        "score": round(score, 3),
        "details": results,
        "summary": summary,
    }))


if __name__ == "__main__":
    main()

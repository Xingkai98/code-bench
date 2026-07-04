#!/usr/bin/env python3
"""Visible smoke tests for TTLCache."""
import sys

from cache import TTLCache


class FakeClock:
    def __init__(self):
        self.now_value = 0.0

    def now(self):
        return self.now_value

    def advance(self, seconds):
        self.now_value += seconds


PASSED = 0
FAILED = 0


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  OK {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name} {detail}")


clock = FakeClock()
cache = TTLCache(capacity=2, ttl_seconds=5, clock=clock.now)

print("Basic cache behavior:")
cache.set("a", 1)
check("get existing key", cache.get("a") == 1)
check("missing key returns None", cache.get("missing") is None)

cache.set("b", 2)
cache.set("c", 3)
check("capacity eviction removes oldest", cache.get("a") is None)
check("newer key remains", cache.get("b") == 2)
check("newest key remains", cache.get("c") == 3)

cache.delete("b")
check("delete removes key", cache.get("b") is None)
cache.delete("does-not-exist")
check("delete missing key is harmless", True)

clock.advance(6)
check("expired key returns None", cache.get("c") is None)

print()
print(f"{PASSED} passed, {FAILED} failed")
sys.exit(0 if FAILED == 0 else 1)

#!/usr/bin/env python3
"""Eval: order-race — concurrent order processing correctness.

Tests against both the REFERENCE buggy code (to verify eval detects bugs)
and the model's FIXED code in the workspace.

Runs multiple stress-test rounds. Each round spawns concurrent producers
and consumers and checks invariants.
"""

import json
import subprocess
import sys
import threading
import time
import random
from collections import Counter
from pathlib import Path


# ---------------------------------------------------------------------------
# Atomic helpers for stress test tracking
# ---------------------------------------------------------------------------

class AtomicCounter:
    """Thread-safe counter."""
    def __init__(self):
        self._val = 0
        self._lock = threading.Lock()

    def get(self):
        with self._lock:
            return self._val

    def inc(self):
        with self._lock:
            self._val += 1


class AtomicList:
    """Thread-safe list append."""
    def __init__(self):
        self._items = []
        self._lock = threading.Lock()

    def append(self, item):
        with self._lock:
            self._items.append(item)

    def get_all(self):
        with self._lock:
            return list(self._items)


# ---------------------------------------------------------------------------
# Test runners
# ---------------------------------------------------------------------------

def import_modules(workdir):
    """Import inventory and orders from the given workdir."""
    sys.path.insert(0, str(workdir))

    # Force reimport
    for mod_name in ("inventory", "orders"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    import inventory
    import orders

    sys.path.pop(0)
    return inventory, orders


def run_stress_test(workdir, n_producers, n_orders, capacity_per_item, timeout=30):
    """Core stress test. Returns (passed, errors_list)."""
    inventory_mod, orders_mod = import_modules(workdir)

    ITEMS = ["item-a", "item-b", "item-c"]

    inv = inventory_mod.Inventory({item: capacity_per_item for item in ITEMS})
    proc = orders_mod.OrderProcessor(inv)

    processed_count = AtomicCounter()
    duplicate_count = AtomicCounter()
    fail_count = AtomicCounter()
    negative_stock_seen = AtomicCounter()
    errors = AtomicList()
    done = AtomicList()  # track which order_ids are done

    def producer():
        for i in range(n_orders):
            order_id = f"{threading.current_thread().name}-{i}"
            qty = random.randint(1, 3)
            item = random.choice(ITEMS)
            items = {item: qty}

            try:
                ok = proc.process(order_id, items)
                if ok:
                    processed_count.inc()
                    done.append((order_id, item, qty))
                else:
                    duplicate_count.inc()
            except Exception as e:
                fail_count.inc()
                errors.append(f"EXCEPTION: {e}")

    # Launch threads
    threads = []
    for i in range(n_producers):
        t = threading.Thread(target=producer, name=f"worker-{i}")
        threads.append(t)

    for t in threads:
        t.start()

    for t in threads:
        t.join(timeout=timeout)
        if t.is_alive():
            errors.append("DEADLOCK: a thread did not finish within timeout")
            return False, errors.get_all()

    # --- Verify invariants ---

    # 1. No negative stock
    for item in ITEMS:
        s = inv.get_stock(item)
        if s < 0:
            negative_stock_seen.inc()
            errors.append(f"NEGATIVE STOCK: {item} = {s}")
        if s > capacity_per_item:
            errors.append(f"STOCK OVERFLOW: {item} = {s} (max {capacity_per_item})")

    # 2. Stock conservation: reserved + remaining should add up
    total_processed = processed_count.get()
    total_remaining = sum(inv.get_stock(item) for item in ITEMS)
    expected_max = capacity_per_item * len(ITEMS)

    # Reserved items = processed successfully
    total_reserved = total_processed  # each successful order reserves 1 item
    if total_remaining + total_reserved > expected_max:
        errors.append(
            f"INVARIANT VIOLATION: remaining({total_remaining}) + "
            f"reserved({total_reserved}) > max_possible({expected_max})"
        )

    # 3. Check for duplicate processing of same order_id
    done_orders = done.get_all()
    order_ids = [d[0] for d in done_orders]
    id_counts = Counter(order_ids)
    for oid, count in id_counts.items():
        if count > 1:
            errors.append(f"DUPLICATE PROCESSING: {oid} was processed {count} times")

    # 4. Duplicate detection works (some should be duplicates if
    #    orders exceed capacity)
    #    This isn't a hard pass/fail, just informational

    passed = (
        negative_stock_seen.get() == 0
        and len([e for e in errors.get_all() if "NEGATIVE" in e or "DUPLICATE" in e or "INVARIANT" in e or "DEADLOCK" in e]) == 0
    )

    return passed, errors.get_all()


def run_contention_test(workdir, n_threads, rounds, capacity, timeout=30):
    """High-contention test: small capacity, many threads trading stock."""
    inventory_mod, orders_mod = import_modules(workdir)

    inv = inventory_mod.Inventory({"item-a": capacity})
    proc = orders_mod.OrderProcessor(inv)
    barrier = threading.Barrier(n_threads)
    errors = AtomicList()
    deadlock = AtomicCounter()
    done_counter = AtomicCounter()

    def worker(worker_id):
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            return

        for r in range(rounds):
            try:
                # Each worker tries to reserve 1, then immediately rollback
                ok = proc.process(f"w{worker_id}-r{r}", {"item-a": 1})
                if ok:
                    # simulate using the item, then rollback
                    inv.rollback("item-a", 1)
                    done_counter.inc()
            except Exception as e:
                errors.append(f"WORKER ERROR: w{worker_id}: {e}")

    threads = []
    for i in range(n_threads):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)

    for t in threads:
        t.start()

    for t in threads:
        t.join(timeout=timeout)
        if t.is_alive():
            deadlock.inc()

    failed = False
    error_msgs = errors.get_all()

    if deadlock.get() > 0:
        error_msgs.append(f"DEADLOCK: {deadlock.get()} threads stuck")
        failed = True

    # Check final stock sanity
    final_stock = inv.get_stock("item-a")
    if final_stock < 0:
        error_msgs.append(f"NEGATIVE STOCK after contention: {final_stock}")
        failed = True
    if final_stock > capacity:
        error_msgs.append(f"STOCK OVERFLOW: {final_stock} > {capacity}")
        failed = True

    return (not failed), error_msgs


# ---------------------------------------------------------------------------
# Main eval
# ---------------------------------------------------------------------------

def verify_api(inventory_mod, orders_mod, workdir):
    """Verify the required API surface exists before running tests."""
    errors = []

    # Check files exist in workdir (prevent fallback to original buggy code)
    inv_file = workdir / "inventory.py"
    ord_file = workdir / "orders.py"
    if not inv_file.exists():
        errors.append("MISSING: inventory.py not found in workspace")
    if not ord_file.exists():
        errors.append("MISSING: orders.py not found in workspace")
    if errors:
        return errors

    # Verify Inventory API
    if not hasattr(inventory_mod, "Inventory"):
        errors.append("MISSING: Inventory class not found in inventory.py")
    else:
        inv = inventory_mod.Inventory
        import inspect
        sig = inspect.signature(inv.__init__)
        params = list(sig.parameters.keys())[1:]  # skip 'self'
        if "initial_stock" not in params:
            errors.append(
                f"API CHANGE: Inventory.__init__() signature is {sig} — "
                f"must accept 'initial_stock' parameter"
            )

    # Verify OrderProcessor API
    if not hasattr(orders_mod, "OrderProcessor"):
        errors.append("MISSING: OrderProcessor class not found in orders.py")
    else:
        proc = orders_mod.OrderProcessor
        import inspect
        sig = inspect.signature(proc.process)
        params = list(sig.parameters.keys())[1:]  # skip 'self'
        if params != ["order_id", "items"]:
            errors.append(
                f"API CHANGE: OrderProcessor.process() signature is {sig} — "
                f"must be process(order_id, items)"
            )

    return errors


def main():
    workdir = Path(sys.argv[1])
    results = {}

    # --- Gate: API verification ---
    inventory_mod, orders_mod = import_modules(workdir)
    api_errors = verify_api(inventory_mod, orders_mod, workdir)
    if api_errors:
        print(json.dumps({
            "score": 0.0,
            "details": {"api_errors": api_errors},
            "summary": f"API verification failed: {'; '.join(api_errors)}",
        }))
        return

    # --- Test 1: Basic correctness (single thread) ---
    _, orders_mod = import_modules(workdir)
    inventory_mod, _ = import_modules(workdir)

    inv = inventory_mod.Inventory({"item-x": 3})
    proc = orders_mod.OrderProcessor(inv)
    assert proc.process("order-1", {"item-x": 2}), "basic reserve should succeed"
    assert inv.get_stock("item-x") == 1, f"stock should be 1, got {inv.get_stock('item-x')}"
    assert not proc.process("order-1", {"item-x": 1}), "duplicate should return False"
    assert inv.get_stock("item-x") == 1, "duplicate should not change stock"
    results["basic"] = {"passed": True, "note": "single-thread basics OK"}

    # --- Test 2: Stress test (the real bug detector) ---
    stress_rounds = 10
    stress_passes = 0
    all_errors = []

    for r in range(stress_rounds):
        passed, errors = run_stress_test(
            workdir,
            n_producers=8,
            n_orders=200,
            capacity_per_item=50,
            timeout=30,
        )
        if passed:
            stress_passes += 1
        else:
            all_errors.extend(errors)

    # Deduplicate errors for readability
    unique_errors = list(dict.fromkeys(all_errors))[:20]

    results["stress"] = {
        "rounds": stress_rounds,
        "passed": stress_passes,
        "failures": stress_rounds - stress_passes,
        "pass_rate": stress_passes / stress_rounds,
        "sample_errors": unique_errors,
    }

    # --- Test 3: High-contention test ---
    cont_passed, cont_errors = run_contention_test(
        workdir, n_threads=8, rounds=50, capacity=5, timeout=30
    )
    results["contention"] = {
        "passed": cont_passed,
        "errors": cont_errors[:10],
    }

    # --- Score ---
    stress_rate = stress_passes / stress_rounds
    contention_ok = 1.0 if cont_passed else 0.0

    # Buggy code will get low stress_rate (probably 0/10)
    score = (
        0.20 * 1.0 +           # basic (always passes if code is functional)
        0.50 * stress_rate +   # stress (the main differentiator)
        0.30 * contention_ok   # contention edge cases
    )

    # Build summary
    if score >= 0.95:
        summary = "All tests passed — no concurrency bugs detected"
    elif score >= 0.7:
        summary = (
            f"Mostly correct: {stress_passes}/{stress_rounds} stress rounds passed"
        )
    elif score >= 0.3:
        summary = (
            f"Intermittent failures: {stress_passes}/{stress_rounds} stress rounds passed. "
            f"Sample errors: {unique_errors[:3] if unique_errors else ['none']}"
        )
    else:
        summary = (
            f"Severe concurrency bugs: {stress_passes}/{stress_rounds} stress rounds passed. "
            f"Sample errors: {unique_errors[:3] if unique_errors else ['none']}"
        )

    print(json.dumps({
        "score": score,
        "details": results,
        "summary": summary,
    }))


if __name__ == "__main__":
    main()

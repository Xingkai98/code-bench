#!/usr/bin/env python3
"""
Hidden eval for double-charge — multi-file concurrency bug localization.

Scoring dimensions (7 checks, 100% total):
  api               ( 5%) — import + signature verification
  smoke             ( 5%) — test_basic.py still passes
  inventory_atomic  (20%) — concurrent reserves do not oversell
  idempotency       (15%) — concurrent charge same order_id = 1 payment
  coupon_quota      (15%) — concurrent coupon use does not exceed max_uses
  rollback          (20%) — partial failure triggers inventory release; cancel refunds
  state_machine     (20%) — illegal transitions rejected, cancel+order race, edge cases

Usage: python3 eval.py <workspace_path>
Output: JSON with score, details, summary.
"""
from __future__ import annotations

import importlib
import inspect
import json
import os
import random
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path


# ---------------------------------------------------------------------------
# Thread-safe helpers
# ---------------------------------------------------------------------------

class AtomicCounter:
    def __init__(self):
        self._val = 0
        self._lock = threading.Lock()

    def inc(self):
        with self._lock:
            self._val += 1

    def get(self) -> int:
        with self._lock:
            return self._val


class AtomicList:
    def __init__(self):
        self._items: list = []
        self._lock = threading.Lock()

    def append(self, item):
        with self._lock:
            self._items.append(item)

    def get_all(self) -> list:
        with self._lock:
            return list(self._items)


# ---------------------------------------------------------------------------
# Module import
# ---------------------------------------------------------------------------

_MODULE_NAMES = ("db", "models", "exceptions", "inventory",
                 "payment", "orders", "pricing")


def import_modules(workdir: Path):
    """Import all service modules from the workspace."""
    sys.path.insert(0, str(workdir))
    for mod_name in _MODULE_NAMES:
        sys.modules.pop(mod_name, None)

    import db
    import exceptions
    import inventory
    import models
    import orders
    import payment
    import pricing

    sys.path.pop(0)
    return db, exceptions, inventory, models, orders, payment, pricing


# ---------------------------------------------------------------------------
# Scoring helper
# ---------------------------------------------------------------------------

def run_check(checks_dict: dict, name: str, weight: float, fn) -> float:
    try:
        fn()
        checks_dict[name] = {"passed": True, "weight": weight}
        return weight
    except Exception as exc:
        checks_dict[name] = {
            "passed": False,
            "weight": weight,
            "error": f"{type(exc).__name__}: {exc}",
        }
        return 0.0


# ---------------------------------------------------------------------------
# 1. API check (5%)
# ---------------------------------------------------------------------------

def check_api(workdir: Path):
    results = {}

    def verify():
        _, _, inventory_mod, models_mod, orders_mod, payment_mod, pricing_mod = \
            import_modules(workdir)

        # Inventory
        assert hasattr(inventory_mod, "Inventory"), "Inventory class missing"
        sig = inspect.signature(inventory_mod.Inventory.__init__)
        assert "db" in [p for p in list(sig.parameters.keys())[1:]], "bad __init__ sig"

        for method, params in [
            ("reserve", ["product_id", "quantity"]),
            ("release", ["product_id", "quantity"]),
            ("get_stock", ["product_id"]),
            ("get_product", ["product_id"]),
        ]:
            assert hasattr(inventory_mod.Inventory, method), f"Inventory.{method} missing"
            msig = inspect.signature(getattr(inventory_mod.Inventory, method))
            actual = list(msig.parameters.keys())[1:]
            assert actual == params, f"Inventory.{method} sig: {msig}"

        # PaymentService
        assert hasattr(payment_mod, "PaymentService"), "PaymentService missing"
        for method, params in [
            ("charge", ["order_id", "amount", "card_token"]),
            ("refund", ["payment_id"]),
            ("get_payment", ["payment_id"]),
            ("get_payments_for_order", ["order_id"]),
        ]:
            assert hasattr(payment_mod.PaymentService, method), f"PaymentService.{method} missing"
            msig = inspect.signature(getattr(payment_mod.PaymentService, method))
            actual = list(msig.parameters.keys())[1:]
            assert actual == params, f"PaymentService.{method} sig: {msig}"

        # OrderService
        assert hasattr(orders_mod, "OrderService"), "OrderService missing"
        osig = inspect.signature(orders_mod.OrderService.__init__)
        oparams = list(osig.parameters.keys())[1:]
        assert oparams == ["db", "inventory", "payment", "coupons"], \
            f"OrderService.__init__ sig: {osig}"

        for method, params in [
            ("place_order", ["order_id", "items", "card_token", "coupon_code"]),
            ("get_order", ["order_id"]),
            ("cancel_order", ["order_id"]),
        ]:
            assert hasattr(orders_mod.OrderService, method), f"OrderService.{method} missing"
            msig = inspect.signature(getattr(orders_mod.OrderService, method))
            actual = list(msig.parameters.keys())[1:]
            assert actual == params, f"OrderService.{method} sig: {msig}"

        # CouponManager
        assert hasattr(pricing_mod, "CouponManager"), "CouponManager missing"
        for method, params in [
            ("create_coupon", ["code", "discount_rate", "max_uses"]),
            ("apply_coupon", ["code", "order_amount"]),
            ("get_remaining_uses", ["code"]),
        ]:
            assert hasattr(pricing_mod.CouponManager, method), f"CouponManager.{method} missing"
            msig = inspect.signature(getattr(pricing_mod.CouponManager, method))
            actual = list(msig.parameters.keys())[1:]
            assert actual == params, f"CouponManager.{method} sig: {msig}"

    score = run_check(results, "api", 0.05, verify)
    return score, results


# ---------------------------------------------------------------------------
# 2. Smoke test (5%)
# ---------------------------------------------------------------------------

def check_smoke(workdir: Path):
    results = {}

    def verify():
        proc = subprocess.run(
            [sys.executable, str(workdir / "test_basic.py")],
            cwd=str(workdir),
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + "\n" + proc.stderr).strip()[-800:]
            raise AssertionError(f"test_basic.py failed (exit {proc.returncode}):\n{tail}")

    score = run_check(results, "smoke", 0.05, verify)
    return score, results


# ---------------------------------------------------------------------------
# Helpers for concurrency tests
# ---------------------------------------------------------------------------

def _make_db(db_mod, workdir: Path, name: str):
    """Create a fresh Database pointed at a temp file."""
    db_file = workdir / name
    if db_file.exists():
        db_file.unlink()
    return db_mod.Database(str(db_file)), db_file


def _make_services(mods, db, with_products=True):
    """Create Inventory, PaymentService, CouponManager, OrderService.

    Args:
        mods: Tuple of (db_mod, exc_mod, inv_mod, models_mod,
                         ord_mod, pay_mod, pricing_mod).
        db: Database instance.
        with_products: Seed some test products if True.
    """
    _, _, inv_mod, models_mod, ord_mod, pay_mod, pricing_mod = mods

    inv = inv_mod.Inventory(db)
    pay = pay_mod.PaymentService(db)
    coupons = pricing_mod.CouponManager(db)
    order_svc = ord_mod.OrderService(db, inv, pay, coupons)

    if with_products:
        products = [
            models_mod.Product("pA", "Item A", 10000, 60),
            models_mod.Product("pB", "Item B", 20000, 60),
            models_mod.Product("pC", "Item C", 30000, 40),
        ]
        inv.load_catalog(products)

    return inv, pay, coupons, order_svc


# ---------------------------------------------------------------------------
# 3. Inventory atomicity (20%)
# ---------------------------------------------------------------------------

def check_inventory_atomic(workdir: Path):
    results = {}
    mods = import_modules(workdir)

    def verify():
        db, db_file = _make_db(mods[0], workdir, "_eval_inv.json")
        inv, _, _, _ = _make_services(mods, db)

        N_THREADS = 12
        ORDERS_PER_THREAD = 30     # 360 total attempts
        CAPACITY = 60               # per product

        errors = AtomicList()
        success = AtomicCounter()

        def worker(wid: int):
            for i in range(ORDERS_PER_THREAD):
                prod = random.choice(["pA", "pB", "pC"])
                qty = random.randint(1, 3)
                try:
                    if inv.reserve(prod, qty):
                        success.inc()
                except Exception as e:
                    errors.append(f"w{wid}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            if t.is_alive():
                errors.append("DEADLOCK")

        # Verify invariants
        total_remaining = sum(inv.get_stock(p) for p in ["pA", "pB", "pC"])
        total_sold = success.get()
        total_initial = CAPACITY * 3  # 180

        for pid in ["pA", "pB", "pC"]:
            s = inv.get_stock(pid)
            if s < 0:
                errors.append(f"NEGATIVE STOCK: {pid} = {s}")
            if s > CAPACITY:
                errors.append(f"STOCK OVERFLOW: {pid} = {s}")

        if total_remaining + total_sold > total_initial:
            errors.append(
                f"INVARIANT: remaining({total_remaining}) + sold({total_sold}) "
                f"> initial({total_initial})"
            )

        critical = [e for e in errors.get_all()
                     if "NEGATIVE" in e or "INVARIANT" in e or "DEADLOCK" in e]
        if critical:
            raise AssertionError("; ".join(critical[:5]))

        if db_file.exists():
            db_file.unlink()

    score = run_check(results, "inventory_atomic", 0.20, verify)
    return score, results


# ---------------------------------------------------------------------------
# 4. Payment idempotency (15%)
# ---------------------------------------------------------------------------

def check_idempotency(workdir: Path):
    results = {}
    mods = import_modules(workdir)

    def verify():
        db, db_file = _make_db(mods[0], workdir, "_eval_idem.json")
        pay_mod = mods[5]
        pay = pay_mod.PaymentService(db)

        N_THREADS = 16
        barrier = threading.Barrier(N_THREADS, timeout=10)
        errors = AtomicList()
        results_list = AtomicList()

        def worker():
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                return
            try:
                r = pay.charge("order-same", 5000, "tok_4242")
                results_list.append(r)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        if errors.get_all():
            raise AssertionError(f"Worker errors: {errors.get_all()[:3]}")

        # Verify exactly one COMPLETED payment for this order
        payments = pay.get_payments_for_order("order-same")
        completed = [p for p in payments
                     if str(p.status) == "PaymentStatus.COMPLETED"]
        total = [p for p in payments]

        if len(completed) != 1:
            raise AssertionError(
                f"Expected exactly 1 COMPLETED payment for order-same, "
                f"got {len(completed)} completed out of {len(total)} total. "
                f"Payment IDs: {[p.payment_id for p in completed]}"
            )

        # Total charged should equal order amount (not more)
        total_charged = sum(p.amount for p in completed)
        if total_charged > 5000:
            raise AssertionError(
                f"Total charged ({total_charged}) exceeds order amount (5000)"
            )

        if db_file.exists():
            db_file.unlink()

    score = run_check(results, "idempotency", 0.15, verify)
    return score, results


# ---------------------------------------------------------------------------
# 5. Coupon quota (15%)
# ---------------------------------------------------------------------------

def check_coupon_quota(workdir: Path):
    results = {}
    mods = import_modules(workdir)
    pricing_mod = mods[6]

    def verify():
        db, db_file = _make_db(mods[0], workdir, "_eval_cpn.json")
        coupons = pricing_mod.CouponManager(db)

        MAX_USES = 20
        N_THREADS = 15
        CALLS_PER_THREAD = 3  # 45 total attempts, only 20 should succeed

        coupons.create_coupon("LIMITED", 0.3, MAX_USES)

        errors = AtomicList()
        success = AtomicCounter()

        def worker(wid: int):
            for i in range(CALLS_PER_THREAD):
                try:
                    r = coupons.apply_coupon("LIMITED", 10000)
                    if r["ok"]:
                        success.inc()
                except Exception as e:
                    errors.append(f"w{wid}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        used = success.get()
        if used > MAX_USES:
            raise AssertionError(
                f"Coupon overused: {used} successful uses, max is {MAX_USES}"
            )

        remaining = coupons.get_remaining_uses("LIMITED")
        if remaining != MAX_USES - used:
            raise AssertionError(
                f"Remaining uses mismatch: {remaining} remaining, "
                f"expected {MAX_USES - used}"
            )

        # At least some should have succeeded
        if used == 0:
            raise AssertionError("No coupon uses succeeded — check implementation")

        if db_file.exists():
            db_file.unlink()

    score = run_check(results, "coupon_quota", 0.15, verify)
    return score, results


# ---------------------------------------------------------------------------
# 6. Rollback (20%)
# ---------------------------------------------------------------------------

def check_rollback(workdir: Path):
    results = {}
    mods = import_modules(workdir)
    models_mod = mods[3]

    def verify():
        db, db_file = _make_db(mods[0], workdir, "_eval_rollback.json")
        inv, pay, coupons, order_svc = _make_services(mods, db)

        # Test A: Multi-item order where one item is sold out.
        # pA has 60, pB has 60 — place an order with pA:2, pB:100
        # pB should fail. After fix: pA should be released (stock restored).
        stock_pA_before = inv.get_stock("pA")

        order = order_svc.place_order("ord-rb-1", {"pA": 2, "pB": 100})

        stock_pA_after = inv.get_stock("pA")

        # On a cancelled order, ALL reserved items must be released.
        # Stock should be back to what it was before the order.
        if order.status == models_mod.OrderStatus.CANCELLED:
            if stock_pA_after != stock_pA_before:
                raise AssertionError(
                    f"ROLLBACK FAILED: cancelled order did not release pA. "
                    f"before={stock_pA_before}, after={stock_pA_after}"
                )
        else:
            # Order somehow succeeded despite insufficient stock
            raise AssertionError(
                f"Expected CANCELLED order, got {order.status}"
            )

        # No payment should exist for this failed order
        payments = pay.get_payments_for_order("ord-rb-1")
        completed = [p for p in payments
                     if str(p.status) == "PaymentStatus.COMPLETED"]
        if completed:
            raise AssertionError(
                f"ROLLBACK FAILED: {len(completed)} completed payments for "
                f"a failed order. Should be 0."
            )

        # Test B: Place a valid order, cancel it — verify refund + release.
        order2 = order_svc.place_order("ord-rb-2", {"pA": 1, "pC": 2})
        if order2.status != models_mod.OrderStatus.PAID:
            raise AssertionError(f"Expected PAID, got {order2.status}")

        stock_before_cancel = inv.get_stock("pA")
        cancelled = order_svc.cancel_order("ord-rb-2")
        if cancelled.status != models_mod.OrderStatus.CANCELLED:
            raise AssertionError(f"Expected CANCELLED, got {cancelled.status}")

        # Stock should be restored
        stock_after_cancel = inv.get_stock("pA")
        if stock_after_cancel != stock_before_cancel + 1:
            raise AssertionError(
                f"Cancel did not restore stock: before={stock_before_cancel}, "
                f"after={stock_after_cancel}, expected {stock_before_cancel + 1}"
            )

        # Payment should be refunded
        if order2.payment_id:
            payment = pay.get_payment(order2.payment_id)
            if payment is None:
                raise AssertionError(f"Payment {order2.payment_id} not found")
            if str(payment.status) != "PaymentStatus.REFUNDED":
                raise AssertionError(
                    f"Payment should be REFUNDED after cancel, got {payment.status}"
                )

        # Test C: Multi-item order where a later item fails.
        # pC has 40 stock. Reserve pA:2 + pB:2 + pC:100.
        # pC should fail. If rollback works: pA and pB must be released.
        stock_pA2 = inv.get_stock("pA")
        stock_pB2 = inv.get_stock("pB")

        order3 = order_svc.place_order("ord-rb-3", {"pA": 2, "pB": 2, "pC": 100})

        if order3.status == models_mod.OrderStatus.CANCELLED:
            # Cancelled order must release all reserved items
            if inv.get_stock("pA") != stock_pA2:
                raise AssertionError(
                    f"Multi-item rollback: pA not released. "
                    f"before={stock_pA2}, after={inv.get_stock('pA')}"
                )
            if inv.get_stock("pB") != stock_pB2:
                raise AssertionError(
                    f"Multi-item rollback: pB not released. "
                    f"before={stock_pB2}, after={inv.get_stock('pB')}"
                )
        else:
            raise AssertionError(
                f"Expected CANCELLED for multi-item order with insufficient stock, "
                f"got {order3.status}"
            )

        if db_file.exists():
            db_file.unlink()

    score = run_check(results, "rollback", 0.20, verify)
    return score, results


# ---------------------------------------------------------------------------
# 7. State machine + edge cases (20%)
# ---------------------------------------------------------------------------

def check_state_machine(workdir: Path):
    results = {}
    mods = import_modules(workdir)
    exc_mod = mods[1]
    models_mod = mods[3]

    def verify():
        db, db_file = _make_db(mods[0], workdir, "_eval_state.json")
        inv, pay, coupons, order_svc = _make_services(mods, db)
        InvalidOrderStateError = exc_mod.InvalidOrderStateError

        # --- Test A: Illegal state transitions ---

        # Cancel a REFUNDED order should be rejected
        order = order_svc.place_order("st-a", {"pA": 1})
        order_svc.cancel_order("st-a")   # now CANCELLED
        try:
            order_svc.cancel_order("st-a")
            raise AssertionError("Double cancel should raise InvalidOrderStateError")
        except InvalidOrderStateError:
            pass  # expected

        # --- Test B: Concurrent cancel + place for same order_id ---
        # After cancel, placing with same order_id should return existing
        # (cancelled) order, not re-process.
        order2 = order_svc.place_order("st-b", {"pB": 2})
        order_svc.cancel_order("st-b")
        stock_after_cancel = inv.get_stock("pB")

        # Try to place again with same order_id
        order3 = order_svc.place_order("st-b", {"pB": 1})
        # Should return the existing CANCELLED order, NOT re-process
        if order3.status != models_mod.OrderStatus.CANCELLED:
            raise AssertionError(
                f"Re-using cancelled order_id should return CANCELLED, "
                f"got {order3.status}"
            )
        # Stock should NOT have changed
        if inv.get_stock("pB") != stock_after_cancel:
            raise AssertionError(
                f"Re-using order_id should not modify stock. "
                f"before={stock_after_cancel}, after={inv.get_stock('pB')}"
            )

        # --- Test C: Refund a paid order, then try to cancel ---
        order4 = order_svc.place_order("st-c", {"pC": 1})
        # Manually refund via payment service
        pay.refund(order4.payment_id)
        # Try to cancel a refunded order — should fail
        try:
            order_svc.cancel_order("st-c")
            raise AssertionError("Cancel after refund should fail")
        except InvalidOrderStateError:
            pass

        # --- Test D: Concurrent place_order calls for same ID ---
        # Two threads simultaneously try to place the same order_id.
        # Only one should succeed (PAID), the other should return the existing order.
        N_THREADS = 8
        errors = AtomicList()
        results_list = AtomicList()

        def worker():
            try:
                o = order_svc.place_order("st-d", {"pA": 1})
                results_list.append((str(o.status), o.payment_id))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        if errors.get_all():
            raise AssertionError(f"Concurrent place errors: {errors.get_all()[:3]}")

        all_results = results_list.get_all()
        paid_count = sum(1 for s, _ in all_results if s == "OrderStatus.PAID")

        if paid_count != 1:
            raise AssertionError(
                f"Expected exactly 1 PAID result for concurrent place_order, "
                f"got {paid_count}. Results: {all_results}"
            )

        # Stock should reflect exactly 1 order
        if inv.get_stock("pA") != 60 - 1 - 1 - 1:  # st-a:1, st-d:1, st-b used pB
            # Actually let me recalculate: st-a took pA:1, st-d took pA:1
            # st-c took pC:1, st-b took pB:2
            # pA should have 60 - 2 = 58 (st-a was cancelled so restored, st-d:1)
            # Hmm, st-a was cancelled and restored. So pA: 60 - 0(st-a cancelled) - 1(st-d) = 59
            expected_pA = 60 - 1  # st-d is the only active PAID order for pA
            actual_pA = inv.get_stock("pA")
            if actual_pA != expected_pA:
                raise AssertionError(
                    f"Concurrent place stock wrong: pA={actual_pA}, expected={expected_pA}"
                )

        if db_file.exists():
            db_file.unlink()

    score = run_check(results, "state_machine", 0.20, verify)
    return score, results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    workdir = Path(sys.argv[1])

    all_details = {}
    total_score = 0.0

    checks = [
        ("api",               0.05, lambda: check_api(workdir)),
        ("smoke",             0.05, lambda: check_smoke(workdir)),
        ("inventory_atomic",  0.20, lambda: check_inventory_atomic(workdir)),
        ("idempotency",       0.15, lambda: check_idempotency(workdir)),
        ("coupon_quota",      0.15, lambda: check_coupon_quota(workdir)),
        ("rollback",          0.20, lambda: check_rollback(workdir)),
        ("state_machine",     0.20, lambda: check_state_machine(workdir)),
    ]

    for name, weight, fn in checks:
        try:
            score, details = fn()
            total_score += score
            all_details[name] = details
        except Exception as exc:
            all_details[name] = {
                "passed": False,
                "weight": weight,
                "error": f"{type(exc).__name__}: {exc}",
            }

    # Build summary
    passed = []
    failed = []
    for n, d in all_details.items():
        if not isinstance(d, dict):
            failed.append(n)
            continue
        # d is like {"api": {"passed": true, "weight": 0.05}} — unwrap one level
        inner = next(iter(d.values()), {})
        if isinstance(inner, dict) and inner.get("passed"):
            passed.append(n)
        else:
            failed.append(n)

    summary = f"passed {len(passed)}/{len(checks)} checks"
    if failed:
        summary += f"; failed: {', '.join(failed)}"

    # Clean up temp files
    for f in workdir.glob("_eval_*.json"):
        f.unlink(missing_ok=True)

    print(json.dumps({
        "score": round(total_score, 3),
        "details": all_details,
        "summary": summary,
    }))


if __name__ == "__main__":
    main()

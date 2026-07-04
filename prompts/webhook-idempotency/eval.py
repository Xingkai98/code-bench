#!/usr/bin/env python3
"""Hidden eval for webhook-idempotency."""
import importlib
import inspect
import json
import subprocess
import sys
import threading
from pathlib import Path


def import_payments(workdir):
    sys.path.insert(0, str(workdir))
    try:
        sys.modules.pop("payments", None)
        return importlib.import_module("payments")
    finally:
        sys.path.pop(0)


def event(event_id, order_id, event_type, amount=0):
    return {
        "event_id": event_id,
        "order_id": order_id,
        "type": event_type,
        "amount": amount,
    }


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


def assert_true(condition, msg):
    if not condition:
        raise AssertionError(msg)


def main():
    workdir = Path(sys.argv[1])
    results = {}
    score = 0.0

    try:
        payments_mod = import_payments(workdir)
    except Exception as exc:
        print(json.dumps({
            "score": 0.0,
            "details": {"import": {"passed": False, "error": repr(exc)}},
            "summary": f"import failed: {exc}",
        }))
        return

    def check_api():
        if not hasattr(payments_mod, "PaymentProcessor"):
            raise AssertionError("PaymentProcessor class missing")
        sig = inspect.signature(payments_mod.PaymentProcessor.__init__)
        params = list(sig.parameters.keys())[1:]
        if params != ["expected_amounts"]:
            raise AssertionError(f"bad __init__ signature: {sig}")
        if sig.parameters["expected_amounts"].default is inspect.Signature.empty:
            raise AssertionError(f"expected_amounts must be optional: {sig}")

        expected_methods = {
            "handle_event": ["event"],
            "get_order": ["order_id"],
        }
        for method, expected_params in expected_methods.items():
            if not hasattr(payments_mod.PaymentProcessor, method):
                raise AssertionError(f"{method} missing")
            method_sig = inspect.signature(getattr(payments_mod.PaymentProcessor, method))
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

    def check_sequential_idempotency():
        proc = payments_mod.PaymentProcessor({"order": 100})
        first = proc.handle_event(event("evt-capture", "order", "payment_captured", 40))
        second = proc.handle_event(event("evt-capture", "order", "payment_captured", 40))
        order = proc.get_order("order")
        assert_true(first["ok"], "first capture should succeed")
        assert_true(second["duplicate"], "second delivery should be marked duplicate")
        assert_equal(order["captured_amount"], 40, "duplicate capture should not change amount")

    def check_illegal_state_transitions():
        proc = payments_mod.PaymentProcessor({"order": 100})
        proc.handle_event(event("evt-capture", "order", "payment_captured", 100))
        failed = proc.handle_event(event("evt-failed", "order", "payment_failed"))
        order = proc.get_order("order")
        assert_true(not failed["ok"], "failed event after capture should be rejected")
        assert_equal(order["status"], "captured", "failed event must not roll captured back")

        auth = proc.handle_event(event("evt-auth-late", "order", "payment_authorized", 100))
        order = proc.get_order("order")
        assert_true(not auth["ok"], "late authorization after capture should be rejected")
        assert_equal(order["status"], "captured", "late authorization must not roll state back")

    def check_refund_and_amount_rules():
        proc = payments_mod.PaymentProcessor({"order": 100, "other": 50})
        refund = proc.handle_event(event("evt-refund-early", "order", "refund_issued", 10))
        assert_true(not refund["ok"], "refund before capture should be rejected")
        assert_equal(proc.get_order("order")["refunded_amount"], 0, "early refund must not change amount")

        too_much = proc.handle_event(event("evt-capture-too-much", "other", "payment_captured", 60))
        assert_true(not too_much["ok"], "capture above expected amount should be rejected")
        assert_equal(proc.get_order("other")["captured_amount"], 0, "rejected capture must not change amount")

        proc.handle_event(event("evt-capture", "order", "payment_captured", 100))
        partial = proc.handle_event(event("evt-refund-1", "order", "refund_issued", 30))
        assert_true(partial["ok"], "partial refund should succeed")
        assert_equal(proc.get_order("order")["status"], "captured", "partial refund keeps captured status")
        over = proc.handle_event(event("evt-refund-over", "order", "refund_issued", 80))
        assert_true(not over["ok"], "over-refund should be rejected")
        assert_equal(proc.get_order("order")["refunded_amount"], 30, "over-refund must not change amount")
        final = proc.handle_event(event("evt-refund-final", "order", "refund_issued", 70))
        assert_true(final["ok"], "remaining refund should succeed")
        order = proc.get_order("order")
        assert_equal(order["refunded_amount"], 100, "full refund amount")
        assert_equal(order["status"], "refunded", "full refund status")

    def check_out_of_order_capture_then_authorize():
        proc = payments_mod.PaymentProcessor({"order": 100})
        capture = proc.handle_event(event("evt-capture", "order", "payment_captured", 100))
        late_auth = proc.handle_event(event("evt-auth", "order", "payment_authorized", 100))
        order = proc.get_order("order")
        assert_true(capture["ok"], "capture from new should be accepted")
        assert_true(not late_auth["ok"], "late authorization should be rejected")
        assert_equal(order["status"], "captured", "late authorization must not downgrade state")
        assert_equal(order["captured_amount"], 100, "capture amount should remain")

    def check_concurrent_duplicate_event():
        proc = payments_mod.PaymentProcessor({"order": 100})
        barrier = threading.Barrier(24)
        errors = []

        def worker():
            try:
                barrier.wait(timeout=5)
                proc.handle_event(event("evt-same", "order", "payment_captured", 10))
            except Exception as exc:
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker) for _ in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            if thread.is_alive():
                errors.append("thread did not finish")

        assert_equal(errors, [], "concurrent duplicate errors")
        order = proc.get_order("order")
        assert_equal(order["captured_amount"], 10, "same event_id may only capture once")

    def check_concurrent_refund_ceiling():
        proc = payments_mod.PaymentProcessor({"order": 100})
        proc.handle_event(event("evt-capture", "order", "payment_captured", 100))
        barrier = threading.Barrier(20)
        errors = []

        def worker(i):
            try:
                barrier.wait(timeout=5)
                proc.handle_event(event(f"evt-refund-{i}", "order", "refund_issued", 10))
            except Exception as exc:
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            if thread.is_alive():
                errors.append("thread did not finish")

        assert_equal(errors, [], "concurrent refund errors")
        order = proc.get_order("order")
        assert_true(order["refunded_amount"] <= 100, "refunds must not exceed captured amount")
        assert_equal(order["refunded_amount"], 100, "exactly captured amount should be refundable")
        assert_equal(order["status"], "refunded", "full refund should end refunded")

    checks = [
        ("api", 0.10, check_api),
        ("visible_basic", 0.10, check_visible_basic),
        ("sequential_idempotency", 0.15, check_sequential_idempotency),
        ("illegal_state_transitions", 0.15, check_illegal_state_transitions),
        ("refund_and_amount_rules", 0.15, check_refund_and_amount_rules),
        ("out_of_order_capture_then_authorize", 0.10, check_out_of_order_capture_then_authorize),
        ("concurrent_duplicate_event", 0.15, check_concurrent_duplicate_event),
        ("concurrent_refund_ceiling", 0.10, check_concurrent_refund_ceiling),
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

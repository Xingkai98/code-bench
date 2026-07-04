#!/usr/bin/env python3
"""Visible smoke tests for PaymentProcessor."""
import sys

from payments import PaymentProcessor


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


def event(event_id, order_id, event_type, amount=0):
    return {
        "event_id": event_id,
        "order_id": order_id,
        "type": event_type,
        "amount": amount,
    }


processor = PaymentProcessor(expected_amounts={"order-1": 100, "order-2": 50})

print("Basic payment webhook behavior:")
res = processor.handle_event(event("evt-auth-1", "order-1", "payment_authorized", 100))
check("authorize ok", res["ok"] is True)
check("authorize status", processor.get_order("order-1")["status"] == "authorized")

res = processor.handle_event(event("evt-capture-1", "order-1", "payment_captured", 100))
order = processor.get_order("order-1")
check("capture ok", res["ok"] is True)
check("capture status", order["status"] == "captured")
check("capture amount", order["captured_amount"] == 100)

res = processor.handle_event(event("evt-capture-1", "order-1", "payment_captured", 100))
order = processor.get_order("order-1")
check("duplicate marked", res["duplicate"] is True)
check("duplicate did not double capture", order["captured_amount"] == 100)

res = processor.handle_event(event("evt-fail-2", "order-2", "payment_failed"))
check("failed ok", res["ok"] is True)
check("failed status", processor.get_order("order-2")["status"] == "failed")

print()
print(f"{PASSED} passed, {FAILED} failed")
sys.exit(0 if FAILED == 0 else 1)

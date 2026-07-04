#!/usr/bin/env python3
"""
Basic single-threaded correctness tests for the e-commerce system.

These tests verify CRUD operations and basic business logic.
They are intentionally single-threaded and will pass even if concurrency
bugs exist in the code.

Run: python3 test_basic.py
"""
from __future__ import annotations

import os
import sys

from db import Database
from exceptions import (
    InvalidOrderStateError,
    OrderNotFoundError,
    ProductNotFoundError,
)
from inventory import Inventory
from models import OrderStatus, Product
from orders import OrderService
from payment import PaymentService
from pricing import CouponManager

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  OK  {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}  — {detail}")


def setup_fresh(db_name: str = "test_store.json"):
    """Create a fresh in-memory store and services."""
    if os.path.exists(db_name):
        os.remove(db_name)

    db = Database(db_name)
    inv = Inventory(db)
    pay = PaymentService(db)
    coupons = CouponManager(db)
    order_svc = OrderService(db, inv, pay, coupons)

    products = [
        Product("p100", "Mouse",     9900,  10),
        Product("p200", "Keyboard", 29900,   5),
        Product("p300", "Monitor", 299900,   3),
        Product("p400", "Webcam",   19900,   8),
        Product("p500", "Headphone", 59900,   0),
    ]
    inv.load_catalog(products)

    return db, inv, pay, coupons, order_svc


# ---------------------------------------------------------------------------
# Inventory tests
# ---------------------------------------------------------------------------

def test_inventory():
    print("\nInventory:")
    _, inv, _, _, _ = setup_fresh("test_inv.json")

    check("get_stock existing",       inv.get_stock("p100") == 10)
    check("get_stock missing",        inv.get_stock("p999") == 0)

    p = inv.get_product("p100")
    check("get_product exists",       p is not None and p.name == "Mouse")
    check("get_product missing",      inv.get_product("p999") is None)

    ok = inv.reserve("p100", 3)
    check("reserve ok",               ok is True)
    check("stock after reserve",      inv.get_stock("p100") == 7)

    ok = inv.reserve("p200", 100)
    check("reserve insufficient",     ok is False)
    check("stock unchanged",          inv.get_stock("p200") == 5)

    ok = inv.reserve("p200", 5)
    check("reserve exact",            ok is True)
    check("stock zero after exact",   inv.get_stock("p200") == 0)

    ok = inv.reserve("p500", 1)
    check("reserve sold-out",         ok is False)

    inv.release("p100", 2)
    check("release restores stock",   inv.get_stock("p100") == 9)

    check("reserve zero",             inv.reserve("p100", 0) is False)
    check("reserve negative",         inv.reserve("p100", -1) is False)

    inv.release("p999", 5)
    check("release missing noop",     True)

    os.remove("test_inv.json")


# ---------------------------------------------------------------------------
# Payment tests
# ---------------------------------------------------------------------------

def test_payment():
    print("\nPaymentService:")
    _, _, pay, _, _ = setup_fresh("test_pay.json")

    result = pay.charge("order-1", 9900, "tok_4242")
    check("charge ok",                result["ok"] is True)
    pid = result["payment_id"]
    check("has payment_id",           bool(pid))

    payment = pay.get_payment(pid)
    check("get_payment returns",      payment is not None)
    check("payment amount",           payment.amount == 9900)

    check("get_payment missing",      pay.get_payment("nonexistent") is None)

    ref = pay.refund(pid)
    check("refund ok",                ref["ok"] is True)
    p2 = pay.get_payment(pid)
    check("refund status",            str(p2.status) == "PaymentStatus.REFUNDED")

    ref2 = pay.refund(pid)
    check("double refund idempotent", ref2["ok"] is True)

    r = pay.charge("order-z", 0)
    check("charge zero rejected",     r["ok"] is False)

    payments = pay.get_payments_for_order("order-1")
    check("payments for order",       len(payments) == 1)

    os.remove("test_pay.json")


# ---------------------------------------------------------------------------
# Coupon tests
# ---------------------------------------------------------------------------

def test_coupons():
    print("\nCouponManager:")
    _, _, _, coupons, _ = setup_fresh("test_cpn.json")

    coupons.create_coupon("FLASH50", 0.5, 3)
    check("remaining uses",           coupons.get_remaining_uses("FLASH50") == 3)

    r = coupons.apply_coupon("FLASH50", 10000)
    check("apply ok",                 r["ok"] is True)
    check("discount amount",          r["discount"] == 5000)
    check("remaining after use",      coupons.get_remaining_uses("FLASH50") == 2)

    coupons.apply_coupon("FLASH50", 10000)
    coupons.apply_coupon("FLASH50", 10000)
    check("exhausted",                coupons.get_remaining_uses("FLASH50") == 0)

    r = coupons.apply_coupon("FLASH50", 10000)
    check("exhausted rejected",       r["ok"] is False)

    r = coupons.apply_coupon("INVALID", 10000)
    check("invalid rejected",         r["ok"] is False)

    os.remove("test_cpn.json")


# ---------------------------------------------------------------------------
# Order tests
# ---------------------------------------------------------------------------

def test_orders():
    print("\nOrderService:")
    db, inv, pay, coupons, order_svc = setup_fresh("test_ord.json")

    # Place order
    order = order_svc.place_order("ord-001", {"p100": 2})
    check("place_order ok",           order.status == OrderStatus.PAID)
    check("payment attached",         order.payment_id is not None)
    check("stock reduced",            inv.get_stock("p100") == 8)

    # Duplicate order_id should return existing
    order2 = order_svc.place_order("ord-001", {"p100": 1})
    check("duplicate returns existing", order2.order_id == "ord-001")
    check("stock unchanged on dup",   inv.get_stock("p100") == 8)

    # Insufficient stock
    bad = order_svc.place_order("ord-002", {"p200": 100})
    check("insufficient cancelled",   bad.status == OrderStatus.CANCELLED)

    # Unknown product
    try:
        order_svc.place_order("ord-003", {"p999": 1})
        check("unknown product raises", False, "should have raised")
    except ProductNotFoundError:
        check("unknown product raises", True)

    # Get order
    o = order_svc.get_order("ord-001")
    check("get_order returns",        o is not None)
    check("order items correct",      o.items == {"p100": 2})
    check("get_order missing",        order_svc.get_order("nonexistent") is None)

    # Place order with coupon
    coupons.create_coupon("SAVE20", 0.2, 10)
    order4 = order_svc.place_order("ord-004", {"p300": 1}, coupon_code="SAVE20")
    check("coupon order ok",          order4.status == OrderStatus.PAID)
    check("coupon applied",           order4.discount_amount > 0)
    check("discounted total",         order4.total_amount < 299900)

    # Cancel order
    order5 = order_svc.place_order("ord-005", {"p400": 3})
    stock_before = inv.get_stock("p400")
    cancelled = order_svc.cancel_order("ord-005")
    check("cancel status",            cancelled.status == OrderStatus.CANCELLED)
    check("cancel releases stock",    inv.get_stock("p400") == stock_before + 3)

    # Cancel already-cancelled
    try:
        order_svc.cancel_order("ord-005")
        check("double cancel raises", False, "should raise")
    except InvalidOrderStateError:
        check("double cancel raises", True)

    # Cancel non-existent
    try:
        order_svc.cancel_order("no-such-order")
        check("cancel missing raises", False, "should raise")
    except OrderNotFoundError:
        check("cancel missing raises", True)

    os.remove("test_ord.json")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    test_inventory()
    test_payment()
    test_coupons()
    test_orders()

    print()
    total = PASSED + FAILED
    print(f"{PASSED}/{total} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)


if __name__ == "__main__":
    main()

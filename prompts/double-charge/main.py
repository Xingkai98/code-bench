#!/usr/bin/env python3
"""
CLI entry point for the e-commerce inventory system.

Usage:
    python3 main.py setup
    python3 main.py list
    python3 main.py order <order_id> <items...>
    python3 main.py status <order_id>
    python3 main.py cancel <order_id>

This is a thin wrapper — the real logic lives in inventory.py, orders.py,
payment.py, and pricing.py.
"""
from __future__ import annotations

import argparse
import os
import sys

from db import Database
from exceptions import ECommerceError
from inventory import Inventory
from models import Product
from orders import OrderService
from payment import PaymentService
from pricing import CouponManager

# ---------------------------------------------------------------------------
# Sample product catalog (prices in fen)
# ---------------------------------------------------------------------------

SAMPLE_PRODUCTS = [
    Product(product_id="p001", name="Wireless Mouse",        price=9900,  stock=100),
    Product(product_id="p002", name="Mechanical Keyboard",   price=29900, stock=50),
    Product(product_id="p003", name="USB-C Hub",             price=14900, stock=200),
    Product(product_id="p004", name="27-inch 4K Monitor",    price=299900, stock=30),
    Product(product_id="p005", name="Laptop Stand",          price=7900,  stock=150),
    Product(product_id="p006", name="Noise-Cancelling HP",   price=59900, stock=75),
    Product(product_id="p007", name="Webcam 1080p",          price=19900, stock=60),
    Product(product_id="p008", name="External SSD 1TB",      price=49900, stock=40),
]


def _fmt_price(fen: int) -> str:
    return f"¥{fen / 100:.2f}"


def cmd_setup(db: Database, inv: Inventory) -> None:
    inv.load_catalog(SAMPLE_PRODUCTS)
    print(f"Loaded {len(SAMPLE_PRODUCTS)} products.")


def cmd_list(inv: Inventory) -> None:
    print(f"{'ID':<8} {'Name':<25} {'Price':>10} {'Stock':>8}")
    print("-" * 53)
    for p in SAMPLE_PRODUCTS:
        current = inv.get_product(p.product_id)
        stock = current.stock if current else p.stock
        print(f"{p.product_id:<8} {p.name:<25} {_fmt_price(p.price):>10} {stock:>8}")


def cmd_order(order_svc: OrderService, order_id: str,
              items_str: list[str], coupon: str = "") -> None:
    items = {}
    for spec in items_str:
        pid, qty = spec.split(":")
        items[pid] = int(qty)

    try:
        order = order_svc.place_order(order_id, items, coupon_code=coupon)
        print(f"Order '{order.order_id}' — {order.status.value}")
        print(f"  Total: {_fmt_price(order.total_amount)}")
        if order.discount_amount:
            print(f"  Discount: -{_fmt_price(order.discount_amount)}")
        if order.payment_id:
            print(f"  Payment: {order.payment_id}")
    except ECommerceError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_status(order_svc: OrderService, order_id: str) -> None:
    order = order_svc.get_order(order_id)
    if order is None:
        print(f"Order '{order_id}' not found.")
        sys.exit(1)
    print(f"Order:    {order.order_id}")
    print(f"Status:   {order.status.value}")
    print(f"Total:    {_fmt_price(order.total_amount)}")
    print(f"Payment:  {order.payment_id or 'N/A'}")
    print("Items:")
    for pid, qty in order.items.items():
        print(f"  {pid}: {qty}")


def cmd_cancel(order_svc: OrderService, order_id: str) -> None:
    try:
        order_svc.cancel_order(order_id)
        print(f"Order '{order_id}' cancelled.")
    except ECommerceError as e:
        print(f"Error: {e}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="E-Commerce Inventory System")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup")
    sub.add_parser("list")

    p_order = sub.add_parser("order")
    p_order.add_argument("order_id")
    p_order.add_argument("items", nargs="+",
                         help="'product_id:quantity' (e.g. p001:2 p003:1)")
    p_order.add_argument("--coupon", default="")

    p_status = sub.add_parser("status")
    p_status.add_argument("order_id")

    p_cancel = sub.add_parser("cancel")
    p_cancel.add_argument("order_id")

    args = parser.parse_args()

    db = Database("store.json")
    inv = Inventory(db)
    pay = PaymentService(db)
    coupons = CouponManager(db)
    order_svc = OrderService(db, inv, pay, coupons)

    if args.command == "setup":
        cmd_setup(db, inv)
    elif args.command == "list":
        cmd_list(inv)
    elif args.command == "order":
        cmd_order(order_svc, args.order_id, args.items, args.coupon)
    elif args.command == "status":
        cmd_status(order_svc, args.order_id)
    elif args.command == "cancel":
        cmd_cancel(order_svc, args.order_id)


if __name__ == "__main__":
    main()

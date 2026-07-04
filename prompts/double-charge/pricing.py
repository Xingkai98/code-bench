"""
Pricing and discount engine — coupon codes, promotions.

Manages limited-quantity coupon codes for flash sales.
"""
from __future__ import annotations

from typing import Optional

from db import Database


class CouponManager:
    """Manages coupon codes with usage limits.

    Public API (preserve these signatures):
        - CouponManager(db: Database)
        - .create_coupon(code: str, discount_rate: float, max_uses: int) -> None
        - .apply_coupon(code: str, order_amount: int) -> dict
        - .get_remaining_uses(code: str) -> int
    """

    def __init__(self, db: Database):
        self.db = db

    def create_coupon(self, code: str, discount_rate: float, max_uses: int) -> None:
        """Register a new coupon code.

        Args:
            code: Coupon code string (e.g. "FLASH50").
            discount_rate: Fraction to discount, e.g. 0.1 = 10% off.
            max_uses: Maximum number of times this coupon can be used.
        """
        if not (0 < discount_rate <= 1.0):
            raise ValueError("discount_rate must be between 0 and 1")
        if max_uses <= 0:
            raise ValueError("max_uses must be positive")

        self.db.write("coupons", code, {
            "code": code,
            "discount_rate": discount_rate,
            "max_uses": max_uses,
            "used_count": 0,
        })

    def apply_coupon(self, code: str, order_amount: int) -> dict:
        """Apply a coupon to an order amount.

        Returns::

            {"ok": bool, "discount": int, "error": str}

        The discount is returned as an integer amount (in fen).
        """
        if order_amount <= 0:
            return {"ok": False, "discount": 0, "error": "invalid order amount"}

        coupon = self.db.read("coupons", code)
        if coupon is None:
            return {"ok": False, "discount": 0, "error": "invalid coupon code"}

        if coupon["used_count"] >= coupon["max_uses"]:
            return {"ok": False, "discount": 0, "error": "coupon exhausted"}

        coupon["used_count"] += 1
        self.db.write("coupons", code, coupon)

        discount = int(order_amount * coupon["discount_rate"])
        return {"ok": True, "discount": discount, "error": ""}

    def get_remaining_uses(self, code: str) -> int:
        """Return how many more times this coupon can be used."""
        coupon = self.db.read("coupons", code)
        if coupon is None:
            return 0
        return max(0, coupon["max_uses"] - coupon["used_count"])

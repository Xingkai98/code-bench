"""
Order service — orchestrates the order placement flow.

Coordinates inventory reservation, payment charging, and coupon application.
"""
from __future__ import annotations

from typing import Optional

from db import Database
from exceptions import (
    InvalidOrderStateError,
    OrderNotFoundError,
    ProductNotFoundError,
)
from inventory import Inventory
from models import Order, OrderStatus
from payment import PaymentService
from pricing import CouponManager


class OrderService:
    """Orchestrates order placement and lifecycle.

    Public API (preserve these signatures):
        - OrderService(db: Database, inventory: Inventory,
                       payment: PaymentService, coupons: CouponManager)
        - .place_order(order_id: str, items: dict[str, int],
                       card_token: str, coupon_code: str) -> Order
        - .get_order(order_id: str) -> Optional[Order]
        - .cancel_order(order_id: str) -> Order
    """

    def __init__(
        self,
        db: Database,
        inventory: Inventory,
        payment: PaymentService,
        coupons: CouponManager,
    ):
        self.db = db
        self.inventory = inventory
        self.payment = payment
        self.coupons = coupons

    def place_order(
        self,
        order_id: str,
        items: dict[str, int],
        card_token: str = "tok_visa_4242",
        coupon_code: str = "",
    ) -> Order:
        """Place an order: validate, reserve inventory, charge payment.

        Args:
            order_id: Unique identifier for the order.
            items: Dict mapping ``product_id`` to ``quantity``.
            card_token: Payment token for charging the card.
            coupon_code: Optional coupon code to apply.

        Returns:
            The Order object with its final status.
        """
        # ---- Duplicate check ----
        existing = self.db.read("orders", order_id)
        if existing is not None:
            return Order.from_dict(existing)

        # ---- Validate all products exist ----
        for product_id in items:
            product = self.inventory.get_product(product_id)
            if product is None:
                raise ProductNotFoundError(product_id)

        # ---- Calculate total ----
        total = 0
        for product_id, quantity in items.items():
            product = self.inventory.get_product(product_id)
            total += product.price * quantity

        # ---- Create order (PENDING) ----
        order = Order(
            order_id=order_id,
            items=items,
            status=OrderStatus.PENDING,
            total_amount=total,
        )
        self.db.write("orders", order_id, order.to_dict())

        # ---- Reserve inventory for each item ----
        for product_id, quantity in items.items():
            reserved = self.inventory.reserve(product_id, quantity)
            if not reserved:
                order.status = OrderStatus.CANCELLED
                self.db.write("orders", order_id, order.to_dict())
                return order

        order.status = OrderStatus.RESERVED
        self.db.write("orders", order_id, order.to_dict())

        # ---- Apply coupon (if any) ----
        if coupon_code:
            result = self.coupons.apply_coupon(coupon_code, total)
            if result["ok"]:
                order.discount_amount = result["discount"]
                order.total_amount = total - result["discount"]

        # ---- Charge payment ----
        charge_amount = order.total_amount
        result = self.payment.charge(order_id, charge_amount, card_token)

        if result["ok"]:
            order.status = OrderStatus.PAID
            order.payment_id = result["payment_id"]
        else:
            order.status = OrderStatus.CANCELLED

        self.db.write("orders", order_id, order.to_dict())
        return order

    def cancel_order(self, order_id: str) -> Order:
        """Cancel an order: release inventory and refund payment.

        Raises:
            OrderNotFoundError: if order does not exist.
            InvalidOrderStateError: if order cannot be cancelled.
        """
        d = self.db.read("orders", order_id)
        if d is None:
            raise OrderNotFoundError(order_id)

        order = Order.from_dict(d)

        cancellable = {OrderStatus.PENDING, OrderStatus.RESERVED, OrderStatus.PAID}
        if order.status not in cancellable:
            raise InvalidOrderStateError(
                order_id,
                order.status.value,
                ", ".join(s.value for s in cancellable),
            )

        # Release inventory
        for product_id, quantity in order.items.items():
            self.inventory.release(product_id, quantity)

        # Refund payment if one exists
        if order.payment_id:
            self.payment.refund(order.payment_id)

        order.status = OrderStatus.CANCELLED
        self.db.write("orders", order_id, order.to_dict())

        return order

    def get_order(self, order_id: str) -> Optional[Order]:
        """Look up an order by ID."""
        d = self.db.read("orders", order_id)
        if d is None:
            return None
        return Order.from_dict(d)

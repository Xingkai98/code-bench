"""
Payment processing — card charges and refunds.

Simulates an external payment gateway with realistic latency.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from db import Database
from models import Payment, PaymentStatus


class PaymentService:
    """Handles card charges and refunds.

    Public API (preserve these signatures):
        - PaymentService(db: Database)
        - .charge(order_id: str, amount: int, card_token: str) -> dict
        - .refund(payment_id: str) -> dict
        - .get_payment(payment_id: str) -> Optional[Payment]
        - .get_payments_for_order(order_id: str) -> list[Payment]
    """

    def __init__(self, db: Database):
        self.db = db

    def charge(self, order_id: str, amount: int, card_token: str = "") -> dict:
        """Charge *amount* to the card identified by *card_token*.

        Returns::

            {"ok": bool, "payment_id": str, "error": str}
        """
        if amount <= 0:
            return {"ok": False, "payment_id": "", "error": "amount must be positive"}

        # Check for existing completed payment (idempotency guard)
        existing = self.get_payments_for_order(order_id)
        for p in existing:
            if p.status == PaymentStatus.COMPLETED:
                return {"ok": True, "payment_id": p.payment_id, "error": ""}

        # Simulate external payment gateway call
        time.sleep(0.0002)

        payment_id = f"pay_{uuid.uuid4().hex[:12]}"

        payment = Payment(
            payment_id=payment_id,
            order_id=order_id,
            amount=amount,
            status=PaymentStatus.COMPLETED,
            card_last_four=card_token[-4:] if len(card_token) >= 4 else "****",
        )
        self.db.write("payments", payment_id, payment.to_dict())

        return {"ok": True, "payment_id": payment_id, "error": ""}

    def refund(self, payment_id: str) -> dict:
        """Refund a previously completed payment.

        Returns::

            {"ok": bool, "error": str}
        """
        d = self.db.read("payments", payment_id)
        if d is None:
            return {"ok": False, "error": f"payment not found: {payment_id}"}

        payment = Payment.from_dict(d)

        if payment.status == PaymentStatus.REFUNDED:
            return {"ok": True, "error": ""}

        if payment.status != PaymentStatus.COMPLETED:
            return {
                "ok": False,
                "error": f"cannot refund payment in state '{payment.status.value}'",
            }

        time.sleep(0.0002)

        payment.status = PaymentStatus.REFUNDED
        self.db.write("payments", payment_id, payment.to_dict())

        return {"ok": True, "error": ""}

    def get_payment(self, payment_id: str) -> Optional[Payment]:
        """Look up a payment by ID."""
        d = self.db.read("payments", payment_id)
        if d is None:
            return None
        return Payment.from_dict(d)

    def get_payments_for_order(self, order_id: str) -> list[Payment]:
        """Return all payment records associated with an order."""
        all_payments = self.db.list_all("payments")
        return [
            Payment.from_dict(p)
            for p in all_payments
            if p.get("order_id") == order_id
        ]

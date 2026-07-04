"""Data models for the e-commerce system.

Uses dataclasses with to_dict()/from_dict() for JSON-file persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class OrderStatus(Enum):
    """Valid states for an order."""
    PENDING = "pending"
    RESERVED = "reserved"
    PAID = "paid"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentStatus(Enum):
    """Valid states for a payment record."""
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


@dataclass
class Product:
    """A product in the catalog. Price is in fen (smallest currency unit)."""
    product_id: str
    name: str
    price: int
    stock: int

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "name": self.name,
            "price": self.price,
            "stock": self.stock,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Product":
        return cls(**d)


@dataclass
class Order:
    """An order placed by a customer."""
    order_id: str
    items: dict                    # {product_id: quantity}
    status: OrderStatus = OrderStatus.PENDING
    total_amount: int = 0
    discount_amount: int = 0
    payment_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "items": self.items,
            "status": self.status.value,
            "total_amount": self.total_amount,
            "discount_amount": self.discount_amount,
            "payment_id": self.payment_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        d = dict(d)
        d["status"] = OrderStatus(d["status"])
        return cls(**d)


@dataclass
class Payment:
    """A payment transaction record."""
    payment_id: str
    order_id: str
    amount: int
    status: PaymentStatus = PaymentStatus.PENDING
    card_last_four: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "payment_id": self.payment_id,
            "order_id": self.order_id,
            "amount": self.amount,
            "status": self.status.value,
            "card_last_four": self.card_last_four,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Payment":
        d = dict(d)
        d["status"] = PaymentStatus(d["status"])
        return cls(**d)

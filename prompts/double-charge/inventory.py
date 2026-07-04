"""
Inventory management — stock tracking and reservation.

Handles product catalog and stock reservations for the order flow.
"""
from __future__ import annotations

from typing import Optional

from db import Database
from models import Product


class Inventory:
    """Manages product stock levels with reservation support.

    Public API (preserve these signatures):
        - Inventory(db: Database)
        - .load_catalog(products: list[Product]) -> None
        - .get_product(product_id: str) -> Optional[Product]
        - .reserve(product_id: str, quantity: int) -> bool
        - .release(product_id: str, quantity: int) -> None
        - .get_stock(product_id: str) -> int
    """

    def __init__(self, db: Database):
        self.db = db

    def load_catalog(self, products: list[Product]) -> None:
        """Bulk-load products into the catalog."""
        for product in products:
            self.db.write("products", product.product_id, product.to_dict())

    def get_product(self, product_id: str) -> Optional[Product]:
        """Look up a product by ID."""
        d = self.db.read("products", product_id)
        if d is None:
            return None
        return Product.from_dict(d)

    def reserve(self, product_id: str, quantity: int) -> bool:
        """Attempt to reserve *quantity* units of *product_id*.

        Returns True on success, False if stock is insufficient.
        """
        if quantity <= 0:
            return False

        product = self.get_product(product_id)
        if product is None:
            return False

        if product.stock >= quantity:
            product.stock -= quantity
            self.db.write("products", product_id, product.to_dict())
            return True

        return False

    def release(self, product_id: str, quantity: int) -> None:
        """Return previously reserved stock back to inventory."""
        if quantity <= 0:
            return

        product = self.get_product(product_id)
        if product is None:
            return

        product.stock += quantity
        self.db.write("products", product_id, product.to_dict())

    def get_stock(self, product_id: str) -> int:
        """Return current available stock for a product."""
        product = self.get_product(product_id)
        return product.stock if product else 0

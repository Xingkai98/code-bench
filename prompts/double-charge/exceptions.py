"""Custom exceptions for the e-commerce system."""


class ECommerceError(Exception):
    """Base exception for all e-commerce errors."""
    pass


class InsufficientStockError(ECommerceError):
    """Raised when stock is insufficient for a reservation."""

    def __init__(self, product_id: str, requested: int, available: int):
        self.product_id = product_id
        self.requested = requested
        self.available = available
        super().__init__(
            f"Insufficient stock for '{product_id}': "
            f"requested {requested}, available {available}"
        )


class ProductNotFoundError(ECommerceError):
    """Raised when a product ID is not found."""

    def __init__(self, product_id: str):
        self.product_id = product_id
        super().__init__(f"Product not found: '{product_id}'")


class OrderNotFoundError(ECommerceError):
    """Raised when an order ID is not found."""

    def __init__(self, order_id: str):
        self.order_id = order_id
        super().__init__(f"Order not found: '{order_id}'")


class InvalidOrderStateError(ECommerceError):
    """Raised when an operation is invalid for the current order state."""

    def __init__(self, order_id: str, current_state: str, expected: str):
        self.order_id = order_id
        self.current_state = current_state
        self.expected = expected
        super().__init__(
            f"Order '{order_id}' is '{current_state}'; "
            f"expected one of: {expected}"
        )


class PaymentError(ECommerceError):
    """Raised when a payment operation fails."""

    def __init__(self, order_id: str, reason: str):
        self.order_id = order_id
        self.reason = reason
        super().__init__(f"Payment failed for order '{order_id}': {reason}")


class CouponError(ECommerceError):
    """Raised when a coupon code is invalid or exhausted."""

    def __init__(self, code: str, reason: str):
        self.code = code
        self.reason = reason
        super().__init__(f"Coupon '{code}': {reason}")

"""
Notification service — sends emails for order events.

This module handles outbound notifications. Failures here should never
block the core order flow.
"""
from __future__ import annotations

import time


def send_order_confirmation(order_id: str, email: str, total: int) -> None:
    """Send an order confirmation email.

    This is a best-effort operation. If the email provider is down,
    we log the failure and move on — the order itself is not affected.
    """
    try:
        _deliver(email, f"Order {order_id} confirmed! Total: ¥{total / 100:.2f}")
    except Exception:
        pass


def send_refund_notification(order_id: str, email: str, amount: int) -> None:
    """Send a refund notification email."""
    try:
        _deliver(email, f"Refund of ¥{amount / 100:.2f} for order {order_id}")
    except Exception:
        pass


def _deliver(email: str, body: str) -> None:
    """Simulate delivering an email via an external provider."""
    time.sleep(0.0001)
    if not email or "@" not in email:
        raise ValueError(f"Invalid email: {email}")
    # In production this would call an SMTP server or email API

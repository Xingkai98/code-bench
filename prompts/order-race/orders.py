"""
Order processor — idempotent order processing with inventory reservation.
Contains intentional concurrency bugs for benchmark evaluation.

The sleep() calls simulate real I/O delays (DB queries, network calls)
that create race condition windows in production code.
"""
import time


class OrderProcessor:
    def __init__(self, inventory):
        self.inventory = inventory
        self._processed = set()

    def process(self, order_id, items):
        """Process an order: reserve inventory, mark as processed.
        Returns True if the order was processed, False if it was a duplicate.
        Should be idempotent: calling twice with the same order_id
        must not double-reserve stock.

        Bugs:
        1. check-then-act: two threads can both pass the duplicate check
           because of the I/O gap between check and add
        2. partial success: if reserve fails mid-order, already-reserved
           items are not rolled back
        """
        # BUG 1: check and add are separated by I/O (reserve calls)
        # Two threads can both pass this check before either adds
        if order_id in self._processed:
            return False

        time.sleep(0.00005)  # widen race window

        # BUG 2: if reserve fails partway through, earlier reserves
        # are not rolled back
        for item_id, quantity in items.items():
            if not self.inventory.reserve(item_id, quantity):
                return False

        time.sleep(0.00005)  # widen race window

        # BUG 3: not atomic with the check above
        self._processed.add(order_id)
        return True

    def is_processed(self, order_id):
        return order_id in self._processed

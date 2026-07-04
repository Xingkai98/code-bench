"""
Inventory manager with stock reservation.
Contains intentional concurrency bugs for benchmark evaluation.

The sleep() calls simulate real I/O delays (DB queries, network calls)
that create race condition windows in production code.
"""
import threading
import time


class Inventory:
    def __init__(self, initial_stock):
        self._stock = dict(initial_stock)
        self._lock = threading.Lock()  # Bug: declared but never used

    def reserve(self, item_id, quantity):
        """Try to reserve quantity of item. Returns True on success.

        Bug: read and write are not atomic. The sleep() between check
        and act simulates a real I/O gap (like a DB read then write)
        that makes the race condition practically triggerable.
        """
        if quantity <= 0:
            return False

        # Simulate I/O: read current stock (e.g. DB query)
        current = self._stock.get(item_id, 0)
        time.sleep(0.0001)  # widen race window (simulates I/O)

        # BUG: another thread may have changed stock between the read and write
        if current >= quantity:
            time.sleep(0.0001)  # widen race window further
            self._stock[item_id] = current - quantity
            return True
        return False

    def rollback(self, item_id, quantity):
        """Return reserved stock back to inventory.
        Bug: not thread-safe (read-modify-write not atomic).
        """
        time.sleep(0.00005)  # widen race window
        if item_id in self._stock:
            current = self._stock[item_id]
            time.sleep(0.00005)
            self._stock[item_id] = current + quantity

    def get_stock(self, item_id):
        return self._stock.get(item_id, 0)

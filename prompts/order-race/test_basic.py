#!/usr/bin/env python3
"""Basic single-threaded correctness tests. No concurrency.
Run: python3 test_basic.py
"""
import sys
from inventory import Inventory
from orders import OrderProcessor

PASSED = 0
FAILED = 0

def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}  — {detail}")

# --- Inventory ---
print("Inventory:")
inv = Inventory({"apple": 10, "banana": 5})

check("init stock", inv.get_stock("apple") == 10, f"got {inv.get_stock('apple')}")
check("unknown item", inv.get_stock("orange") == 0, f"got {inv.get_stock('orange')}")

check("reserve ok", inv.reserve("apple", 3) == True)
check("stock after reserve", inv.get_stock("apple") == 7, f"got {inv.get_stock('apple')}")

check("reserve too many", inv.reserve("banana", 10) == False)
check("stock unchanged after fail", inv.get_stock("banana") == 5)

check("reserve exact", inv.reserve("banana", 5) == True)
check("stock zero", inv.get_stock("banana") == 0)

inv.rollback("banana", 3)
check("rollback", inv.get_stock("banana") == 3, f"got {inv.get_stock('banana')}")

check("reserve zero", inv.reserve("apple", 0) == False)
check("reserve negative", inv.reserve("apple", -1) == False)

print()
print("OrderProcessor:")
proc = OrderProcessor(inv)

check("process ok", proc.process("order-1", {"apple": 2}) == True)
check("stock after order", inv.get_stock("apple") == 5, f"got {inv.get_stock('apple')}")

check("duplicate rejected", proc.process("order-1", {"apple": 1}) == False)
check("stock unchanged after dup", inv.get_stock("apple") == 5)

check("is_processed true", proc.is_processed("order-1") == True)
check("is_processed false", proc.is_processed("order-99") == False)

check("process with insuff stock", proc.process("order-2", {"banana": 100}) == False)
check("stock unchanged after fail", inv.get_stock("banana") == 3)

print()
print(f"{PASSED} passed, {FAILED} failed")
sys.exit(0 if FAILED == 0 else 1)

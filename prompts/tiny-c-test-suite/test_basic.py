#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main():
    tests = ROOT / "tests"
    assert tests.exists(), "add pytest tests under tests/"
    test_files = list(tests.glob("test_*.py"))
    assert test_files, "no pytest test_*.py files found"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(tests)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "test_" in proc.stdout, proc.stdout


if __name__ == "__main__":
    main()

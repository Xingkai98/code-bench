#!/usr/bin/env python3
"""Eval script for the code-gen prompt.
Receives the workspace path as argv[1]. Outputs JSON score to stdout."""

import json
import subprocess
import sys
from pathlib import Path


def main():
    workdir = Path(sys.argv[1])

    score = 0.0
    details = {}
    checks = []

    # Check 1: server.go exists
    server_go = workdir / "server.go"
    if server_go.exists():
        details["file_exists"] = 1.0
        checks.append("server.go found")
    else:
        details["file_exists"] = 0.0
        details["score"] = 0.0
        details["checks"] = ["server.go not found"]
        print(json.dumps({"score": 0.0, "details": details, "summary": "server.go not found"}))
        return

    # Check 2: content looks like an HTTP server
    content = server_go.read_text()
    has_http = "net/http" in content or "http." in content
    has_hello = "hello" in content.lower() or "world" in content.lower()
    has_json = "json" in content.lower() or "application/json" in content

    details["has_http"] = 1.0 if has_http else 0.0
    details["has_hello"] = 1.0 if has_hello else 0.0
    details["has_json"] = 1.0 if has_json else 0.0
    checks.append(f"http={has_http}, hello={has_hello}, json={has_json}")

    # Check 3: compiles
    try:
        result = subprocess.run(
            ["go", "build", "-o", str(workdir / "server"), str(server_go)],
            capture_output=True, text=True, timeout=30,
        )
        details["compiles"] = 1.0 if result.returncode == 0 else 0.0
        if result.returncode == 0:
            checks.append("compiles OK")
        else:
            checks.append(f"compilation failed: {result.stderr[:200]}")
    except FileNotFoundError:
        details["compiles"] = 0.5  # go not installed, partial credit
        checks.append("go compiler not available, compilation skipped")
    except Exception as e:
        details["compiles"] = 0.0
        checks.append(f"build error: {e}")

    # Overall score: average of detail scores
    scores = [v for v in details.values() if isinstance(v, (int, float))]
    score = sum(scores) / len(scores) if scores else 0.0

    print(json.dumps({
        "score": round(score, 3),
        "details": details,
        "summary": "; ".join(checks),
    }))


if __name__ == "__main__":
    main()

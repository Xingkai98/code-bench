#!/usr/bin/env python3
"""Eval template for bench prompts.

Receives the workspace path as argv[1].
Must output JSON to stdout with: score, details (optional), summary (optional).

  python3 eval.py <workspace_path>

Example output:
  {"score": 0.85, "details": {"correctness": 1.0}, "summary": "almost perfect"}
"""

import json
import sys
from pathlib import Path


def main():
    workdir = Path(sys.argv[1])

    # --- implement your scoring logic here ---
    # Read files from workdir, run tests, evaluate output, etc.

    score = 0.0
    details = {}
    checks = []

    # Example: check if expected output file exists
    output_file = workdir / "server.go"
    if output_file.exists():
        details["output_exists"] = 1.0
        checks.append("server.go found")
        score += 1.0 / 1  # one check only, full score
    else:
        details["output_exists"] = 0.0
        checks.append("server.go not found")

    # --- output ---
    print(json.dumps({
        "score": round(score, 3),
        "details": details,
        "summary": "; ".join(checks),
    }))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Eval script for bench prompts.

Usage: python3 eval.py <workspace_path>

Must output a single JSON object to stdout with these fields:
  - score (float, required):   overall score 0.0–1.0
  - details (dict, optional):  sub-scores (e.g. {"correctness": 0.9})
  - summary (str, optional):   one-line human-readable note

Example:
  {"score": 0.85, "details": {"correctness": 1.0, "style": 0.7}, "summary": "correct but messy"}
"""

import json
import sys
from pathlib import Path


def main():
    workdir = Path(sys.argv[1])

    # TODO: implement your scoring logic here
    score = 0.0
    details = {}
    checks = []

    # --- scoring ---

    # --- output (keep this format) ---
    print(json.dumps({
        "score": round(score, 3),
        "details": details,
        "summary": "; ".join(checks),
    }))


if __name__ == "__main__":
    main()

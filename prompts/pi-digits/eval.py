#!/usr/bin/env python3
"""Eval: pi first 100k digits, 3rd most frequent number."""

import json
import sys
from pathlib import Path

EXPECTED = "3"


def main():
    workdir = Path(sys.argv[1])
    answer_file = workdir / "answer.txt"
    score = 0.0
    details = {}
    checks = []

    if answer_file.exists():
        details["file_exists"] = 1.0
        answer = answer_file.read_text().strip()
        correct = answer == EXPECTED
        details["correct"] = 1.0 if correct else 0.0
        details["answer"] = answer
        if correct:
            score = 1.0
            checks.append(f"correct: {answer}")
        else:
            score = 0.0
            checks.append(f"got {answer}, expected {EXPECTED}")
    else:
        details["file_exists"] = 0.0
        details["answer"] = None
        checks.append("answer.txt not found")

    print(json.dumps({
        "score": score,
        "details": details,
        "summary": "; ".join(checks),
    }))


if __name__ == "__main__":
    main()

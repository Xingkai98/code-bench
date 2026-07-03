#!/usr/bin/env bash
set -euo pipefail
BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$BENCH_DIR/run.py" "$@"

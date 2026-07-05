#!/usr/bin/env python3
"""Extract benchmark metrics from a Claude Code session JSONL file.

Parses the session log that Claude Code writes to
~/.claude/projects/<project-hash>/<session-id>.jsonl during every session.

Usage:
    python3 extract_tui_metrics.py <session.jsonl> [--model NAME]
"""

import json
import sys
from datetime import datetime
from pathlib import Path


def parse_session_jsonl(jsonl_path, model_name=None):
    """Parse a Claude Code session JSONL and extract aggregate metrics.

    The session JSONL is raw JSON objects (one per line). Each assistant
    event carries per-message usage data. We sum across all assistant
    events to get totals.
    """
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read = 0
    total_cache_create = 0
    tool_count = 0
    turn_count = 0
    thinking_peak = 0

    start_ts = None
    end_ts = None

    with open(jsonl_path) as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            if not line.startswith("{"):
                continue

            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue

            t = evt.get("type", "")

            # Model name: grab from first assistant event
            if model_name is None and t == "assistant":
                model_name = evt.get("message", {}).get("model")

            # Thinking tokens
            if t == "system" and evt.get("subtype") == "thinking_tokens":
                peak = evt.get("estimated_tokens", 0)
                if peak > thinking_peak:
                    thinking_peak = peak

            # Assistant events carry usage + tool calls
            if t == "assistant":
                turn_count += 1
                msg = evt.get("message", {})
                usage = msg.get("usage", {})
                total_input_tokens += usage.get("input_tokens", 0)
                total_output_tokens += usage.get("output_tokens", 0)
                total_cache_read += usage.get("cache_read_input_tokens", 0)
                total_cache_create += usage.get("cache_creation_input_tokens", 0)

                for block in msg.get("content", []):
                    if block.get("type") == "tool_use":
                        tool_count += 1

            # Timestamps for duration
            ts_str = evt.get("timestamp")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if start_ts is None:
                        start_ts = ts
                    end_ts = ts
                except ValueError:
                    pass

    duration_ms = None
    if start_ts and end_ts:
        duration_ms = int((end_ts - start_ts).total_seconds() * 1000)

    return {
        "duration_ms": duration_ms,
        "num_turns": turn_count,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "cache_read_input_tokens": total_cache_read,
        "cache_create_input_tokens": total_cache_create,
        "tool_call_count": tool_count,
        "thinking_token_peak": thinking_peak,
        "model_name": model_name,
    }


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <session.jsonl> [--model NAME]")
        sys.exit(1)

    jsonl_path = Path(sys.argv[1])
    if not jsonl_path.exists():
        print(f"ERROR: file not found: {jsonl_path}")
        sys.exit(1)

    model_name = None
    args = sys.argv[2:]
    for i, arg in enumerate(args):
        if arg == "--model" and i + 1 < len(args):
            model_name = args[i + 1]

    metrics = parse_session_jsonl(jsonl_path, model_name=model_name)

    json.dump(metrics, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()

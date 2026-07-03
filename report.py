#!/usr/bin/env python3
"""Generate Markdown benchmark report from summary.json."""

import json
import sys
from datetime import datetime
from pathlib import Path


def fmt_ms(v):
    if v is None:
        return "-"
    return f"{v:.0f}ms"


def fmt_usd(v):
    if v is None:
        return "-"
    return f"${v:.4f}"


def fmt_num(v):
    if v is None:
        return "-"
    return f"{v:.0f}"


def fmt_score(v):
    if v is None:
        return "-"
    return f"{v:.2f}"


def pick_models(entries):
    """Deduplicate model names in order of first appearance."""
    seen = []
    for e in entries:
        name = e.get("model_name", e.get("model_config", "?"))
        if name not in seen:
            seen.append(name)
    return seen


def pick_prompts(entries):
    seen = []
    for e in entries:
        name = e.get("prompt", "?")
        if name not in seen:
            seen.append(name)
    return seen


def summary_table(entries):
    lines = []
    header = "| Model | Prompt | Runs | Duration (avg) | TTFT (avg) | Cost (avg) | Score | Tokens In | Tokens Out | Tool Calls | Thinking Peak |"
    sep = "|-------|--------|------|----------------|------------|------------|-------|-----------|------------|------------|---------------|"
    lines.append(header)
    lines.append(sep)

    for e in entries:
        m = e.get("metrics", {})
        model = e.get("model_name", e.get("model_config", "?"))
        prompt = e.get("prompt", "?")
        ok = e.get("successful_runs", 0)
        total = e.get("total_runs", 0)

        row = (
            f"| {model} | {prompt} | {ok}/{total} | "
            f"{fmt_ms(m.get('duration_ms', {}).get('avg'))} | "
            f"{fmt_ms(m.get('ttft_ms', {}).get('avg'))} | "
            f"{fmt_usd(m.get('total_cost_usd', {}).get('avg'))} | "
            f"{fmt_score(m.get('score', {}).get('avg'))} | "
            f"{fmt_num(m.get('input_tokens', {}).get('avg'))} | "
            f"{fmt_num(m.get('output_tokens', {}).get('avg'))} | "
            f"{fmt_num(m.get('tool_call_count', {}).get('avg'))} | "
            f"{fmt_num(m.get('thinking_token_peak', {}).get('avg'))} |"
        )
        lines.append(row)

    return "\n".join(lines)


def prompt_detail(prompt_name, entries):
    lines = []
    lines.append(f"## Prompt: `{prompt_name}`")
    lines.append("")

    models = pick_models(entries)
    metrics_keys = [
        ("duration_ms", "Duration (ms)"),
        ("duration_api_ms", "API Duration (ms)"),
        ("ttft_ms", "TTFT (ms)"),
        ("num_turns", "Turns"),
        ("total_cost_usd", "Cost (USD)"),
        ("score", "Score"),
        ("input_tokens", "Input Tokens"),
        ("output_tokens", "Output Tokens"),
        ("tool_call_count", "Tool Calls"),
        ("thinking_token_peak", "Thinking Peak"),
    ]

    def _fmt_val(key, val):
        if val is None:
            return "-"
        if key == "total_cost_usd":
            return fmt_usd(val)
        if key == "score":
            return fmt_score(val)
        if key in ("input_tokens", "output_tokens", "num_turns", "tool_call_count", "thinking_token_peak"):
            return fmt_num(val)
        return fmt_ms(val)

    # cross-model comparison table
    lines.append("### Comparison")
    lines.append("")
    header = "| Metric | " + " | ".join(models) + " |"
    lines.append(header)
    lines.append("|--------|" + "|".join(["-" * len(m) + "------" for m in models]) + "|")

    for key, label in metrics_keys:
        vals = []
        for e in entries:
            m = e.get("metrics", {}).get(key, {})
            avg = m.get("avg")
            vals.append(_fmt_val(key, avg) if isinstance(avg, (int, float)) else "-")
        lines.append(f"| {label} | " + " | ".join(vals) + " |")

    lines.append("")

    # per-model run breakdown
    for e in entries:
        model = e.get("model_name", e.get("model_config", "?"))
        lines.append(f"### {model} — Individual Runs")
        lines.append("")

        # load individual run metrics
        run_files = sorted(Path(e.get("_run_dir", ".")).glob("run-*.metrics.json"))
        if run_files:
            lines.append("| Run | Status | Wall (ms) | Duration (ms) | TTFT (ms) | Turns | Cost | Score | Tokens In | Tokens Out | Tools |")
            lines.append("|-----|--------|-----------|---------------|-----------|-------|------|-------|-----------|------------|-------|")
            for rf in run_files:
                with open(rf) as fh:
                    rd = json.load(fh)
                rm = rd.get("metrics", {})
                lines.append(
                    f"| {rd['run']} | {rd['status']} | "
                    f"{fmt_num(rm.get('wall_duration_ms'))} | "
                    f"{fmt_num(rm.get('duration_ms'))} | "
                    f"{fmt_num(rm.get('ttft_ms'))} | "
                    f"{fmt_num(rm.get('num_turns'))} | "
                    f"{fmt_usd(rm.get('total_cost_usd'))} | "
                    f"{fmt_score(rm.get('score'))} | "
                    f"{fmt_num(rm.get('input_tokens'))} | "
                    f"{fmt_num(rm.get('output_tokens'))} | "
                    f"{fmt_num(rm.get('tool_call_count'))} |"
                )
                # show eval summary if present
                es = rm.get("eval_summary")
                if es:
                    lines.append(f"| | | | | | | | Eval: {es} | | | |")
                ed = rm.get("eval_details")
                if ed and isinstance(ed, dict):
                    detail_parts = ", ".join(f"{k}={v}" for k, v in ed.items())
                    lines.append(f"| | | | | | | | Details: {detail_parts} | | | |")
            lines.append("")

    return "\n".join(lines)


def generate_report(summary_file, output_file):
    with open(summary_file) as f:
        data = json.load(f)

    entries = list(data)
    results_dir = str(Path(summary_file).parent)

    # inject _run_dir for each entry so prompt_detail can find individual runs
    for e in entries:
        model = e.get("model_config", e.get("model_name", "?"))
        prompt = e.get("prompt", "")
        e["_run_dir"] = str(Path(results_dir) / f"{model}__{prompt}")

    prompts = pick_prompts(entries)

    lines = []
    lines.append("# Claude Code Model Benchmark Report")
    lines.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Results dir:** `{results_dir}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(summary_table(entries))
    lines.append("")

    for prompt in prompts:
        prompt_entries = [e for e in entries if e.get("prompt") == prompt]
        lines.append(prompt_detail(prompt, prompt_entries))

    lines.append("## Raw Data")
    lines.append(f"All raw NDJSON streams and per-run metrics: `{results_dir}/`")
    lines.append("")

    report = "\n".join(lines)
    with open(output_file, "w") as f:
        f.write(report)

    return report


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <summary.json> <output.md>")
        sys.exit(1)
    generate_report(sys.argv[1], sys.argv[2])

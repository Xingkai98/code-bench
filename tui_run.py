#!/usr/bin/env python3
"""TUI benchmark runner — interactive Claude Code sessions with automatic metrics.

Workflow:
  1. Pick a model from config.json
  2. Workspace is prepared with seed files
  3. Claude Code TUI launches — you interact naturally
  4. When you /exit, metrics are extracted from session logs and eval is run
  5. Results saved alongside automated benchmark results

Usage:
    python3 tui_run.py                          # pick model interactively
    python3 tui_run.py --model ds-v4-pro        # specify model directly
    python3 tui_run.py --list                   # list available models
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


BENCH_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def log(msg):
    print(f"  {msg}")


def banner(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


def load_config():
    with open(BENCH_DIR / "config.json") as f:
        return json.load(f)


def write_temp_settings(model_cfg):
    """Write a temp settings file for the chosen model."""
    settings = {k: v for k, v in model_cfg.items() if k != "name"}
    tmp = BENCH_DIR / f".tui_settings_{model_cfg['name']}.json"
    tmp.write_text(json.dumps(settings))
    return tmp


def list_available_models(cfg):
    models = []
    for m in cfg["models"]:
        models.append({
            "name": m["name"],
            "model": m.get("model", m["name"]),
            "base_url": m.get("env", {}).get("ANTHROPIC_BASE_URL", "default"),
        })
    return models


def pick_model(models):
    print("\nAvailable models:")
    for i, m in enumerate(models, 1):
        print(f"  [{i}] {m['name']:20s} → {m['model']} ({m['base_url']})")
    print(f"  [q] quit")

    while True:
        choice = input("\nPick a model → ").strip()
        if choice.lower() == "q":
            sys.exit(0)
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                return models[idx]
        except ValueError:
            pass
        print(f"  Enter 1-{len(models)} or q")


def resolve_prompt(prompt_ref):
    """Resolve a prompt reference to (name, content, eval_script, seed_dir)."""
    ref = BENCH_DIR / prompt_ref
    if ref.is_dir():
        prompt_file = ref / "prompt.txt"
        if not prompt_file.exists():
            raise FileNotFoundError(f"{prompt_file} not found")
        eval_file = ref / "eval.py"
        return ref.name, prompt_file.read_text(), eval_file if eval_file.exists() else None, ref
    raise FileNotFoundError(f"Cannot resolve prompt: {prompt_ref}")


# ---------------------------------------------------------------------------
# session JSONL discovery
# ---------------------------------------------------------------------------

def find_newest_session(projects_dir, newer_than=None):
    """Find the most recently modified JSONL file under projects_dir.

    If newer_than (a Path) is provided, only consider files newer than it.
    """
    candidates = []
    marker_time = newer_than.stat().st_mtime if newer_than else 0
    for p in projects_dir.rglob("*.jsonl"):
        try:
            mtime = p.stat().st_mtime
            if mtime > marker_time:
                candidates.append((mtime, p))
        except OSError:
            pass
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------

def run_eval(eval_script, workdir):
    """Run eval script, return (score, summary, details, error)."""
    try:
        proc = subprocess.run(
            [sys.executable, str(eval_script), str(workdir)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "no output")[:300]
            return None, None, None, f"eval exit {proc.returncode}: {err}"
        data = json.loads(proc.stdout)
        return data.get("score"), data.get("summary"), data.get("details"), None
    except json.JSONDecodeError:
        return None, None, None, f"eval output not valid JSON: {proc.stdout[:200]}"
    except Exception as e:
        return None, None, None, str(e)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="TUI Benchmark Runner")
    parser.add_argument("--model", "-m", help="Model config name to use")
    parser.add_argument("--prompt", "-p", help="Prompt to run (default: first in config)")
    parser.add_argument("--list", "-l", action="store_true", help="List available models")
    args = parser.parse_args()

    cfg = load_config()
    models = list_available_models(cfg)

    if args.list:
        for m in models:
            print(f"{m['name']:20s} → {m['model']} ({m['base_url']})")
        return

    # Pick model
    if args.model:
        model = next((m for m in models if m["name"] == args.model), None)
        if not model:
            print(f"ERROR: model '{args.model}' not found in config.json")
            print(f"Available: {', '.join(m['name'] for m in models)}")
            sys.exit(1)
    else:
        model = pick_model(models)

    # Resolve model config
    model_cfg = next(m for m in cfg["models"] if m["name"] == model["name"])

    # Pick prompt
    prompt_ref = args.prompt or cfg["prompts"][0]
    prompt_name, prompt_content, eval_script, seed_dir = resolve_prompt(prompt_ref)

    # Prepare workspace
    workdir = Path(tempfile.mkdtemp(prefix="tui-bench-", dir=BENCH_DIR)) / "workspace"
    workdir.mkdir(parents=True)
    log(f"Workspace: {workdir}")

    # Copy seed files
    if seed_dir and seed_dir.is_dir():
        for src in seed_dir.iterdir():
            if src.name == "eval.py":
                continue
            dst = workdir / src.name
            if src.is_file():
                shutil.copy2(src, dst)
            elif src.is_dir() and not dst.exists():
                shutil.copytree(src, dst)

    # Copy prompt.txt into workspace for reference
    (workdir / "prompt.txt").write_text(prompt_content)

    # Write settings
    settings_path = write_temp_settings(model_cfg)

    # Marker file for session discovery (touch now, find files newer than this)
    marker = Path(tempfile.mktemp(suffix=".marker", prefix="tui-bench-"))
    marker.touch()

    projects_dir = Path.home() / ".claude" / "projects"

    banner(f"TUI Benchmark — {model['name']} / {prompt_name}")
    print(f"  Model:  {model_cfg.get('model', model['name'])}")
    print(f"  Prompt: {prompt_name}")
    print(f"  Workspace: {workdir}")
    print(f"\n  ┌─────────────────────────────────────────────────────┐")
    print(f"  │ Complete the task in the TUI, then type /exit      │")
    print(f"  │ Metrics will be extracted automatically on exit.   │")
    print(f"  └─────────────────────────────────────────────────────┘")
    input("\n  Press Enter to launch Claude Code TUI...")

    # Launch Claude Code TUI
    start_time = time.time()
    cmd = [
        "claude",
        "--settings", str(settings_path),
        "--permission-mode", "acceptEdits",
        "--verbose",
    ]
    exit_code = subprocess.call(cmd, cwd=str(workdir))
    wall_duration_ms = int((time.time() - start_time) * 1000)

    banner("Session Ended — Extracting Metrics")

    # Find session JSONL (created during this run)
    session_file = find_newest_session(projects_dir, newer_than=marker)
    marker.unlink(missing_ok=True)

    if not session_file:
        print("  ⚠ Could not find session JSONL file.")
        print(f"  Look in: {projects_dir}")
        print(f"  Workspace preserved at: {workdir}")
        sys.exit(1)

    print(f"  Session log: {session_file}")

    # Extract metrics via subprocess (clean import boundary)
    extract_proc = subprocess.run(
        [sys.executable, str(BENCH_DIR / "extract_tui_metrics.py"),
         str(session_file), "--model", model_cfg.get("model", model["name"])],
        capture_output=True, text=True,
    )
    if extract_proc.returncode != 0:
        print(f"  ⚠ Metrics extraction failed: {extract_proc.stderr}")
        metrics = {"duration_ms": None, "num_turns": 0, "input_tokens": 0,
                    "output_tokens": 0, "tool_call_count": 0,
                    "thinking_token_peak": 0, "total_cost_usd": None}
    else:
        metrics = json.loads(extract_proc.stdout)
    metrics["wall_duration_ms"] = wall_duration_ms

    # Run eval
    score = None
    eval_summary = None
    eval_details = None
    if eval_script:
        score, eval_summary, eval_details, eval_error = run_eval(eval_script, workdir)
        if eval_error:
            print(f"  Eval error: {eval_error}")
        else:
            print(f"  Score: {score:.2f}" if score is not None else "  Score: N/A")
            if eval_summary:
                print(f"  {eval_summary}")

    metrics["score"] = score
    metrics["eval_details"] = eval_details
    metrics["eval_summary"] = eval_summary

    # Save results
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    results_dir = BENCH_DIR / "results" / f"{timestamp}_tui"
    run_dir = results_dir / f"{model['name']}__{prompt_name}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Save session JSONL copy
    shutil.copy2(session_file, run_dir / "session.jsonl")

    # Save metrics
    result_model = metrics.pop("model_name", model_cfg.get("model", model["name"]))
    metrics_record = {
        "run": 1,
        "model_config": model["name"],
        "model_name": result_model,
        "prompt": prompt_name,
        "status": "success" if score is not None else "completed",
        "error": None,
        "metrics": metrics,
    }
    metrics_file = run_dir / "metrics.json"
    with open(metrics_file, "w") as f:
        json.dump(metrics_record, f, indent=2)

    # Clean up temp settings
    settings_path.unlink(missing_ok=True)

    banner("Done")
    print(f"  Results: {results_dir}")
    print(f"  Metrics: {metrics_file}")
    print(f"  Workspace preserved at: {workdir}")

    # Print summary
    print(f"\n  ┌────────────────────────────────────────────┐")
    print(f"  │ Model:    {model['name']:32s} │")
    print(f"  │ Score:    {score if score is not None else 'N/A':>32s} │")
    tok = f"{metrics.get('input_tokens',0)} in / {metrics.get('output_tokens',0)} out"
    print(f"  │ Tokens:   {tok:32s} │")
    dur = f"{metrics.get('duration_ms',0)/1000:.0f}s"
    print(f"  │ Duration: {dur:32s} │")
    tools = f"{metrics.get('tool_call_count',0)} calls"
    print(f"  │ Tools:    {tools:32s} │")
    print(f"  └────────────────────────────────────────────┘")


if __name__ == "__main__":
    main()

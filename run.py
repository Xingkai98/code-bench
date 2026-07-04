#!/usr/bin/env python3
"""Benchmark runner — schedules model runs, parses stream-json, extracts metrics."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


BENCH_DIR = Path(__file__).resolve().parent


def write_temp_settings(model_cfg, results_dir):
    """Write a temporary settings file for --settings from inline model config.

    Strips the 'name' field (benchmark-internal) and writes the rest as JSON.
    Returns the path to the temp file.
    """
    settings = {k: v for k, v in model_cfg.items() if k != "name"}
    path = results_dir / f".settings_{model_cfg['name']}.json"
    path.write_text(json.dumps(settings))
    return path


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

_docker_prefix_cache = None


def _check_docker_available():
    """Check that Docker is installed and the socket is reachable.

    Returns (True, None) or (False, error_message).
    """
    if not shutil.which("docker"):
        return False, "Docker is not installed. Install Docker or disable sandbox mode."
    try:
        _resolve_docker_prefix()
        return True, None
    except RuntimeError as e:
        return False, str(e)


def _resolve_docker_prefix():
    """Return (mode, prefix_list) for docker CLI. Result is cached.

    Tries: direct docker → sg docker → sudo docker.
    """
    global _docker_prefix_cache
    if _docker_prefix_cache is not None:
        return _docker_prefix_cache

    def _try(cmd, timeout=5):
        try:
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=timeout)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    # 1. Try direct docker
    if _try(["docker", "ps"]):
        _docker_prefix_cache = ("list", ["docker"])
        return _docker_prefix_cache

    # 2. Try sg docker
    if _try(["sg", "docker", "-c", "docker ps"]):
        _docker_prefix_cache = ("shell", ["sg", "docker", "-c"])
        return _docker_prefix_cache

    # 3. Try sudo docker (non-interactive)
    if _try(["sudo", "-n", "docker", "ps"]):
        _docker_prefix_cache = ("list", ["sudo", "-n", "docker"])
        return _docker_prefix_cache

    raise RuntimeError(
        "Cannot access Docker. Run 'newgrp docker' or restart terminal."
    )


def _run_docker(cmd_args):
    """Run a docker command using the resolved prefix."""
    mode, prefix = _resolve_docker_prefix()
    if mode == "shell":
        full_cmd = prefix + ["docker " + shlex.join(cmd_args)]
    else:
        full_cmd = prefix + cmd_args
    return subprocess.run(full_cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)


def _ensure_docker_image(image, build_on_start):
    """Ensure Docker image exists; optionally auto-build it."""
    result = _run_docker(["image", "inspect", image])
    if result.returncode == 0:
        return

    if not build_on_start:
        raise RuntimeError(
            f"Docker image '{image}' not found. "
            f"Build it with: docker build -t {image} {BENCH_DIR}"
        )

    log(f"Building Docker image '{image}' (first run, this may take a few minutes)...")
    build = _run_docker(["build", "-t", image, str(BENCH_DIR)])
    if build.returncode != 0:
        tail = build.stdout.decode(errors="replace")[-2000:]
        raise RuntimeError(f"Failed to build Docker image:\n{tail}")
    log(f"Docker image '{image}' built successfully.")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def load_config():
    with open(BENCH_DIR / "config.json") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# prompt resolution
# ---------------------------------------------------------------------------

def resolve_prompt(prompt_ref):
    """Resolve a prompt reference to (name, content, eval_script_or_None).

    Supports two formats:
      - Old: "prompts/foo.txt"  → reads the file directly, no eval
      - New: "prompts/foo"      → reads foo/prompt.txt, finds foo/eval.py
    """
    ref = BENCH_DIR / prompt_ref
    if ref.is_file() and ref.suffix == ".txt":
        return ref.stem, ref.read_text(), None
    if ref.is_dir():
        prompt_file = ref / "prompt.txt"
        if not prompt_file.exists():
            raise FileNotFoundError(f"{prompt_file} not found")
        eval_file = ref / "eval.py"
        return ref.name, prompt_file.read_text(), eval_file if eval_file.exists() else None
    raise FileNotFoundError(f"Cannot resolve prompt: {prompt_ref}")


# ---------------------------------------------------------------------------
# NDJSON parsing
# ---------------------------------------------------------------------------

def parse_ndjson(raw_file):
    """Parse Claude stream-json NDJSON. Returns (result_event, tool_count, thinking_peak)."""
    result = {}
    tool_count = 0
    thinking_peak = 0

    if not raw_file.exists():
        return result, tool_count, thinking_peak

    with open(raw_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"^\d+\.\d+\s", "", line)
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue

            t = evt.get("type", "")
            st = evt.get("subtype", "")

            if t == "result":
                result = evt
            elif t == "assistant":
                for block in evt.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        tool_count += 1
            elif t == "system" and st == "thinking_tokens":
                peak = evt.get("estimated_tokens", 0)
                if peak > thinking_peak:
                    thinking_peak = peak

    return result, tool_count, thinking_peak


# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------

def run_eval(eval_script, workdir):
    """Run eval script, return (score, details, summary, error)."""
    try:
        proc = subprocess.run(
            [sys.executable, str(eval_script), str(workdir)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "no output")[:300]
            return None, None, None, f"eval exit {proc.returncode}: {err}"
        data = json.loads(proc.stdout)
        return data.get("score"), data.get("details"), data.get("summary"), None
    except json.JSONDecodeError:
        return None, None, None, f"eval output not valid JSON: {proc.stdout[:200]}"
    except Exception as e:
        return None, None, None, str(e)


# ---------------------------------------------------------------------------
# metrics extraction
# ---------------------------------------------------------------------------

METRIC_KEYS = [
    "wall_duration_ms", "duration_ms", "duration_api_ms", "ttft_ms",
    "time_to_request_ms", "num_turns", "total_cost_usd", "input_tokens",
    "output_tokens", "tool_call_count", "thinking_token_peak",
]
EVAL_KEYS = ["score"]


def extract_metrics(result, wall_duration_ms, tool_count, thinking_peak):
    usage = result.get("usage", {})

    def num(key, default=None):
        v = result.get(key)
        return v if v is not None else default

    return {
        "wall_duration_ms": wall_duration_ms,
        "duration_ms": num("duration_ms"),
        "duration_api_ms": num("duration_api_ms"),
        "ttft_ms": num("ttft_ms"),
        "time_to_request_ms": num("time_to_request_ms"),
        "num_turns": num("num_turns"),
        "total_cost_usd": num("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "tool_call_count": tool_count,
        "thinking_token_peak": thinking_peak,
    }


def aggregate(metrics_files):
    runs = []
    for mf in metrics_files:
        with open(mf) as f:
            runs.append(json.load(f))

    if not runs:
        return {}

    def agg(values):
        if not values:
            return {"min": 0, "max": 0, "avg": 0, "values": []}
        nums = [v for v in values if v is not None]
        if not nums:
            return {"min": 0, "max": 0, "avg": 0, "values": values}
        return {
            "min": min(nums),
            "max": max(nums),
            "avg": sum(nums) / len(nums),
            "values": values,
        }

    metrics_agg = {}
    for k in METRIC_KEYS + EVAL_KEYS:
        metrics_agg[k] = agg([r["metrics"].get(k) for r in runs])

    return {
        "model_config": runs[0]["model_config"],
        "model_name": runs[0]["model_name"],
        "prompt": runs[0]["prompt"],
        "total_runs": len(runs),
        "successful_runs": sum(1 for r in runs if r["status"] == "success"),
        "failed_runs": sum(1 for r in runs if r["status"] != "success"),
        "metrics": metrics_agg,
    }


# ---------------------------------------------------------------------------
# claude invocation
# ---------------------------------------------------------------------------

def _run_claude_direct(prompt_content, settings_path, cwd, timeout_sec, raw_file):
    """Run Claude Code directly on the host (non-Docker path)."""
    cmd = [
        "claude", "-p",
        "--bare",
        "--permission-mode", "acceptEdits",
        "--settings", str(settings_path),
        "--output-format", "stream-json",
        "--verbose",
        prompt_content,
    ]

    start_ms = int(time.time() * 1000)

    try:
        use_ts = shutil.which("ts") is not None
        if use_ts:
            with open(raw_file, "w") as out:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=str(cwd))
                ts_proc = subprocess.Popen(
                    ["ts", "%.s"], stdin=proc.stdout, stdout=out, stderr=subprocess.DEVNULL
                )
                proc.stdout.close()
                try:
                    proc.wait(timeout=timeout_sec)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
                    ts_proc.kill(); ts_proc.wait()
                else:
                    ts_proc.wait()
            exit_code = proc.returncode
        else:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout_sec, cwd=str(cwd))
            with open(raw_file, "wb") as f:
                f.write(proc.stdout)
            exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        exit_code = 124

    end_ms = int(time.time() * 1000)
    return exit_code, end_ms - start_ms


def _run_claude_docker(prompt_content, settings_path, cwd, timeout_sec,
                       raw_file, sandbox_config):
    """Run Claude Code inside a Docker container with bypassed permissions."""
    ok, err = _check_docker_available()
    if not ok:
        raise RuntimeError(f"Docker sandbox enabled but unavailable: {err}")

    image = sandbox_config.get("image", "code-bench-sandbox:latest")
    _ensure_docker_image(image, sandbox_config.get("build_on_start", True))

    docker_mode, docker_prefix = _resolve_docker_prefix()

    container_settings = "/tmp/settings.json"
    container_workspace = "/workspace"

    docker_args = [
        "run", "--rm",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "HOME=/tmp",
        "-v", f"{cwd}:{container_workspace}",
        "-v", f"{settings_path}:{container_settings}:ro",
        "-w", container_workspace,
        image,
        "claude", "-p", "--bare",
        "--dangerously-skip-permissions",
        "--settings", container_settings,
        "--output-format", "stream-json", "--verbose",
        prompt_content,
    ]

    if docker_mode == "shell":
        cmd = docker_prefix + ["docker " + shlex.join(docker_args)]
    else:
        cmd = docker_prefix + docker_args

    start_ms = int(time.time() * 1000)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout_sec)
        with open(raw_file, "wb") as f:
            f.write(proc.stdout)
        exit_code = proc.returncode
        if proc.stderr:
            stderr_text = proc.stderr.decode(errors="replace")[:500]
            if stderr_text.strip():
                log(f"Docker stderr: {stderr_text}")
    except subprocess.TimeoutExpired:
        exit_code = 124

    end_ms = int(time.time() * 1000)
    return exit_code, end_ms - start_ms


def run_claude(prompt_content, settings_path, cwd, timeout_sec, raw_file,
               sandbox_config=None):
    """Run Claude Code. Uses Docker sandbox when configured, otherwise direct."""
    if sandbox_config and sandbox_config.get("enabled"):
        return _run_claude_docker(prompt_content, settings_path, cwd,
                                  timeout_sec, raw_file, sandbox_config)
    else:
        return _run_claude_direct(prompt_content, settings_path, cwd,
                                  timeout_sec, raw_file)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    cfg = load_config()
    runs_n = cfg["runs"]
    timeout_sec = cfg["timeout_seconds"]
    retry = cfg["retry_count"]
    template_dir = cfg.get("template_dir")
    sandbox_config = cfg.get("sandbox", None)
    models = cfg["models"]
    prompt_refs = cfg["prompts"]

    if not models:
        log("ERROR: no models configured in config.json"); sys.exit(1)
    if not prompt_refs:
        log("ERROR: no prompts configured in config.json"); sys.exit(1)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    results_dir = BENCH_DIR / "results" / timestamp
    results_dir.mkdir(parents=True, exist_ok=True)

    sandbox_mode = "Docker sandbox" if (sandbox_config and sandbox_config.get("enabled")) else "direct"
    log(f"Benchmark starting — {len(models)} models x {len(prompt_refs)} prompts x {runs_n} runs ({sandbox_mode})")
    log(f"Results: {results_dir}")

    summary_entries = []

    for model_cfg in models:
        model_name = model_cfg["name"]
        settings_path = write_temp_settings(model_cfg, results_dir)

        for prompt_ref in prompt_refs:
            prompt_name, prompt_content, eval_script = resolve_prompt(prompt_ref)

            run_dir = results_dir / f"{model_name}__{prompt_name}"
            run_dir.mkdir(parents=True, exist_ok=True)

            log(f">>> {model_name} / {prompt_name}")
            if eval_script:
                log(f"    eval: {eval_script.relative_to(BENCH_DIR)}")

            run_metrics_files = []

            for run_i in range(1, runs_n + 1):
                attempt = 0
                success = False
                error_msg = None
                wall_dur = 0
                metrics = {}

                while attempt <= retry and not success:
                    if attempt > 0:
                        log(f"  Retry {attempt}/{retry}")

                    # workspace lives inside the run results dir (persisted)
                    workdir = run_dir / f"run-{run_i}" / "workspace"
                    workdir.mkdir(parents=True, exist_ok=True)

                    if template_dir:
                        tmpl = BENCH_DIR / template_dir
                        if tmpl.is_dir():
                            shutil.copytree(tmpl, workdir, dirs_exist_ok=True)

                    raw_file = run_dir / f"run-{run_i}.ndjson"
                    exit_code, wall_dur = run_claude(
                        prompt_content, settings_path, workdir, timeout_sec, raw_file,
                        sandbox_config=sandbox_config,
                    )

                    result, tool_count, thinking_peak = parse_ndjson(raw_file)

                    if exit_code == 124:
                        status = "timeout"
                        error_msg = f"timed out after {timeout_sec}s"
                    elif exit_code != 0:
                        status = "error"
                        error_msg = f"exit code {exit_code}"
                    elif result.get("is_error"):
                        status = "api_error"
                        error_msg = result.get("api_error_status", "unknown api error")
                    else:
                        status = "success"
                        error_msg = None

                    metrics = extract_metrics(result, wall_dur, tool_count, thinking_peak)
                    result_model_name = None
                    mu = result.get("modelUsage", {})
                    if mu:
                        result_model_name = list(mu.keys())[0]

                    # eval
                    score = None
                    eval_details = None
                    eval_summary = None
                    eval_error = None
                    if status == "success" and eval_script:
                        score, eval_details, eval_summary, eval_error = run_eval(eval_script, workdir)
                        if eval_error:
                            log(f"  Eval error: {eval_error}")

                    metrics["score"] = score
                    metrics["eval_details"] = eval_details
                    metrics["eval_summary"] = eval_summary

                    metrics_record = {
                        "run": run_i,
                        "model_config": model_name,
                        "model_name": result_model_name or model_name,
                        "prompt": prompt_name,
                        "status": status,
                        "error": error_msg,
                        "metrics": metrics,
                    }

                    metrics_file = run_dir / f"run-{run_i}.metrics.json"
                    with open(metrics_file, "w") as f:
                        json.dump(metrics_record, f, indent=2)

                    run_metrics_files.append(metrics_file)

                    if status == "success":
                        success = True
                    else:
                        # clean up failed workspace; keep successful ones
                        shutil.rmtree(workdir, ignore_errors=True)
                        attempt += 1

                dur_str = f"{metrics.get('duration_ms', '-')}ms"
                score_str = f", score={score:.2f}" if score is not None else ""
                if success:
                    log(f"  Run {run_i}: OK — {wall_dur}ms wall, {dur_str} claude{score_str}")
                else:
                    log(f"  Run {run_i}: FAILED after {attempt} attempts — {error_msg}")

            # aggregate across runs
            agg_file = run_dir / "aggregate.json"
            agg = aggregate(run_metrics_files)
            with open(agg_file, "w") as f:
                json.dump(agg, f, indent=2)

            summary_entries.append(agg)

    # global summary
    summary_file = results_dir / "summary.json"
    with open(summary_file, "w") as f:
        json.dump(summary_entries, f, indent=2)
    log(f"Summary: {summary_file}")

    # report
    report_file = results_dir / "report.md"
    subprocess.run([sys.executable, str(BENCH_DIR / "report.py"), str(summary_file), str(report_file)])
    log(f"Report: {report_file}")

    # feishu upload
    report_title = f"Bench Report — {timestamp}"
    lark_cli = shutil.which("lark-cli") or shutil.which("lark-cli", path=os.path.expanduser("~/.npm-global/bin"))
    if not lark_cli:
        for p in ["~/.npm-global/bin", "~/node_modules/.bin"]:
            candidate = Path(os.path.expanduser(p)) / "lark-cli"
            if candidate.exists():
                lark_cli = str(candidate)
                break

    if lark_cli:
        log("Uploading to Feishu...")
        try:
            content = report_file.read_text()
            folder_token = cfg.get("feishu_folder_token", "")
            cmd = [lark_cli, "docs", "+create",
                   "--title", report_title,
                   "--content", "-",
                   "--doc-format", "markdown"]
            if folder_token:
                cmd += ["--parent-token", folder_token]
            subprocess.run(cmd, input=content, text=True, check=True)
        except Exception as e:
            log(f"Feishu upload failed: {e}. Local report: {report_file}")
    else:
        log(f"lark-cli not found, skipping Feishu upload. Report: {report_file}")

    log(f"Done. Results: {results_dir}")


if __name__ == "__main__":
    main()

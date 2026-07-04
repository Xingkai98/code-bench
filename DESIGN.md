# Bench: Multi-Model Benchmarking Framework for Claude Code

## Overview

A lightweight framework to benchmark different LLM models through Claude Code. Sends the same set of prompts to multiple model configurations, collects detailed metrics including optional eval scores, and generates a comparison report uploaded to Feishu.

## Architecture

```
bench/
├── config.example.json           # Committed template (copy to config.json)
├── config.json                   # Runtime config — gitignored (contains API keys)
├── configs/                      # Legacy model configs (still supported, gitignored)
├── prompts/                      # Test prompts: directories with seed files + prompt.txt
│   └── task-name/
│       ├── prompt.txt            # Required: task content
│       ├── eval.py               # Optional: scoring script (HIDDEN from model)
│       ├── test_basic.py         # Optional: self-check test (visible to model)
│       └── ...                   # Other seed files (code, data — all visible)
├── templates/                    # Optional: global files copied into every workspace
├── run.sh                        # Thin launcher → calls run.py
├── run.py                        # Python: scheduler, seed copy, NDJSON parser, eval, aggregation
├── report.py                     # Python: JSON → Markdown report + Feishu upload
└── results/<timestamp>/          # Auto-generated output
    ├── summary.json
    ├── report.md
    └── <model>__<prompt>/
        ├── aggregate.json
        └── run-{n}/
            ├── run-{n}.ndjson
            ├── run-{n}.metrics.json
            └── workspace/        # Persisted model outputs
```

## Execution Flow

1. `run.py` reads `config.json`
2. Creates `results/<timestamp>/` output directory
3. For each model, writes a temporary settings JSON from inline config
4. For each prompt × model combination:
   - Creates workspace in `results/<ts>/<model>__<prompt>/run-{n}/workspace/`
   - Copies `template_dir` content if configured
   - Copies **seed files** from prompt directory: all files EXCEPT `eval.py`
   - Runs serial N times (configurable; default 3)
   - Timeout per run (configurable; default 600s)
   - Retries up to N times on failure (configurable; default 1)
5. Each run: invokes Claude Code in non-interactive mode in the workspace (Docker or host)
6. Parses NDJSON stream → extracts performance metrics
7. If `eval.py` exists: runs it against the workspace → parses score JSON
8. Aggregates all metrics across runs → `summary.json`
9. `report.py` generates `report.md` → uploads to Feishu as a doc
10. Failed workspaces are cleaned up; successful ones are persisted

## Key Decisions

### Invocation method
`claude -p --output-format stream-json --verbose` — allows programmatic parsing of the full execution stream as NDJSON. Each event is a JSON line with types: `system/init`, `system/thinking_tokens`, `assistant`, `result/success`.

### Model config: inline in config.json
Model configurations are defined **inline** in `config.json` under the `models` array. Each entry has `name`, `env`, `model`, and `thinking` fields. At runtime, temporary settings files are written for `--settings`. `config.json` is gitignored; `config.example.json` is committed as a template.

### Seed files (hidden eval pattern)
All files in the prompt directory EXCEPT `eval.py` are copied to the workspace as seed files. The model can read and edit these files freely. `eval.py` is intentionally excluded — the model cannot see the scoring criteria, preventing it from gaming the benchmark by reading the test logic. This mirrors the SWE-bench approach: the model gets a `test_basic.py` for self-verification but never sees the full eval.

### Docker sandbox (optional)
The framework supports running models inside Docker containers for isolation. Configure via `config.json` → `sandbox`:
```json
{
  "sandbox": {
    "enabled": true,
    "image": "code-bench-sandbox:latest",
    "build_on_start": true
  }
}
```
When enabled, the workspace is mounted at `/workspace` and Claude Code runs inside the container.

### Configuration isolation
Each model gets its own temporary settings JSON file loaded via `--settings <file>`. This prevents any config leak between models.

### Single-turn interaction
Allows tool calls but no user interaction. Claude Code is expected to autonomously complete and return. `--bare` ensures no environment contamination (no CLAUDE.md, hooks, auto-memory, etc.).

### Permissions
`--permission-mode acceptEdits` to auto-approve tool operations during benchmark runs. `--bare` limits tools to Bash/Edit/Read which is sufficient for most tasks.

### Workspace isolation
Each run gets a fresh directory inside the results tree (persisted). Seed files from the prompt directory and optional `template_dir` populate the initial workspace.

### Retry logic
On failure or timeout, auto-retries up to configurable N times (default 1). Failed workspaces are cleaned up.

### Eval (scoring)
Each prompt can optionally include an `eval.py` script. After a successful run, the framework calls `python3 eval.py <workspace_path>`. The script must output JSON:

```json
{
  "score": 0.85,
  "details": {"correctness": 1.0, "style": 0.7},
  "summary": "Good code, minor style issues"
}
```

- `score` (required): 0-1 rating for cross-model comparison
- `details` (optional): arbitrary sub-scores
- `summary` (optional): one-line human-readable note

### Prompt directory format

```
prompts/<name>/
  prompt.txt       # Required: task description for the model
  eval.py          # Optional: scoring script (HIDDEN, not copied to workspace)
  test_basic.py    # Optional: self-check test (visible, model can run to verify)
  *.py / *.json    # Seed files — initial code/data the model should work with
  ...              #      (all copied to workspace except eval.py)
```

Backward-compatible: old `prompts/<name>.txt` flat files still work (no eval, no seeds).

### Implementation
- **Python** (`run.py`): Scheduling, seed copy, CLI invocation (host or Docker), NDJSON parsing, eval execution, metrics → JSON, aggregation
- **Python** (`report.py`): JSON → Markdown report + Feishu upload
- **Bash** (`run.sh`): Thin 3-line launcher wrapping `run.py`

## Model Config Schema

Models are defined inline in `config.json`:

```json
{
  "models": [
    {
      "name": "my-model",
      "env": {
        "ANTHROPIC_API_KEY": "sk-xxx",
        "ANTHROPIC_BASE_URL": "https://api.provider.com"
      },
      "model": "model-id",
      "thinking": { "type": "enabled", "budget_tokens": 16000 }
    }
  ]
}
```

- `name`: display name used in reports (no spaces)
- `env`: environment variables passed to Claude Code (API key, base URL)
- `model`: model identifier for the API
- `thinking`: thinking/budget config

The legacy `configs/*.json` file-per-model format is still supported for backward compatibility.

## Metrics Collected

From `result/success` event:
- `duration_ms` — total Claude-reported time
- `duration_api_ms` — API time
- `ttft_ms` — time to first token
- `time_to_request_ms` — request preparation time
- `num_turns` — number of conversation turns
- `total_cost_usd` — cost in USD
- `usage.input_tokens` — input token count
- `usage.output_tokens` — output token count

Parsed from the stream:
- Total tool call count (count `tool_use` content blocks)
- Thinking token peak (last `thinking_tokens` event)

From eval (per prompt):
- `score` — overall quality score (0-1)
- `eval_details` — sub-scores
- `eval_summary` — human-readable note

## Report Structure

1. Summary comparison table (all models × all prompts, key metrics including score)
2. Per-prompt detailed sections (cross-model comparison)
3. Per-model per-run breakdown (individual run data, eval summaries, raw data links)

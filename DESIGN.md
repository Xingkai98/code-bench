# Bench: Multi-Model Benchmarking Framework for Claude Code

## Overview

A lightweight framework to benchmark different LLM models through Claude Code. Sends the same set of prompts to multiple model configurations, collects detailed metrics including optional eval scores, and generates a comparison report uploaded to Feishu.

## Architecture

```
bench/
├── config.json                  # Global runtime config (runs, timeout, etc.)
├── configs/                     # Per-model config: env vars, model name, thinking params
├── prompts/                     # Test prompts: directories with prompt.txt + optional eval.py
│   └── task-name/
│       ├── prompt.txt           # Required: task content
│       └── eval.py              # Optional: scoring script, receives workspace path
├── templates/                   # Optional: initial workspace files copied per run
├── run.sh                       # Thin launcher → calls run.py
├── run.py                       # Python: scheduler, NDJSON parser, metric extraction, eval, aggregation
├── report.py                    # Python: JSON → Markdown report + Feishu upload
└── results/<timestamp>/         # Auto-generated output
    ├── summary.json
    ├── report.md
    └── <model>__<prompt>/
        ├── aggregate.json
        └── run-{n}/
            ├── run-{n}.ndjson
            ├── run-{n}.metrics.json
            └── workspace/       # Persisted model outputs
```

## Execution Flow

1. `run.py` reads `config.json`
2. Creates `results/<timestamp>/` output directory
3. For each prompt × model configuration:
   - Creates workspace in `results/<ts>/<model>__<prompt>/run-{n}/workspace/`
   - Copies `template_dir` content if configured
   - Runs serial N times (configurable; default 3)
   - Timeout per run (configurable; default 600s)
   - Retries up to N times on failure (configurable; default 1)
4. Each run: invokes Claude Code in non-interactive mode in the workspace
5. Parses NDJSON stream → extracts performance metrics
6. If `eval.py` exists: runs it against the workspace → parses score JSON
7. Aggregates all metrics across runs → `summary.json`
8. `report.py` generates `report.md` → uploads to Feishu as a doc
9. Failed workspaces are cleaned up; successful ones are persisted

## Key Decisions

### Invocation method
`claude -p --output-format stream-json --verbose` — allows programmatic parsing of the full execution stream as NDJSON. Each event is a JSON line with types: `system/init`, `system/thinking_tokens`, `assistant`, `result/success`.

### Configuration isolation
Each model gets its own settings JSON file loaded via `--settings <file>`. This prevents any config leak into the user's interactive session.

### Single-turn interaction
Allows tool calls but no user interaction. Claude Code is expected to autonomously complete and return. `--bare` ensures no environment contamination (no CLAUDE.md, hooks, auto-memory, etc.).

### Permissions
`--permission-mode acceptEdits` to auto-approve tool operations during benchmark runs. `--bare` limits tools to Bash/Edit/Read which is sufficient for most tasks.

### Workspace isolation
Each run gets a fresh directory inside the results tree (persisted). Optional `template_dir` can seed initial files. No Docker at this stage.

### Retry logic
On failure or timeout, auto-retries up to configurable N times (default 1). Failed workspaces are cleaned up.

### Eval (scoring)
Each prompt can optionally include an `eval.py` script. After a successful run, the framework calls `python3 eval.py <workspace_path>`. The script must output a JSON score:

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
  prompt.txt    # Required: task content
  eval.py       # Optional: scoring script
```

Backward-compatible: old `prompts/<name>.txt` flat files still work (no eval).

### Implementation
- **Python** (`run.py`): Scheduling, CLI invocation, NDJSON parsing, eval execution, metrics → JSON, aggregation
- **Python** (`report.py`): JSON → Markdown report + Feishu upload
- **Bash** (`run.sh`): Thin 3-line launcher wrapping `run.py`

## Model Config Schema

```json
{
  "env": {
    "ANTHROPIC_API_KEY": "sk-xxx",
    "ANTHROPIC_BASE_URL": "https://api.provider.com"
  },
  "model": "model-id",
  "thinking": { "type": "enabled", "budget_tokens": 16000 }
}
```

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

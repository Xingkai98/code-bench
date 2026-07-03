# Bench: Multi-Model Benchmarking Framework for Claude Code

## Overview

A lightweight framework to benchmark different LLM models through Claude Code. Sends the same set of prompts to multiple model configurations, collects detailed metrics, and generates a comparison report uploaded to Feishu.

## Architecture

```
bench/
├── config.json                  # Global runtime config (runs, timeout, etc.)
├── configs/                     # Per-model config: env vars, model name, thinking params
├── prompts/                     # Test prompts (*.txt)
├── templates/                   # Optional: initial workspace files copied per run
├── run.sh                       # Entry point: scheduler + metric extraction
├── report.py                    # JSON → Markdown report + Feishu upload
└── results/<timestamp>/         # Auto-generated output
```

## Execution Flow

1. `run.sh` reads `config.json`
2. Creates `results/<timestamp>/` output directory
3. For each prompt × model configuration:
   - Creates a temp workspace (`mktemp -d`)
   - Copies `template_dir` content if configured
   - Runs serial N times (configurable; default 3)
   - Fails on timeout (configurable; default 600s)
   - Retries up to N times on failure (configurable; default 1)
4. Each run: invokes Claude Code in non-interactive mode, captures structured output
5. Parses NDJSON stream → extracts metrics
6. Aggregates all metrics → `summary.json`
7. `report.py` generates `report.md` → uploads to Feishu as a doc
8. Cleans up temp workspaces

## Key Decisions

### Invocation method
`claude -p --output-format stream-json --verbose` — allows programmatic parsing of the full execution stream as NDJSON. Each event is a JSON line with types: `system/init`, `system/thinking_tokens`, `assistant`, `result/success`.

### Configuration isolation
Each model gets its own settings JSON file loaded via `--settings <file>`. This prevents any config leak into the user's interactive session.

### Single-turn interaction
Allows tool calls but no user interaction. Claude Code is expected to autonomously complete and return. `--bare` ensures no environment contamination (no CLAUDE.md, hooks, auto-memory, etc.).

### Permissions
`--allow-dangerously-skip-permissions` to bypass interactive prompts during benchmark runs.

### Workspace isolation
Each run gets a fresh temp directory (`mktemp -d`). Optional `template_dir` can seed initial files. No Docker at this stage.

### Retry logic
On failure or timeout, auto-retries up to configurable N times (default 1). Failed runs are marked in the report.

### Language split
- **Bash** (`run.sh`): Scheduling, CLI invocation, NDJSON parsing → metrics JSON
- **Python** (`report.py`): JSON aggregation → Markdown report generation + Feishu doc upload

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
- `duration_ms` — total wall-clock time
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
- Per-turn timing (gaps between `assistant` events)

## Report Structure

1. Summary comparison table (all models × all prompts, key metrics)
2. Per-prompt detailed sections (cross-model comparison)
3. Per-model per-run breakdown (individual run data, links to raw JSON)

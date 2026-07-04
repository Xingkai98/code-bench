# Bench — Model Benchmarking Framework for Claude Code

Framework to benchmark different LLM models via Claude Code non-interactive mode. Reads prompts, runs them against multiple model configurations, collects detailed metrics, generates comparison reports, and uploads to Feishu.

## Design

See [DESIGN.md](DESIGN.md) for the full architecture and decision record.

## How to Run

1. Copy `config.example.json` → `config.json` and fill in your model API keys
2. Add prompts: `prompts/<name>/prompt.txt` (see Prompt Format below)
3. Run: `./run.sh`

## Key Files

| Path | Role |
|------|------|
| `config.example.json` | Committed template — copy to `config.json` |
| `config.json` | Runtime config (gitignored, contains API keys). Models are inline objects |
| `prompts/<name>/` | Prompt directory: `prompt.txt`, optional `eval.py`, seed files |
| `templates/` | Optional: global files copied into every run's workspace |
| `run.sh` | Thin launcher → calls `run.py` |
| `run.py` | Python: scheduler, NDJSON parser, metric extraction, aggregation |
| `report.py` | Python: JSON → Markdown report + Feishu upload |
| `results/<timestamp>/` | All output (raw streams, metrics, report) |

## Prompt Format

Each prompt lives in `prompts/<name>/`:

```
prompts/order-race/
├── prompt.txt       # Task description (required)
├── eval.py          # Scoring script (optional, HIDDEN from model)
├── test_basic.py    # Self-check test (optional, visible to model)
├── inventory.py     # Seed files — initial code the model should edit
└── orders.py        # (can be .py, .json, .txt, anything)
```

**Seed files**: All files in the prompt directory EXCEPT `eval.py` are copied to
the workspace before each run. The model sees and can edit these files.

**Hidden eval**: `eval.py` is intentionally NOT copied to the workspace. The model
cannot see the scoring criteria, preventing it from gaming the benchmark by
reading the test logic. Models can only self-verify via a provided `test_basic.py`.

## Constraints

- Always use `--bare` for clean benchmarking
- `config.json` is gitignored — never commit it (contains API keys)
- `configs/*.json` is also gitignored (legacy format, still supported)
- All runs are serial — no concurrency between models
- Docker sandbox is enabled by default (`config.json` → `sandbox.enabled`)

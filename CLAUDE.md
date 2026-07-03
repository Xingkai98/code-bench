# Bench — Model Benchmarking Framework for Claude Code

Framework to benchmark different LLM models via Claude Code non-interactive mode. Reads prompts, runs them against multiple model configurations, collects detailed metrics, generates comparison reports, and uploads to Feishu.

## Design

See [DESIGN.md](DESIGN.md) for the full architecture and decision record.

## How to Run

1. Add model configs: `configs/<name>.json`
2. Add prompts: `prompts/<name>.txt`
3. Edit `config.json` to list models and prompts
4. Run: `./run.sh`

## Key Files

| Path | Role |
|------|------|
| `config.json` | Global runtime config |
| `configs/*.json` | Per-model settings (API key, base URL, thinking) |
| `prompts/*.txt` | Test prompts |
| `templates/` | Optional: files copied into each run's workspace |
| `run.sh` | Thin launcher → calls `run.py` |
| `run.py` | Python: scheduler, NDJSON parser, metric extraction, aggregation |
| `report.py` | Python: JSON → Markdown report + Feishu upload |
| `results/<timestamp>/` | All output (raw streams, metrics, report) |

## Constraints

- Always use `--bare` for clean benchmarking
- Never commit `configs/*.json` (contains API keys)
- All runs are serial — no concurrency between models

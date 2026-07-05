#!/usr/bin/env bash
# ============================================================================
# tui_collect.sh — Collect results from parallel TUI sessions.
#
# Usage:
#   ./tui_collect.sh <run_dir>
#
# Extracts metrics from each model's session JSONL, runs eval.py,
# and prints a comparison table.
# ============================================================================
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
log() { echo -e "  $*"; }
banner() { echo -e "\n${CYAN}══════════════════════════════════════════════════${NC}"; echo -e "${CYAN}  $*${NC}"; echo -e "${CYAN}══════════════════════════════════════════════════${NC}"; }

RUN_DIR="${1:-}"
if [ -z "$RUN_DIR" ]; then
    echo "Usage: $0 <run_dir>"
    echo "  e.g.: $0 results/2026-07-05_120000_tui_parallel"
    exit 1
fi

if [ ! -d "$RUN_DIR" ]; then
    echo "ERROR: run dir not found: $RUN_DIR"
    exit 1
fi

PANES_DIR="$RUN_DIR/.panes"
if [ ! -d "$PANES_DIR" ]; then
    echo "ERROR: no .panes dir — not a parallel run directory?"
    exit 1
fi

PROMPT_NAME=$(python3 -c "
import os,glob
# Find the prompt from any model's metrics later, or from dir name
print('tiny-c-modernize')  # default
" 2>/dev/null || echo "tiny-c-modernize")

PROMPT_DIR="$BENCH_DIR/prompts/$PROMPT_NAME"
EVAL_SCRIPT="$PROMPT_DIR/eval.py"

banner "Collecting Results — $(basename $RUN_DIR)"

RESULTS=()
MODELS=$(ls "$PANES_DIR")

for MODEL_NAME in $MODELS; do
    WS="$PANES_DIR/$MODEL_NAME"
    echo ""
    log "Processing $MODEL_NAME..."

    # Find session JSONL
    SESSIONS_DIR="$WS/sessions"
    SESSION_FILE=""
    if [ -d "$SESSIONS_DIR" ]; then
        SESSION_FILE=$(find "$SESSIONS_DIR" -name "*.jsonl" -type f 2>/dev/null | sort | tail -1)
    fi

    if [ -z "$SESSION_FILE" ]; then
        echo -e "    ${RED}No session log found${NC}"
        RESULTS+=("$MODEL_NAME|error|0|0|0|0|0|No session")
        continue
    fi

    # Extract metrics
    METRICS=$(python3 "$BENCH_DIR/extract_tui_metrics.py" "$SESSION_FILE" --model "$MODEL_NAME" 2>/dev/null) || {
        echo -e "    ${RED}Metrics extraction failed${NC}"
        RESULTS+=("$MODEL_NAME|error|0|0|0|0|0|Extract failed")
        continue
    }

    TURNS=$(echo "$METRICS" | python3 -c "import sys,json; print(json.load(sys.stdin)['num_turns'])")
    TOK_IN=$(echo "$METRICS" | python3 -c "import sys,json; print(json.load(sys.stdin)['input_tokens'])")
    TOK_OUT=$(echo "$METRICS" | python3 -c "import sys,json; print(json.load(sys.stdin)['output_tokens'])")
    TOOLS=$(echo "$METRICS" | python3 -c "import sys,json; print(json.load(sys.stdin)['tool_call_count'])")
    DUR=$(echo "$METRICS" | python3 -c "import sys,json; d=json.load(sys.stdin).get('duration_ms'); print(f'{d/1000:.0f}s' if d else '-')")

    # Run eval
    SCORE="N/A"
    EVAL_SUM=""
    if [ -f "$EVAL_SCRIPT" ] && [ -d "$WS/workspace" ]; then
        EVAL_OUT=$(python3 "$EVAL_SCRIPT" "$WS/workspace" 2>&1) || true
        SCORE=$(echo "$EVAL_OUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('score'))" 2>/dev/null || echo "N/A")
        EVAL_SUM=$(echo "$EVAL_OUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('summary',''))" 2>/dev/null || echo "")
    fi

    echo -e "    ${GREEN}Turns: $TURNS  Tokens: $TOK_IN/$TOK_OUT  Tools: $TOOLS  Time: $DUR  Score: $SCORE${NC}"
    [ -n "$EVAL_SUM" ] && echo -e "    ${CYAN}$EVAL_SUM${NC}"

    RESULTS+=("$MODEL_NAME|success|$TURNS|$TOK_IN|$TOK_OUT|$TOOLS|$DUR|$SCORE|$EVAL_SUM")

    # Save individual result
    MODEL_RESULT_DIR="$RUN_DIR/${MODEL_NAME}__${PROMPT_NAME}"
    mkdir -p "$MODEL_RESULT_DIR"
    cp "$SESSION_FILE" "$MODEL_RESULT_DIR/session.jsonl" 2>/dev/null || true

    # Use temp files to avoid shell escaping issues
    echo "$METRICS" > "$MODEL_RESULT_DIR/.metrics_raw.json"
    echo "$SCORE" > "$MODEL_RESULT_DIR/.score.txt"
    echo "$EVAL_SUM" > "$MODEL_RESULT_DIR/.eval_summary.txt"

    SAVE_SCRIPT=$(mktemp)
    cat > "$SAVE_SCRIPT" << 'PYEOF'
import json, os, sys
rd = sys.argv[1]
mn = sys.argv[2]
pn = sys.argv[3]
m = json.load(open(os.path.join(rd, '.metrics_raw.json')))
sc = open(os.path.join(rd, '.score.txt')).read().strip()
es = open(os.path.join(rd, '.eval_summary.txt')).read().strip()
score = float(sc) if sc and sc != 'N/A' and sc != 'null' else None
m['score'] = score
m['eval_summary'] = es if es else None
r = {
    'run': 1, 'model_config': mn,
    'model_name': m.get('model_name', mn),
    'prompt': pn,
    'status': 'success' if score is not None else 'completed',
    'error': None, 'metrics': m,
}
json.dump(r, open(os.path.join(rd, 'metrics.json'), 'w'), indent=2)
for f in ['.metrics_raw.json', '.score.txt', '.eval_summary.txt']:
    os.remove(os.path.join(rd, f))
PYEOF
    python3 "$SAVE_SCRIPT" "$MODEL_RESULT_DIR" "$MODEL_NAME" "$PROMPT_NAME"
    rm -f "$SAVE_SCRIPT"
done

# ---------------------------------------------------------------------------
# comparison table
# ---------------------------------------------------------------------------
banner "Comparison"

printf "  %-20s %6s %8s %8s %6s %8s %8s\n" "Model" "Turns" "TokIn" "TokOut" "Tools" "Time" "Score"
printf "  %-20s %6s %8s %8s %6s %8s %8s\n" "────" "─────" "──────" "──────" "─────" "──────" "─────"

for row in "${RESULTS[@]}"; do
    IFS='|' read -r name status turns t_in t_out tools dur score extra <<< "$row"
    if [ "$status" = "error" ]; then
        printf "  ${RED}%-20s %6s${NC}\n" "$name" "FAILED"
    else
        if [ "$score" != "N/A" ] && [ "$score" != "null" ] && [ -n "$score" ]; then
            SCORE_FMT=$(printf "%.2f" "$score" 2>/dev/null || echo "$score")
        else
            SCORE_FMT="-"
        fi
        printf "  %-20s %6s %8s %8s %6s %8s ${GREEN}%8s${NC}\n" \
            "$name" "$turns" "$t_in" "$t_out" "$tools" "$dur" "$SCORE_FMT"
    fi
done

echo ""
echo -e "  Results dir: ${CYAN}$RUN_DIR${NC}"

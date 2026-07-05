#!/usr/bin/env bash
set -euo pipefail
BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'

log() { echo -e "  $*"; }
banner() { echo -e "\n${CYAN}============================================================${NC}"; echo -e "${CYAN}  $*${NC}"; echo -e "${CYAN}============================================================${NC}"; }

# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
CONFIG="$BENCH_DIR/config.json"
if [ ! -f "$CONFIG" ]; then
    echo "ERROR: config.json not found at $CONFIG"
    exit 1
fi

IMAGE="${CODE_BENCH_IMAGE:-code-bench-sandbox:latest}"
PROMPT="${1:-prompts/tiny-c-modernize}"

# ---------------------------------------------------------------------------
# docker resolution (same logic as run.py)
# ---------------------------------------------------------------------------
DOCKER_MODE="direct"
if docker ps >/dev/null 2>&1; then
    DOCKER_MODE="direct"
elif sg docker -c "docker ps" >/dev/null 2>&1; then
    DOCKER_MODE="sg"
else
    echo "Cannot access Docker. Try: newgrp docker"
    exit 1
fi

# Wrapper that handles both direct and sg modes
drun() {
    if [ "$DOCKER_MODE" = "sg" ]; then
        sg docker -c "docker $*"
    else
        docker "$@"
    fi
}

# ---------------------------------------------------------------------------
# model selection
# ---------------------------------------------------------------------------
MODELS_JSON=$(python3 -c "
import json
cfg=json.load(open('$CONFIG'))
for i,m in enumerate(cfg['models']):
    print(f'{i+1}|{m[\"name\"]}|{m.get(\"model\",m[\"name\"])}|{m.get(\"env\",{}).get(\"ANTHROPIC_BASE_URL\",\"default\")}')
")

echo -e "\n${CYAN}Available models:${NC}"
echo "$MODELS_JSON" | while IFS='|' read idx name model url; do
    printf "  ${GREEN}[%s]${NC} %-20s → %-30s (%s)\n" "$idx" "$name" "$model" "$url"
done
echo "  [q] quit"

while true; do
    read -r -p $'\nPick a model → ' choice
    [ "$choice" = "q" ] && exit 0
    SELECTED=$(echo "$MODELS_JSON" | grep "^${choice}|" || true)
    if [ -n "$SELECTED" ]; then
        MODEL_IDX=$(echo "$SELECTED" | cut -d'|' -f1)
        MODEL_NAME=$(echo "$SELECTED" | cut -d'|' -f2)
        break
    fi
    echo "  Enter 1-$(echo "$MODELS_JSON" | wc -l) or q"
done

# Extract model config as JSON
MODEL_CFG=$(python3 -c "
import json
cfg=json.load(open('$CONFIG'))
m=cfg['models'][$MODEL_IDX - 1]
print(json.dumps({k:v for k,v in m.items() if k!='name'}))
")

# ---------------------------------------------------------------------------
# workspace setup
# ---------------------------------------------------------------------------
PROMPT_DIR="$BENCH_DIR/$PROMPT"
if [ ! -d "$PROMPT_DIR" ]; then
    echo "ERROR: prompt directory not found: $PROMPT_DIR"
    exit 1
fi

WORKSPACE=$(mktemp -d -p "$BENCH_DIR" tui-docker-workspace-XXXXXX)
log "Workspace: $WORKSPACE"
mkdir -p "$WORKSPACE/workspace"

# Copy seed files (everything except eval.py)
for src in "$PROMPT_DIR"/*; do
    base=$(basename "$src")
    [ "$base" = "eval.py" ] && continue
    cp -r "$src" "$WORKSPACE/workspace/$base"
done
# Copy prompt.txt to workspace root for Claude Code context
cp "$PROMPT_DIR/prompt.txt" "$WORKSPACE/workspace/"

# Write settings file (mounted read-only into container)
SETTINGS_FILE="$WORKSPACE/settings.json"
echo "$MODEL_CFG" > "$SETTINGS_FILE"

# Session persistence directory (mounted at /tmp/.claude in container)
SESSIONS_DIR="$WORKSPACE/.claude-sessions"
mkdir -p "$SESSIONS_DIR"

# Marker file for finding the new session after TUI ends
MARKER=$(mktemp -p "$BENCH_DIR" tui-marker-XXXXXX)

# ---------------------------------------------------------------------------
# ensure image (use task-specific image if available, else base)
# ---------------------------------------------------------------------------
TASK_IMAGE="code-bench-$(basename $PROMPT):latest"
if [ -f "$PROMPT_DIR/Dockerfile" ]; then
    if ! drun image inspect "$TASK_IMAGE" >/dev/null 2>&1; then
        log "Building task image '$TASK_IMAGE'..."
        drun build -t "$TASK_IMAGE" "$PROMPT_DIR"
    fi
    IMAGE="$TASK_IMAGE"
else
    if ! drun image inspect "$IMAGE" >/dev/null 2>&1; then
        log "Building base image '$IMAGE'..."
        drun build -t "$IMAGE" "$BENCH_DIR"
    fi
fi

# ---------------------------------------------------------------------------
# launch
# ---------------------------------------------------------------------------
CONTAINER_WORKSPACE="/workspace"
CONTAINER_SETTINGS="/tmp/settings.json"
CONTAINER_CLAUDE="/tmp/.claude"

banner "TUI Benchmark — $MODEL_NAME / $(basename $PROMPT)"
echo ""
echo -e "  Model:     ${GREEN}$MODEL_NAME${NC}"
echo -e "  Prompt:    $(basename $PROMPT)"
echo -e "  Workspace: $WORKSPACE"
echo ""
echo -e "  ${CYAN}┌─────────────────────────────────────────────────────┐${NC}"
echo -e "  ${CYAN}│${NC} Complete the task in the TUI, then type ${GREEN}/exit${NC}      ${CYAN}│${NC}"
echo -e "  ${CYAN}│${NC} Metrics will be extracted automatically on exit.   ${CYAN}│${NC}"
echo -e "  ${CYAN}└─────────────────────────────────────────────────────┘${NC}"
echo ""
read -r -p "  Press Enter to launch Docker TUI..."

# Record start time
START_MS=$(python3 -c "import time; print(int(time.time()*1000))")

# --- Run Docker container interactively ---
set +e
drun run --rm -it \
    --user "$(id -u):$(id -g)" \
    -e "HOME=/tmp" \
    -v "$WORKSPACE/workspace:$CONTAINER_WORKSPACE" \
    -v "$SETTINGS_FILE:$CONTAINER_SETTINGS:ro" \
    -v "$SESSIONS_DIR:$CONTAINER_CLAUDE" \
    -w "$CONTAINER_WORKSPACE" \
    "$IMAGE" \
    claude --settings "$CONTAINER_SETTINGS" --verbose
EXIT_CODE=$?
set -e

WALL_DURATION_MS=$(( $(python3 -c "import time; print(int(time.time()*1000))") - START_MS ))

# ---------------------------------------------------------------------------
# extract metrics
# ---------------------------------------------------------------------------
banner "Session Ended — Extracting Metrics"

# Find the newest session JSONL created during this run
touch "$MARKER"
SESSION_FILE=$(find "$SESSIONS_DIR" -name "*.jsonl" -newer "$MARKER" -print 2>/dev/null | sort | tail -1)
rm -f "$MARKER"

if [ -z "$SESSION_FILE" ]; then
    echo -e "  ${RED}⚠ Could not find session JSONL.${NC}"
    echo "  Sessions dir: $SESSIONS_DIR"
    echo "  Contents:"
    find "$SESSIONS_DIR" -name "*.jsonl" -ls 2>/dev/null || echo "    (empty)"
    echo ""
    echo "  Workspace preserved at: $WORKSPACE"
    exit 1
fi

log "Session log: $SESSION_FILE"

# Extract metrics
METRICS=$(python3 "$BENCH_DIR/extract_tui_metrics.py" "$SESSION_FILE" --model "$MODEL_NAME" 2>&1)
echo "$METRICS" | python3 -c "
import sys,json
m=json.load(sys.stdin)
print(f'  Turns:      {m[\"num_turns\"]}')
print(f'  Tokens:     {m[\"input_tokens\"]:,} in / {m[\"output_tokens\"]:,} out')
print(f'  Tools:      {m[\"tool_call_count\"]} calls')
d=m.get('duration_ms')
if d: print(f'  Duration:   {d/1000:.0f}s')
"

# Merge wall duration into metrics JSON
METRICS=$(echo "$METRICS" | python3 -c "
import sys,json
m=json.load(sys.stdin)
m['wall_duration_ms']=$WALL_DURATION_MS
print(json.dumps(m))
")

# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------
EVAL_SCRIPT="$PROMPT_DIR/eval.py"
SCORE="null"
EVAL_SUMMARY=""
if [ -f "$EVAL_SCRIPT" ]; then
    EVAL_OUT=$(python3 "$EVAL_SCRIPT" "$WORKSPACE/workspace" 2>&1) || true
    SCORE=$(echo "$EVAL_OUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('score'))" 2>/dev/null || echo "null")
    EVAL_SUMMARY=$(echo "$EVAL_OUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('summary',''))" 2>/dev/null || echo "")
    echo -e "  Score:      ${GREEN}${SCORE}${NC}"
    [ -n "$EVAL_SUMMARY" ] && log "$EVAL_SUMMARY"
else
    log "No eval.py — skipping scoring"
fi

# ---------------------------------------------------------------------------
# save results
# ---------------------------------------------------------------------------
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
RESULTS_DIR="$BENCH_DIR/results/${TIMESTAMP}_tui"
RUN_DIR="$RESULTS_DIR/${MODEL_NAME}__$(basename $PROMPT)"
mkdir -p "$RUN_DIR"

# Copy session log
cp "$SESSION_FILE" "$RUN_DIR/session.jsonl"

# Write metrics record — use temp files for safe data transfer
METRICS_FILE="$RUN_DIR/metrics.json"
# Save raw metrics
echo "$METRICS" > "$RUN_DIR/.metrics_raw.json"
# Save eval summary (may contain special chars) to file
echo "$EVAL_SUMMARY" > "$RUN_DIR/.eval_summary.txt"
# Save score to file
echo "$SCORE" > "$RUN_DIR/.score.txt"

# Write a temp Python script (avoids heredoc escaping issues)
SAVE_SCRIPT=$(mktemp)
cat > "$SAVE_SCRIPT" << 'PYEOF'
import json, os, sys
run_dir = sys.argv[1]
model_name = sys.argv[2]
prompt_name = sys.argv[3]
metrics_raw = json.load(open(os.path.join(run_dir, '.metrics_raw.json')))
score_str = open(os.path.join(run_dir, '.score.txt')).read().strip()
eval_summary = open(os.path.join(run_dir, '.eval_summary.txt')).read().strip()
score = float(score_str) if score_str and score_str != 'null' else None
metrics_raw['score'] = score
metrics_raw['eval_summary'] = eval_summary if eval_summary else None
record = {
    'run': 1,
    'model_config': model_name,
    'model_name': metrics_raw.get('model_name', model_name),
    'prompt': prompt_name,
    'status': 'success' if score is not None else 'completed',
    'error': None,
    'metrics': metrics_raw,
}
json.dump(record, open(os.path.join(run_dir, 'metrics.json'), 'w'), indent=2)
os.remove(os.path.join(run_dir, '.metrics_raw.json'))
os.remove(os.path.join(run_dir, '.eval_summary.txt'))
os.remove(os.path.join(run_dir, '.score.txt'))
PYEOF
python3 "$SAVE_SCRIPT" "$RUN_DIR" "$MODEL_NAME" "$(basename $PROMPT)"
rm -f "$SAVE_SCRIPT"

# Clean up temp settings
rm -f "$SETTINGS_FILE"

# ---------------------------------------------------------------------------
# done
# ---------------------------------------------------------------------------
banner "Done"
echo -e "  Results:   $RESULTS_DIR"
echo -e "  Metrics:   $METRICS_FILE"
echo -e "  Workspace: $WORKSPACE"
echo ""
echo -e "  ${CYAN}┌────────────────────────────────────────────┐${NC}"
printf  "  ${CYAN}│${NC} %-10s ${GREEN}%-31s${NC} ${CYAN}│${NC}\n" "Model:" "$MODEL_NAME"
printf  "  ${CYAN}│${NC} %-10s ${GREEN}%-31s${NC} ${CYAN}│${NC}\n" "Score:" "$SCORE"
echo -e "  ${CYAN}└────────────────────────────────────────────┘${NC}"

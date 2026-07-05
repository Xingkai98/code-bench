#!/usr/bin/env bash
# ============================================================================
# tui_parallel.sh — Launch parallel TUI benchmark sessions in tmux panes.
#
# Usage:
#   ./tui_parallel.sh [prompt_dir]
#
# Creates one tmux pane per model from config.json. Each pane runs an
# independent Docker container with Claude Code TUI. You interact with
# each model in its own pane (Ctrl+b arrows to switch).
#
# After all sessions exit, run:
#   ./tui_collect.sh <run_dir>
# ============================================================================
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$BENCH_DIR/config.json"
PROMPT="${1:-prompts/tiny-c-modernize}"
PROMPT_DIR="$BENCH_DIR/$PROMPT"
PROMPT_NAME=$(basename "$PROMPT")

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log() { echo -e "  $*"; }

# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------
if [ ! -f "$CONFIG" ]; then
    echo "ERROR: config.json not found"
    exit 1
fi
if [ ! -d "$PROMPT_DIR" ]; then
    echo "ERROR: prompt dir not found: $PROMPT_DIR"
    exit 1
fi

# Docker resolution
DOCKER_MODE="direct"
if docker ps >/dev/null 2>&1; then
    DOCKER_MODE="direct"
elif sg docker -c "docker ps" >/dev/null 2>&1; then
    DOCKER_MODE="sg"
else
    echo "ERROR: Cannot access Docker"
    exit 1
fi

drun() {
    if [ "$DOCKER_MODE" = "sg" ]; then
        sg docker -c "docker $*"
    else
        docker "$@"
    fi
}

# Check tmux
if ! command -v tmux &>/dev/null; then
    echo "ERROR: tmux not installed. Run: sudo apt-get install tmux"
    exit 1
fi

# ---------------------------------------------------------------------------
# setup: one workspace per model
# ---------------------------------------------------------------------------
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
RUN_DIR="$BENCH_DIR/results/${TIMESTAMP}_tui_parallel"
mkdir -p "$RUN_DIR"

MODELS=$(python3 -c "
import json
cfg=json.load(open('$CONFIG'))
for m in cfg['models']:
    print(m['name'])
")

MODEL_COUNT=$(echo "$MODELS" | wc -l)

# Ensure task image exists
TASK_IMAGE="code-bench-${PROMPT_NAME}:latest"
BASE_IMAGE="code-bench-sandbox:latest"
if [ -f "$PROMPT_DIR/Dockerfile" ]; then
    drun image inspect "$TASK_IMAGE" >/dev/null 2>&1 || drun build -t "$TASK_IMAGE" "$PROMPT_DIR"
    IMAGE="$TASK_IMAGE"
else
    drun image inspect "$BASE_IMAGE" >/dev/null 2>&1 || drun build -t "$BASE_IMAGE" "$BENCH_DIR"
    IMAGE="$BASE_IMAGE"
fi

log "Image: $IMAGE"
log "Models: $(echo $MODELS | tr '\n' ' ')"

# ---------------------------------------------------------------------------
# prepare each model's workspace
# ---------------------------------------------------------------------------
declare -A MODEL_CONTAINERS
PANES_DIR="$RUN_DIR/.panes"
mkdir -p "$PANES_DIR"

i=0
for MODEL_NAME in $MODELS; do
    WS="$PANES_DIR/$MODEL_NAME"
    mkdir -p "$WS/workspace" "$WS/sessions"

    # Copy seed files
    for src in "$PROMPT_DIR"/*; do
        base=$(basename "$src")
        [ "$base" = "eval.py" ] && continue
        cp -r "$src" "$WS/workspace/$base"
    done
    cp "$PROMPT_DIR/prompt.txt" "$WS/workspace/"

    # Write settings for this model
    python3 -c "
import json
cfg=json.load(open('$CONFIG'))
for m in cfg['models']:
    if m['name']=='$MODEL_NAME':
        s={k:v for k,v in m.items() if k!='name'}
        json.dump(s,open('$WS/settings.json','w'))
        break
"

    # Write pane init script
    cat > "$WS/init.sh" << EOF
#!/bin/bash
echo ""
echo "═══════════════════════════════════════════════════"
echo "  Model : ${MODEL_NAME}"
echo "  Prompt: ${PROMPT_NAME}"
echo "  Type /exit when done."
echo "═══════════════════════════════════════════════════"
echo ""
drun run --rm -it \\
    --name "tui-${MODEL_NAME}" \\
    --user "\$(id -u):\$(id -g)" \\
    -e "HOME=/tmp" \\
    -v "${WS}/workspace:/workspace" \\
    -v "${WS}/settings.json:/tmp/settings.json:ro" \\
    -v "${WS}/sessions:/tmp/.claude" \\
    -w /workspace \\
    "${IMAGE}" \\
    claude --settings /tmp/settings.json --verbose
echo "DONE:${MODEL_NAME}" > "${WS}/.done"
EOF
    chmod +x "$WS/init.sh"

    MODEL_CONTAINERS[$MODEL_NAME]="tui-${MODEL_NAME}"
    i=$((i+1))
    log "Ready: $MODEL_NAME → $WS"
done

# ---------------------------------------------------------------------------
# tmux session
# ---------------------------------------------------------------------------
SESSION="tui-bench-$TIMESTAMP"

# Kill existing session with same name if any
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Create session with first model
FIRST_MODEL=$(echo "$MODELS" | head -1)
tmux new-session -d -s "$SESSION" -n "$FIRST_MODEL" \
    "cd ${PANES_DIR}/${FIRST_MODEL} && ./init.sh; exec bash"

# Add remaining models as split panes
REMAINING=$(echo "$MODELS" | tail -n +2)
COUNT=1
for MODEL_NAME in $REMAINING; do
    if [ $COUNT -eq 1 ]; then
        # Split vertically (right)
        tmux split-window -h -t "$SESSION" \
            "cd ${PANES_DIR}/${MODEL_NAME} && ./init.sh; exec bash"
    elif [ $COUNT -eq 2 ]; then
        # Go back to first pane and split horizontally (bottom)
        tmux select-pane -t "$SESSION:0.0"
        tmux split-window -v -t "$SESSION:0.0" \
            "cd ${PANES_DIR}/${MODEL_NAME} && ./init.sh; exec bash"
    else
        # Go to right pane and split horizontally
        tmux select-pane -t "$SESSION:0.1"
        tmux split-window -v -t "$SESSION:0.1" \
            "cd ${PANES_DIR}/${MODEL_NAME} && ./init.sh; exec bash"
    fi
    COUNT=$((COUNT+1))
done

# Set pane titles and colors
for i in $(seq 0 $((MODEL_COUNT - 1))); do
    MODEL_NAME=$(echo "$MODELS" | sed -n "$((i+1))p")
    tmux select-pane -t "$SESSION:0.$i" -T "$MODEL_NAME"
done

# Equalize layout
tmux select-layout -t "$SESSION" tiled

# Set status bar
tmux set-option -t "$SESSION" status on
tmux set-option -t "$SESSION" status-style "bg=#1a1a2e,fg=#e0e0e0"
tmux set-option -t "$SESSION" status-left "#[fg=#00ff00] TUI Bench #[fg=#888888]| #[fg=#00bfff]${PROMPT_NAME}"
tmux set-option -t "$SESSION" status-right "#[fg=#888888]Ctrl+b arrows to switch | Ctrl+b d to detach #[fg=#888888]| %H:%M"

# ---------------------------------------------------------------------------
# launch
# ---------------------------------------------------------------------------
echo ""
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  TUI Parallel — ${MODEL_COUNT} models × ${PROMPT_NAME}${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Session:  ${GREEN}${SESSION}${NC}"
echo -e "  Results:  ${RUN_DIR}"
echo ""
echo -e "  ${YELLOW}Controls:${NC}"
echo -e "    Ctrl+b ↑↓←→   switch between panes"
echo -e "    Ctrl+b d       detach (rejoin: tmux attach -t ${SESSION})"
echo -e "    /exit          quit Claude Code in a pane"
echo ""
echo -e "  ${YELLOW}After all panes exit:${NC}"
echo -e "    ./tui_collect.sh ${RUN_DIR}"
echo ""
read -r -p "  Press Enter to open tmux..."

tmux attach-session -t "$SESSION"

# ---------------------------------------------------------------------------
# after tmux exits — check if all done
# ---------------------------------------------------------------------------
echo ""
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  All panes closed${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo ""

ALL_DONE=true
for MODEL_NAME in $MODELS; do
    WS="$PANES_DIR/$MODEL_NAME"
    if [ -f "$WS/.done" ]; then
        echo -e "  ${GREEN}✓${NC} $MODEL_NAME"
    else
        echo -e "  ${RED}✗${NC} $MODEL_NAME (no .done marker)"
        ALL_DONE=false
    fi
done

echo ""
if $ALL_DONE; then
    echo -e "  ${GREEN}All models completed. Run:${NC}"
    echo -e "    ${CYAN}./tui_collect.sh ${RUN_DIR}${NC}"
else
    echo -e "  ${YELLOW}Some models may still be running.${NC}"
    echo -e "  Reattach: ${CYAN}tmux attach -t ${SESSION}${NC}"
fi
echo -e "  Results dir: ${RUN_DIR}"

#!/usr/bin/env bash
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$BENCH_DIR/config.json"

# --- helpers ---
die() { echo "[ERROR] $*" >&2; exit 1; }
log() { echo "[$(date '+%H:%M:%S')] $*"; }

# --- load config ---
RUNS=$(jq -r '.runs' "$CONFIG_FILE")
TIMEOUT=$(jq -r '.timeout_seconds' "$CONFIG_FILE")
RETRY=$(jq -r '.retry_count' "$CONFIG_FILE")
TEMPLATE_DIR=$(jq -r '.template_dir' "$CONFIG_FILE")
MODEL_COUNT=$(jq '.models | length' "$CONFIG_FILE")
PROMPT_COUNT=$(jq '.prompts | length' "$CONFIG_FILE")

if [ "$MODEL_COUNT" -eq 0 ]; then die "No models configured in config.json"; fi
if [ "$PROMPT_COUNT" -eq 0 ]; then die "No prompts configured in config.json"; fi

# --- setup output dir ---
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
RESULTS_DIR="$BENCH_DIR/results/$TIMESTAMP"
mkdir -p "$RESULTS_DIR"

log "Benchmark starting — $MODEL_COUNT models × $PROMPT_COUNT prompts × $RUNS runs"
log "Results: $RESULTS_DIR"

# --- main loop ---
declare -a SUMMARY_FILES=()

for m_idx in $(seq 0 $((MODEL_COUNT - 1))); do
  MODEL_FILE=$(jq -r ".models[$m_idx]" "$CONFIG_FILE")
  MODEL_NAME=$(basename "$MODEL_FILE" .json)

  for p_idx in $(seq 0 $((PROMPT_COUNT - 1))); do
    PROMPT_FILE=$(jq -r ".prompts[$p_idx]" "$CONFIG_FILE")
    PROMPT_NAME=$(basename "$PROMPT_FILE" .txt)
    PROMPT_CONTENT=$(cat "$BENCH_DIR/$PROMPT_FILE")

    RUN_DIR="$RESULTS_DIR/${MODEL_NAME}__${PROMPT_NAME}"
    mkdir -p "$RUN_DIR"

    log ">>> $MODEL_NAME / $PROMPT_NAME"

    RUN_METRICS_FILES=()

    for run in $(seq 1 "$RUNS"); do
      attempt=0
      success=false

      while [ "$attempt" -le "$RETRY" ] && [ "$success" = false ]; do
        if [ "$attempt" -gt 0 ]; then
          log "  Retry $attempt/$RETRY for $MODEL_NAME / $PROMPT_NAME run $run"
        fi

        WORKDIR=$(mktemp -d)
        trap "rm -rf '$WORKDIR'" EXIT

        # copy template files
        if [ "$TEMPLATE_DIR" != "null" ] && [ -d "$BENCH_DIR/$TEMPLATE_DIR" ]; then
          cp -r "$BENCH_DIR/$TEMPLATE_DIR/." "$WORKDIR/"
        fi

        RAW_FILE="$RUN_DIR/run-${run}.ndjson"

        START_MS=$(date +%s%3N)

        # run claude with timeout; pipe through ts for per-event timestamps
        # ts (moreutils) prepends ISO timestamps; fallback to raw capture
        set +e
        if command -v ts &>/dev/null; then
          timeout "$TIMEOUT" claude -p \
            --bare \
            --settings "$BENCH_DIR/$MODEL_FILE" \
            --output-format stream-json \
            --verbose \
            "$PROMPT_CONTENT" \
            2>/dev/null | ts "%.s" > "$RAW_FILE"
          EXIT_CODE=${PIPESTATUS[0]}
        else
          timeout "$TIMEOUT" claude -p \
            --bare \
            --settings "$BENCH_DIR/$MODEL_FILE" \
            --output-format stream-json \
            --verbose \
            "$PROMPT_CONTENT" \
            > "$RAW_FILE" 2>/dev/null
          EXIT_CODE=$?
        fi
        set -e

        END_MS=$(date +%s%3N)
        WALL_DURATION=$((END_MS - START_MS))

        # determine status
        if [ "$EXIT_CODE" -eq 124 ]; then
          STATUS="timeout"
          ERROR_MSG="timed out after ${TIMEOUT}s"
        elif [ "$EXIT_CODE" -ne 0 ]; then
          STATUS="error"
          ERROR_MSG="exit code $EXIT_CODE"
        else
          STATUS="success"
          ERROR_MSG=""
        fi

        # --- extract metrics (strip optional ts prefix) ---
        RESULT_LINE=$(grep '"type":"result"' "$RAW_FILE" 2>/dev/null | tail -1 | sed 's/^[0-9.]* //' || echo '{}')

        # safely extract from result event
        _get() { echo "$RESULT_LINE" | jq -r "$1 // empty" 2>/dev/null || echo ""; }

        # tool call count from assistant events
        TOOL_COUNT=$(grep '"type":"tool_use"' "$RAW_FILE" 2>/dev/null | wc -l || echo 0)

        # thinking token peak from system events (strip optional ts prefix)
        THINKING_PEAK=$(grep '"subtype":"thinking_tokens"' "$RAW_FILE" 2>/dev/null | \
          sed 's/^[0-9.]* //' | jq -r '.estimated_tokens // 0' 2>/dev/null | sort -n | tail -1 || echo 0)

        # token usage from result
        INPUT_TOKENS=$(_get '.usage.input_tokens')
        OUTPUT_TOKENS=$(_get '.usage.output_tokens')

        # model name from result
        RESULT_MODEL=$(_get '.modelUsage | keys[0]')

        # is this actually a success based on result content?
        if [ "$STATUS" = "success" ]; then
          RESULT_ERROR=$(_get '.is_error')
          if [ "$RESULT_ERROR" = "true" ]; then
            STATUS="api_error"
            ERROR_MSG=$(_get '.api_error_status')
          fi
        fi

        # decide whether to retry
        if [ "$STATUS" = "success" ]; then
          success=true
        else
          attempt=$((attempt + 1))
        fi

        METRICS_FILE="$RUN_DIR/run-${run}.metrics.json"
        jq -n \
          --arg run "$run" \
          --arg model_config "$MODEL_FILE" \
          --arg model_name "$RESULT_MODEL" \
          --arg prompt "$PROMPT_NAME" \
          --arg status "$STATUS" \
          --arg error_msg "$ERROR_MSG" \
          --argjson wall_duration_ms "$WALL_DURATION" \
          --argjson duration_ms "$(_get '.duration_ms')" \
          --argjson duration_api_ms "$(_get '.duration_api_ms')" \
          --argjson ttft_ms "$(_get '.ttft_ms')" \
          --argjson time_to_request_ms "$(_get '.time_to_request_ms')" \
          --argjson num_turns "$(_get '.num_turns')" \
          --argjson total_cost_usd "$(_get '.total_cost_usd')" \
          --argjson input_tokens "$INPUT_TOKENS" \
          --argjson output_tokens "$OUTPUT_TOKENS" \
          --argjson tool_call_count "$TOOL_COUNT" \
          --argjson thinking_token_peak "$THINKING_PEAK" \
          '{
            run: $run | tonumber,
            model_config: $model_config,
            model_name: $model_name,
            prompt: $prompt,
            status: $status,
            error: (if $error_msg == "" then null else $error_msg end),
            metrics: {
              wall_duration_ms: $wall_duration_ms,
              duration_ms: $duration_ms,
              duration_api_ms: $duration_api_ms,
              ttft_ms: $ttft_ms,
              time_to_request_ms: $time_to_request_ms,
              num_turns: $num_turns,
              total_cost_usd: $total_cost_usd,
              input_tokens: $input_tokens,
              output_tokens: $output_tokens,
              tool_call_count: $tool_call_count,
              thinking_token_peak: $thinking_token_peak
            }
          }' > "$METRICS_FILE"

        RUN_METRICS_FILES+=("$METRICS_FILE")

        # cleanup temp dir for this run
        rm -rf "$WORKDIR"
      done

      if [ "$success" = false ]; then
        log "  Run $run: FAILED after $((attempt)) attempts — $ERROR_MSG"
      else
        log "  Run $run: OK — ${WALL_DURATION}ms wall, $(_get '.duration_ms')ms claude"
      fi
    done

    # --- aggregate this model+prompt across runs ---
    AGG_FILE="$RUN_DIR/aggregate.json"
    jq -s '
      def avg: if length == 0 then 0 else (add / length) end;
      {
        model_config: .[0].model_config,
        model_name: .[0].model_name,
        prompt: .[0].prompt,
        total_runs: length,
        successful_runs: (map(select(.status == "success")) | length),
        failed_runs: (map(select(.status != "success")) | length),
        metrics: {
          wall_duration_ms:    { min: (map(.metrics.wall_duration_ms) | min), max: max, avg: avg, values: [.[].metrics.wall_duration_ms] },
          duration_ms:         { min: (map(.metrics.duration_ms) | min), max: max, avg: avg, values: [.[].metrics.duration_ms] },
          duration_api_ms:     { min: (map(.metrics.duration_api_ms) | min), max: max, avg: avg, values: [.[].metrics.duration_api_ms] },
          ttft_ms:             { min: (map(.metrics.ttft_ms) | min), max: max, avg: avg, values: [.[].metrics.ttft_ms] },
          num_turns:           { min: (map(.metrics.num_turns) | min), max: max, avg: avg, values: [.[].metrics.num_turns] },
          total_cost_usd:      { min: (map(.metrics.total_cost_usd) | min), max: max, avg: avg, values: [.[].metrics.total_cost_usd] },
          input_tokens:        { min: (map(.metrics.input_tokens) | min), max: max, avg: avg, values: [.[].metrics.input_tokens] },
          output_tokens:       { min: (map(.metrics.output_tokens) | min), max: max, avg: avg, values: [.[].metrics.output_tokens] },
          tool_call_count:     { min: (map(.metrics.tool_call_count) | min), max: max, avg: avg, values: [.[].metrics.tool_call_count] },
          thinking_token_peak: { min: (map(.metrics.thinking_token_peak) | min), max: max, avg: avg, values: [.[].metrics.thinking_token_peak] }
        }
      }' "${RUN_METRICS_FILES[@]}" > "$AGG_FILE"

    SUMMARY_FILES+=("$AGG_FILE")
  done
done

# --- global summary ---
SUMMARY_FILE="$RESULTS_DIR/summary.json"
jq -s '.' "${SUMMARY_FILES[@]}" > "$SUMMARY_FILE"
log "Summary written: $SUMMARY_FILE"

# --- generate report ---
REPORT_FILE="$RESULTS_DIR/report.md"
python3 "$BENCH_DIR/report.py" "$SUMMARY_FILE" "$REPORT_FILE"
log "Report written: $REPORT_FILE"

# --- upload to Feishu ---
FEISHU_TITLE="Bench Report — $TIMESTAMP"
if command -v lark-cli &>/dev/null; then
  log "Uploading to Feishu..."
  lark-cli doc create --title "$FEISHU_TITLE" --content "$(cat "$REPORT_FILE")" 2>&1 || \
    log "Feishu upload failed (check lark-cli auth). Report saved locally: $REPORT_FILE"
else
  log "lark-cli not found — skipping Feishu upload. Report: $REPORT_FILE"
fi

log "Done. Results: $RESULTS_DIR"

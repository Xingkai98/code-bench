#!/usr/bin/env python3
"""Generate a standalone HTML viewer for a Claude Code NDJSON run file."""

import json
import re
import sys
from pathlib import Path


def parse_ndjson(ndjson_path):
    """Parse NDJSON into structured conversation data.

    Returns dict with:
      - init: system init info
      - turns: list of dicts, each with assistant blocks and user results
      - result: final result event
      - thinking_total: total thinking_tokens events
      - thinking_peak: max estimated_tokens
    """
    init = {}
    turns = []
    current_turn = None
    result = {}
    thinking_total = 0
    thinking_peak = 0
    task_map = {}  # tool_use_id -> {started, notified}

    with open(ndjson_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                # Strip optional ts prefix (seconds since start)
                line = re.sub(r"^\d+\.\d+\s", "", line)
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue

            t = evt.get("type", "")
            st = evt.get("subtype", "")

            if t == "system":
                if st == "init":
                    init = evt
                elif st == "thinking_tokens":
                    thinking_total += 1
                    peak = evt.get("estimated_tokens", 0)
                    if peak > thinking_peak:
                        thinking_peak = peak
                elif st == "task_started":
                    task_map[evt.get("tool_use_id", "")] = {"started": evt}
                elif st == "task_notification":
                    tid = evt.get("tool_use_id", "")
                    if tid in task_map:
                        task_map[tid]["notified"] = evt

            elif t == "assistant":
                msg = evt.get("message", {})
                blocks = msg.get("content", [])

                # Start new turn when we see first assistant message or after user
                if current_turn is None or current_turn.get("has_user"):
                    current_turn = {"assistant_blocks": [], "user_results": [],
                                    "message_ids": set(), "has_user": False}
                    turns.append(current_turn)

                for block in blocks:
                    bt = block.get("type", "")
                    if bt == "thinking":
                        current_turn["assistant_blocks"].append({
                            "type": "thinking",
                            "text": block.get("thinking", ""),
                            "signature": block.get("signature", ""),
                            "tokens_peak": thinking_peak,
                        })
                    elif bt == "tool_use":
                        current_turn["assistant_blocks"].append({
                            "type": "tool_use",
                            "tool_name": block.get("name", "?"),
                            "tool_id": block.get("id", ""),
                            "input": block.get("input", {}),
                        })
                    elif bt == "text":
                        current_turn["assistant_blocks"].append({
                            "type": "text",
                            "text": block.get("text", ""),
                        })

                current_turn["message_ids"].add(msg.get("id", ""))
                current_turn["has_user"] = False

            elif t == "user":
                tr = evt.get("tool_use_result", {})
                if isinstance(tr, str):
                    tr = {"stdout": "", "stderr": tr, "interrupted": False}
                mc = evt.get("message", {}).get("content", [{}])
                tool_result = mc[0] if mc else {}

                if current_turn is not None:
                    current_turn["user_results"].append({
                        "tool_use_id": evt.get("parent_tool_use_id",
                                               tool_result.get("tool_use_id", "")),
                        "stdout": tr.get("stdout", ""),
                        "stderr": tr.get("stderr", ""),
                        "is_error": tool_result.get("is_error", False),
                        "interrupted": tr.get("interrupted", False),
                    })
                    current_turn["has_user"] = True

            elif t == "result":
                result = evt

    # Compute per-turn thinking peak by watching thinking_tokens before each assistant
    return {
        "init": init,
        "turns": turns,
        "result": result,
        "thinking_total": thinking_total,
        "thinking_peak": thinking_peak,
        "task_map": task_map,
    }


def html_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def format_duration(ms):
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms}ms"
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    m = int(s // 60)
    sec = s % 60
    return f"{m}m{sec:.0f}s"


def format_cost(usd):
    if usd is None:
        return "-"
    return f"${usd:.4f}"


def render_html(data, title=""):
    """Render parsed NDJSON data into a standalone HTML page."""
    init = data["init"]
    turns = data["turns"]
    result = data["result"]
    thinking_total = data["thinking_total"]
    thinking_peak = data["thinking_peak"]

    model = init.get("model", "?")
    cwd = init.get("cwd", "?")
    version = init.get("claude_code_version", "?")
    tools = ", ".join(init.get("tools", []))
    skills_count = len(init.get("skills", []))

    usage = result.get("usage", {})
    dur_ms = result.get("duration_ms")
    dur_api = result.get("duration_api_ms")
    ttft = result.get("ttft_ms")
    cost = result.get("total_cost_usd")
    num_turns = result.get("num_turns", len(turns))
    tokens_in = usage.get("input_tokens", 0)
    tokens_out = usage.get("output_tokens", 0)
    tokens_cache = usage.get("cache_read_input_tokens", 0)
    status = result.get("subtype", "?")
    eval_score = result.get("score")  # may be None
    total_tool_calls = sum(
        sum(1 for b in t["assistant_blocks"] if b["type"] == "tool_use")
        for t in turns
    )

    def _th_peak():
        peaks = [b.get("tokens_peak", 0) for t in turns
                 for b in t["assistant_blocks"] if b["type"] == "thinking" and b.get("tokens_peak")]
        return max(peaks) if peaks else thinking_peak

    # Build stat cards
    stat_cards = [
        ("Status", "✔" if status == "success" else "✘", "ok" if status == "success" else "err"),
        ("Duration", format_duration(dur_ms), ""),
        ("API Time", format_duration(dur_api), ""),
        ("TTFT", format_duration(ttft), ""),
        ("Turns", str(num_turns), ""),
        ("Cost", format_cost(cost), ""),
        ("Tokens In", f"{tokens_in:,}", ""),
        ("Tokens Out", f"{tokens_out:,}", ""),
        ("Cache Read", f"{tokens_cache:,}", ""),
        ("Tool Calls", str(total_tool_calls), ""),
        ("Thinking Peak", str(_th_peak()), ""),
    ]
    if eval_score is not None:
        stat_cards.append(("Eval Score", f"{eval_score:.2f}", "ok" if eval_score == 1.0 else "err"))

    stats_html = "\n".join(
        f'<div class="stat"><span class="stat-label">{label}</span><span class="stat-value {cls}">{val}</span></div>'
        for label, val, cls in stat_cards
    )

    # Build turns HTML
    turns_html_parts = []
    for i, turn in enumerate(turns):
        blocks = turn["assistant_blocks"]
        # Separate thinking from others
        thinking_blocks = [b for b in blocks if b["type"] == "thinking"]
        action_blocks = [b for b in blocks if b["type"] != "thinking"]
        user_results = turn["user_results"]

        turn_id = f"turn-{i+1}"

        # Header
        turn_html = [f'<div class="turn" id="{turn_id}">']
        tool_names = [b["tool_name"] for b in action_blocks if b["type"] == "tool_use"]
        turn_label = " → ".join(tool_names) if tool_names else ("Text" if any(b["type"] == "text" for b in action_blocks) else "Thinking only")
        turn_html.append(f'<div class="turn-header" onclick="toggleTurn(\'{turn_id}\')">')
        turn_html.append(f'<span class="turn-arrow">▼</span> Turn {i+1}: {html_escape(turn_label)}</div>')
        turn_html.append(f'<div class="turn-body">')

        # Thinking blocks (collapsed)
        for ti, tb in enumerate(thinking_blocks):
            text = tb["text"]
            preview = text[:120].replace("\n", " ")
            think_id = f"{turn_id}-think-{ti}"
            turn_html.append(f'<div class="thinking-block" onclick="toggleThinking(\'{think_id}\')">')
            turn_html.append(f'<span class="thinking-arrow">▸</span>')
            turn_html.append(f'<span class="thinking-label">Thinking ({len(text)} chars)</span>')
            turn_html.append(f'<span class="thinking-preview">{html_escape(preview)}...</span>')
            turn_html.append(f'</div>')
            turn_html.append(f'<div class="thinking-content" id="{think_id}" style="display:none">')
            turn_html.append(f'<pre>{html_escape(text)}</pre>')
            turn_html.append(f'</div>')

        # Action blocks
        for block in action_blocks:
            if block["type"] == "tool_use":
                tool_name = block["tool_name"]
                inp = block.get("input", {})
                tool_icon = {"Bash": "💻", "Edit": "✏️", "Read": "📖", "Write": "📝"}.get(tool_name, "🔧")
                cmd = inp.get("command", inp.get("content", inp.get("file_path", "")))
                if isinstance(cmd, list):
                    cmd = " ".join(str(x) for x in cmd)
                cmd_display = html_escape(str(cmd)[:200])
                desc = html_escape(inp.get("description", "")[:100])

                turn_html.append(f'<div class="tool-call">')
                turn_html.append(f'<span class="tool-icon">{tool_icon}</span>')
                turn_html.append(f'<span class="tool-name">{tool_name}</span>')
                if desc:
                    turn_html.append(f'<span class="tool-desc">{desc}</span>')
                turn_html.append(f'<pre class="tool-input">{cmd_display}</pre>')
                turn_html.append(f'</div>')

            elif block["type"] == "text":
                text = block["text"]
                turn_html.append(f'<div class="assistant-text">{html_escape(text)}</div>')

        # User results
        for ri, r in enumerate(user_results):
            stdout = r.get("stdout", "")
            stderr = r.get("stderr", "")
            is_err = r.get("is_error", False)
            interrupted = r.get("interrupted", False)

            result_class = "tool-result-error" if (is_err or interrupted) else "tool-result"
            turn_html.append(f'<div class="{result_class}">')
            if interrupted:
                turn_html.append(f'<span class="result-badge">⏱ Interrupted</span>')
            elif is_err:
                turn_html.append(f'<span class="result-badge">❌ Error</span>')
            if stdout.strip():
                turn_html.append(f'<pre class="result-stdout">{html_escape(stdout.strip()[:5000])}</pre>')
            if stderr.strip():
                turn_html.append(f'<pre class="result-stderr">{html_escape(stderr.strip()[:2000])}</pre>')
            if not stdout.strip() and not stderr.strip():
                turn_html.append(f'<span class="result-empty">(no output)</span>')
            turn_html.append(f'</div>')

        turn_html.append('</div></div>')  # turn-body, turn
        turns_html_parts.append("\n".join(turn_html))

    turns_html = "\n".join(turns_html_parts)

    # Full HTML
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html_escape(title or f'Run Viewer — {model}')}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0d1117; color: #c9d1d9; line-height: 1.5;
    max-width: 1000px; margin: 0 auto; padding: 20px;
}}
.header {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 20px; margin-bottom: 20px;
}}
.header h1 {{ font-size: 18px; color: #f0f6fc; margin-bottom: 8px; }}
.header .meta {{ font-size: 13px; color: #8b949e; margin-bottom: 12px; }}
.stats {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
    gap: 8px; margin-top: 12px;
}}
.stat {{
    background: #21262d; border-radius: 6px; padding: 8px 12px;
    display: flex; flex-direction: column;
}}
.stat-label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }}
.stat-value {{ font-size: 16px; font-weight: 600; color: #f0f6fc; }}
.stat-value.ok {{ color: #3fb950; }}
.stat-value.err {{ color: #f85149; }}

.turn {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    margin-bottom: 12px; overflow: hidden;
}}
.turn-header {{
    padding: 10px 16px; cursor: pointer; user-select: none;
    font-weight: 600; font-size: 14px; color: #f0f6fc;
    background: #1c2128; border-bottom: 1px solid #30363d;
    display: flex; align-items: center; gap: 8px;
}}
.turn-header:hover {{ background: #21262d; }}
.turn-arrow {{ font-size: 10px; color: #8b949e; transition: transform 0.2s; }}
.turn.collapsed .turn-arrow {{ transform: rotate(-90deg); }}
.turn.collapsed .turn-body {{ display: none; }}
.turn-body {{ padding: 12px 16px; }}

.thinking-block {{
    padding: 6px 10px; margin-bottom: 8px; border-radius: 4px;
    background: #1c2128; border-left: 3px solid #8b949e;
    cursor: pointer; font-size: 12px; color: #8b949e;
    display: flex; align-items: center; gap: 8px;
}}
.thinking-block:hover {{ background: #21262d; }}
.thinking-arrow {{ font-size: 10px; }}
.thinking-label {{ font-weight: 600; white-space: nowrap; }}
.thinking-preview {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.thinking-content {{ margin-bottom: 8px; }}
.thinking-content pre {{
    background: #0d1117; border: 1px solid #30363d; border-radius: 4px;
    padding: 10px; font-size: 12px; color: #8b949e; white-space: pre-wrap;
    max-height: 400px; overflow-y: auto;
}}

.tool-call {{
    margin-bottom: 6px; padding: 8px 12px; border-radius: 4px;
    background: #1a1f2e; border-left: 3px solid #58a6ff;
    display: flex; align-items: flex-start; gap: 8px; flex-wrap: wrap;
}}
.tool-icon {{ font-size: 16px; }}
.tool-name {{ font-weight: 600; font-size: 13px; color: #58a6ff; }}
.tool-desc {{ font-size: 12px; color: #8b949e; }}
.tool-input {{
    width: 100%; background: #0d1117; border: 1px solid #30363d; border-radius: 4px;
    padding: 8px; font-size: 12px; font-family: 'SF Mono', 'Fira Code', monospace;
    color: #c9d1d9; white-space: pre-wrap; word-break: break-all;
    max-height: 150px; overflow-y: auto;
}}

.assistant-text {{
    padding: 10px 12px; margin-bottom: 8px; border-radius: 4px;
    background: #1c2128; font-size: 14px; white-space: pre-wrap;
}}

.tool-result, .tool-result-error {{
    margin-bottom: 8px; padding: 8px 12px; border-radius: 4px;
    background: #161b22; border: 1px solid #30363d;
}}
.tool-result-error {{ border-color: #f85149; }}
.result-badge {{ font-size: 11px; font-weight: 600; margin-bottom: 4px; display: inline-block; }}
.result-badge {{ color: #f85149; }}

.result-stdout {{
    background: #0d1117; border-radius: 4px; padding: 8px;
    font-size: 12px; font-family: 'SF Mono', 'Fira Code', monospace;
    color: #7ee787; white-space: pre-wrap; max-height: 300px; overflow-y: auto;
}}
.result-stderr {{
    background: #2d1117; border-radius: 4px; padding: 8px;
    font-size: 12px; font-family: 'SF Mono', 'Fira Code', monospace;
    color: #f85149; white-space: pre-wrap; max-height: 150px; overflow-y: auto;
    margin-top: 4px;
}}
.result-empty {{ font-size: 12px; color: #484f58; font-style: italic; }}

.footer {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 16px; margin-top: 20px; font-size: 12px; color: #8b949e;
}}
.footer span {{ margin-right: 20px; }}

.model-name {{ color: #58a6ff; }}
</style>
</head>
<body>

<div class="header">
    <h1>🤖 <span class="model-name">{html_escape(model)}</span></h1>
    <div class="meta">
        CWD: {html_escape(cwd)} &nbsp;|&nbsp;
        Version: {html_escape(version)} &nbsp;|&nbsp;
        Tools: {html_escape(tools)} &nbsp;|&nbsp;
        Skills: {skills_count}
    </div>
    <div class="stats">{stats_html}</div>
</div>

<div class="conversation">
{turns_html}
</div>

<div class="footer">
    <span>🧠 Thinking token events: <b>{thinking_total}</b></span>
    <span>📊 Peak thinking tokens: <b>{thinking_peak}</b></span>
</div>

<script>
function toggleThinking(id) {{
    var el = document.getElementById(id);
    var arrow = el.previousElementSibling.querySelector('.thinking-arrow');
    if (el.style.display === 'none') {{
        el.style.display = 'block';
        arrow.textContent = '▾';
    }} else {{
        el.style.display = 'none';
        arrow.textContent = '▸';
    }}
}}

function toggleTurn(id) {{
    var el = document.getElementById(id);
    el.classList.toggle('collapsed');
}}
</script>
</body>
</html>"""

    return html


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate HTML viewer for Claude Code NDJSON run")
    parser.add_argument("ndjson", help="Path to NDJSON file")
    parser.add_argument("-o", "--output", help="Output HTML file path (default: next to NDJSON)")
    args = parser.parse_args()

    ndjson_path = Path(args.ndjson)
    if not ndjson_path.exists():
        print(f"Error: {ndjson_path} not found", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or (ndjson_path.parent / f"{ndjson_path.stem}.html")
    if isinstance(output_path, str):
        output_path = Path(output_path)

    title = f"{ndjson_path.parent.parent.name} / {ndjson_path.stem}"
    data = parse_ndjson(ndjson_path)
    html = render_html(data, title)

    output_path.write_text(html)
    print(f"HTML written to {output_path}")


if __name__ == "__main__":
    main()

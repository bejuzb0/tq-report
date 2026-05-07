"""Render the full Claude Code session jsonl as a readable Markdown transcript.

Excludes assistant 'thinking' blocks (internal reasoning) and noisy system
events; keeps user messages, assistant text, tool calls (with inputs), and
tool results (truncated)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(
    "/Users/chiragjain/.claude/projects/"
    "-Users-chiragjain-Desktop-UWM-Courses-CS-639-Project/"
    "e2474999-5d44-4b28-8bac-23a64d3117f3.jsonl"
)
DST = Path(
    "/Users/chiragjain/Desktop/UWM_Courses/CS_639/Project/turboquant-clip/"
    "full_conversation_history.md"
)

MAX_TOOL_INPUT = 1500       # truncate large tool inputs (file writes etc.)
MAX_TOOL_RESULT = 2500      # truncate large tool results (file reads etc.)


def truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"\n... [truncated, {len(s) - n} more chars]"


def render_block(block: dict) -> str:
    btype = block.get("type")
    if btype == "text":
        return block.get("text", "").rstrip()
    if btype == "thinking":
        return ""  # skip internal reasoning
    if btype == "tool_use":
        name = block.get("name", "?")
        inp = block.get("input", {})
        try:
            inp_str = json.dumps(inp, indent=2, ensure_ascii=False)
        except Exception:
            inp_str = repr(inp)
        return f"**[Tool call: {name}]**\n```json\n{truncate(inp_str, MAX_TOOL_INPUT)}\n```"
    if btype == "tool_result":
        content = block.get("content", "")
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(c.get("text", ""))
                else:
                    parts.append(str(c))
            content = "\n".join(parts)
        if not isinstance(content, str):
            content = str(content)
        return f"**[Tool result]**\n```\n{truncate(content, MAX_TOOL_RESULT)}\n```"
    return ""


def render_message(obj: dict) -> str | None:
    t = obj.get("type")
    msg = obj.get("message") or {}
    ts = obj.get("timestamp", "")
    content = msg.get("content")

    if t == "user":
        if isinstance(content, str):
            text = content.rstrip()
            if not text:
                return None
            return f"## User — {ts}\n\n{text}\n"
        if isinstance(content, list):
            parts = []
            for blk in content:
                rendered = render_block(blk)
                if rendered:
                    parts.append(rendered)
            if not parts:
                return None
            body = "\n\n".join(parts)
            # If the message is purely tool_results, label it as such
            kinds = {b.get("type") for b in content if isinstance(b, dict)}
            label = "User" if "text" in kinds else "Tool Results"
            return f"## {label} — {ts}\n\n{body}\n"
        return None

    if t == "assistant":
        if not isinstance(content, list):
            return None
        parts = []
        for blk in content:
            rendered = render_block(blk)
            if rendered:
                parts.append(rendered)
        if not parts:
            return None
        body = "\n\n".join(parts)
        return f"## Assistant — {ts}\n\n{body}\n"

    if t == "attachment":
        # User-supplied attachments (pasted file contents, screenshots, etc.)
        attach = obj.get("attachment") or {}
        kind = attach.get("type", "attachment")
        summary_keys = [k for k in ("file_path", "filename", "name") if k in attach]
        summary = ", ".join(f"{k}={attach[k]}" for k in summary_keys) or kind
        return f"## Attachment — {ts}\n\n_[{kind}: {summary}]_\n"

    return None


def main() -> None:
    out: list[str] = []
    out.append(
        "# Full conversation history — TurboQuant on CLIP\n\n"
        f"Source: `{SRC}`\n\n"
        "Rendering rules: user/assistant turns in order; assistant 'thinking' "
        "blocks omitted; tool calls show name + JSON input (truncated at "
        f"{MAX_TOOL_INPUT} chars); tool results truncated at "
        f"{MAX_TOOL_RESULT} chars. System events (permission mode, "
        "file-history snapshots) skipped.\n"
    )

    with SRC.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            rendered = render_message(obj)
            if rendered:
                out.append(rendered)

    DST.write_text("\n---\n\n".join(out))
    print(f"wrote {DST} ({DST.stat().st_size:,} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()

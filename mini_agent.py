#!/usr/bin/env python3
"""mini_agent: a small, token-frugal coding agent in the terminal (Claude API)."""
import os
import subprocess
import sys
from pathlib import Path

import anthropic

MODEL = os.environ.get("MINI_AGENT_MODEL", "claude-opus-5")
EFFORT = os.environ.get("MINI_AGENT_EFFORT", "medium")  # low | medium | high | xhigh | max
MAX_TOKENS = 128000  # model's output ceiling; you only pay for tokens actually generated
MAX_OUT = 8000  # chars of tool output sent back to the model (head + tail)
ROOT = Path.cwd().resolve()
AUTO_YES = "--yes" in sys.argv

# Kept short and byte-stable so it caches; no timestamps or per-run values.
SYSTEM = (
    "You are a coding agent working in the user's project directory. "
    "Use the bash and editor tools to inspect and change files. "
    "Be concise: no preamble, no restating the request, short final answers. "
    "Read only what you need (use view_range, grep, head) instead of whole large files."
)
TOOLS = [
    {"type": "bash_20250124", "name": "bash"},
    {"type": "text_editor_20250728", "name": "str_replace_based_edit_tool", "max_characters": MAX_OUT},
]
BETAS = ["compact-2026-01-12", "server-side-fallback-2026-07-01"]


def clip(text: str) -> str:
    if len(text) <= MAX_OUT:
        return text
    half = MAX_OUT // 2
    return f"{text[:half]}\n...[{len(text) - MAX_OUT} chars cut]...\n{text[-half:]}"


def safe_path(p: str) -> Path:
    path = (ROOT / p).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"path outside project: {p}")
    return path


def run_bash(inp: dict) -> str:
    if inp.get("restart"):
        return "shell restarted"
    cmd = inp["command"]
    if not AUTO_YES and input(f"\033[33m$ {cmd}\033[0m  run? [y/N] ").strip().lower() != "y":
        raise RuntimeError("user declined this command")
    r = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=300)
    return clip((r.stdout + r.stderr).strip() or f"(exit {r.returncode}, no output)")


def run_editor(inp: dict) -> str:
    cmd, path = inp["command"], safe_path(inp["path"])
    if cmd == "view":
        if path.is_dir():
            return clip("\n".join(sorted(str(p.relative_to(ROOT)) for p in path.iterdir())))
        lines = path.read_text().splitlines()
        start, end = inp.get("view_range") or [1, len(lines)]
        end = len(lines) if end == -1 else end
        return clip("\n".join(f"{i}\t{l}" for i, l in enumerate(lines[start - 1:end], start)))
    if cmd == "create":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(inp["file_text"])
        return "created"
    if cmd == "str_replace":
        text = path.read_text()
        n = text.count(inp["old_str"])
        if n != 1:
            raise ValueError(f"old_str matched {n} times; must match exactly once")
        path.write_text(text.replace(inp["old_str"], inp.get("new_str", ""), 1))
        return "edited"
    if cmd == "insert":
        lines = path.read_text().splitlines(keepends=True)
        new = inp["insert_text"]
        lines.insert(inp["insert_line"], new if new.endswith("\n") else new + "\n")
        path.write_text("".join(lines))
        return "inserted"
    raise ValueError(f"unknown command: {cmd}")


HANDLERS = {"bash": run_bash, "str_replace_based_edit_tool": run_editor}


def run_tools(content) -> list:
    results = []
    for block in content:
        if block.type != "tool_use":
            continue
        try:
            out, err = HANDLERS[block.name](block.input), False
        except Exception as e:  # report every tool failure back so the model can recover
            out, err = f"Error: {e}", True
        results.append({"type": "tool_result", "tool_use_id": block.id, "content": out or "(empty)", "is_error": err})
    return results


def main() -> None:
    client = anthropic.Anthropic()
    messages: list = []
    used = {"in": 0, "cached": 0, "out": 0}
    print(f"mini_agent ({MODEL}, effort={EFFORT}) in {ROOT}. Ctrl-D to quit.")
    while True:
        try:
            prompt = input("\n\033[1m> \033[0m").strip()
        except EOFError:
            break
        if not prompt:
            continue
        checkpoint = len(messages)  # roll back here if the turn fails, so history stays valid
        messages.append({"role": "user", "content": prompt})
        while True:
            try:
                # Streaming keeps very long answers (up to MAX_TOKENS) from hitting HTTP timeouts.
                with client.beta.messages.stream(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM,
                    tools=TOOLS,
                    messages=messages,
                    output_config={"effort": EFFORT},
                    cache_control={"type": "ephemeral"},  # caches the whole prefix each turn
                    context_management={"edits": [{"type": "compact_20260112"}]},
                    fallbacks="default",
                    betas=BETAS,
                ) as stream:
                    for text in stream.text_stream:
                        print(text, end="", flush=True)
                    resp = stream.get_final_message()
                print()
            except anthropic.APIStatusError as e:
                print(f"API error {e.status_code}: {e.message}")
                del messages[checkpoint:]
                break
            except anthropic.APIConnectionError:
                print("Network error; try again.")
                del messages[checkpoint:]
                break
            u = resp.usage
            used["in"] += u.input_tokens + (u.cache_creation_input_tokens or 0)
            used["cached"] += u.cache_read_input_tokens or 0
            used["out"] += u.output_tokens
            # Keep full content (incl. compaction blocks) so server-side compaction works.
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "refusal":
                print("(request declined)")
                del messages[checkpoint:]
                break
            if resp.stop_reason == "pause_turn":
                continue
            if resp.stop_reason == "max_tokens" and any(b.type == "tool_use" for b in resp.content):
                # A cut-off tool call may carry partial input; ask for a smaller step instead of running it.
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                     "content": "Error: output hit max_tokens; retry in smaller pieces."}
                    for b in resp.content if b.type == "tool_use"]})
                continue
            if resp.stop_reason != "tool_use":
                break
            messages.append({"role": "user", "content": run_tools(resp.content)})
        print(f"\033[2m[tokens: in {used['in']}, cache-read {used['cached']}, out {used['out']}]\033[0m")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""mini_agent: a small, token-frugal AI assistant in the terminal (Claude API)."""
import base64
import json
import mimetypes
import os
import subprocess
import sys
import time
from pathlib import Path

import anthropic
from anthropic.tools.memory import BetaLocalFilesystemMemoryTool

HOME = Path.home() / ".mini_agent"  # memories/, chats/, instructions.md
CHATS = HOME / "chats"
MAX_TOKENS = 128000  # model's output ceiling; you only pay for tokens actually generated
MAX_OUT = 8000  # chars of tool output sent back to the model (head + tail)
ROOT = Path.cwd().resolve()
AUTO_YES = "--yes" in sys.argv
LOCAL = "--local" in sys.argv  # free mode: an open-source model on this computer, served by Ollama
OLLAMA_URL = os.environ.get("MINI_AGENT_OLLAMA_URL", "http://localhost:11434")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
FALLBACK_MODELS = ("claude-opus-5", "claude-fable")  # models that take fallbacks="default"
S = {  # settings changeable at runtime with slash commands
    "model": os.environ.get("MINI_AGENT_MODEL", "qwen3-coder" if LOCAL else "claude-opus-5"),
    "effort": os.environ.get("MINI_AGENT_EFFORT", "medium"),
    "think": False,
}
HELP = """/new             start a new chat
/resume          continue the last saved chat
/attach <file>   attach an image, PDF or text file to your next message
/memory          show what the assistant remembers about you
/model <id>      switch model (Claude: Opus / Sonnet / Fable; --local: any Ollama model)
/effort <level>  low | medium | high | xhigh | max
/think           show or hide thinking summaries
Ctrl-C stops a reply, Ctrl-D quits."""

# Kept short and byte-stable so it caches; no timestamps or per-run values.
BASE_SYSTEM = (
    "You are a helpful assistant and coding agent running in the user's terminal, in their project directory. "
    f"You can {'' if LOCAL else 'search and fetch the web, '}run shell commands, edit project files, and keep notes about the user "
    "across chats with the memory tool (save lasting preferences and facts; never secrets). "
    "Be concise: no preamble, no restating the request, short final answers. "
    "Read only what you need (use view_range, grep, head) instead of whole large files."
)


# Local models don't know Anthropic's built-in tools, so they get the same tools with explicit schemas.
LOCAL_TOOLS = [
    {"name": "bash", "description": "Run a shell command in the project directory; returns stdout+stderr.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "editor",
     "description": "View or edit project files. command=view (path; optional view_range [start, end]), "
                    "create (path, file_text), str_replace (path, old_str, new_str; old_str must match once), "
                    "insert (path, insert_line, insert_text).",
     "input_schema": {"type": "object", "required": ["command", "path"], "properties": {
         "command": {"type": "string", "enum": ["view", "create", "str_replace", "insert"]},
         "path": {"type": "string"}, "file_text": {"type": "string"}, "old_str": {"type": "string"},
         "new_str": {"type": "string"}, "insert_line": {"type": "integer"}, "insert_text": {"type": "string"},
         "view_range": {"type": "array", "items": {"type": "integer"}}}}},
    {"name": "memory",
     "description": "Long-term memory kept across chats as files under /memories. View it at the start of a task; "
                    "save lasting user preferences. command=view (path), create (path, file_text), "
                    "str_replace (path, old_str, new_str), insert (path, insert_line, insert_text), "
                    "delete (path), rename (old_path, new_path).",
     "input_schema": {"type": "object", "required": ["command"], "properties": {
         "command": {"type": "string", "enum": ["view", "create", "str_replace", "insert", "delete", "rename"]},
         "path": {"type": "string"}, "file_text": {"type": "string"}, "old_str": {"type": "string"},
         "new_str": {"type": "string"}, "insert_line": {"type": "integer"}, "insert_text": {"type": "string"},
         "old_path": {"type": "string"}, "new_path": {"type": "string"}}}},
]


def load_system() -> str:
    parts = [BASE_SYSTEM]
    for p in (HOME / "instructions.md", ROOT / "AGENTS.md", ROOT / "CLAUDE.md"):
        if p.is_file():
            parts.append(f'<instructions source="{p}">\n{p.read_text().strip()}\n</instructions>')
    return "\n\n".join(parts)


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


def run_tools(tool_uses: list, handlers: dict) -> list:
    results = []
    for block in tool_uses:
        try:
            out, err = handlers[block.name](block.input), False
        except Exception as e:  # report every tool failure back so the model can recover
            out, err = f"Error: {e}", True
        results.append({"type": "tool_result", "tool_use_id": block.id, "content": out or "(empty)", "is_error": err})
    return results


def attachment_block(path: Path) -> dict:
    mime = mimetypes.guess_type(path.name)[0] or "text/plain"
    if LOCAL and mime == "application/pdf":
        raise ValueError("PDFs only work with Claude, not in --local mode")
    if mime in ("image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"):
        data = base64.standard_b64encode(path.read_bytes()).decode()
        kind = "document" if mime == "application/pdf" else "image"
        return {"type": kind, "source": {"type": "base64", "media_type": mime, "data": data}}
    text = path.read_text()
    if LOCAL:  # Ollama has no document blocks
        return {"type": "text", "text": f'<file name="{path.name}">\n{text}\n</file>'}
    return {"type": "document", "title": path.name, "source": {"type": "text", "media_type": "text/plain", "data": text}}


def new_chat_file() -> Path:
    return CHATS / f"{time.strftime('%Y%m%d-%H%M%S')}.json"


def save_chat(path: Path, messages: list) -> None:
    path.write_text(json.dumps(messages, ensure_ascii=False, default=lambda o: o.to_dict(mode="json")))


def load_last_chat() -> tuple:
    files = sorted(CHATS.glob("*.json"))
    if not files:
        print("(no saved chats)")
        return [], new_chat_file()
    print(f"(resumed {files[-1].name})")
    return json.loads(files[-1].read_text()), files[-1]


def show(event) -> None:
    if event.type == "content_block_start" and event.content_block.type in ("tool_use", "server_tool_use"):
        print(f"\033[2m[{event.content_block.name}]\033[0m", flush=True)
    elif event.type == "content_block_delta":
        if event.delta.type == "text_delta":
            print(event.delta.text, end="", flush=True)
        elif event.delta.type == "thinking_delta" and S["think"]:
            print(f"\033[2m{event.delta.thinking}\033[0m", end="", flush=True)


def ask(client, system: str, tools: list, messages: list):
    if LOCAL:  # Ollama supports the core Messages API only: no caching, compaction or betas
        kwargs = {"model": S["model"], "max_tokens": MAX_TOKENS, "system": system, "tools": tools,
                  "messages": messages, "output_config": {"effort": S["effort"]}}
        if S["think"]:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 1024}
        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                show(event)
            return stream.get_final_message()
    kwargs = {
        "model": S["model"],
        "max_tokens": MAX_TOKENS,
        "system": system,
        "tools": tools,
        "messages": messages,
        "thinking": {"type": "adaptive", "display": "summarized" if S["think"] else "omitted"},
        "output_config": {"effort": S["effort"]},
        "cache_control": {"type": "ephemeral"},  # caches the whole prefix each turn
        "context_management": {"edits": [{"type": "compact_20260112"}]},
        "betas": ["compact-2026-01-12"],
    }
    if S["model"].startswith(FALLBACK_MODELS):
        kwargs["fallbacks"] = "default"
        kwargs["betas"].append("server-side-fallback-2026-07-01")
    # Streaming keeps very long answers (up to MAX_TOKENS) from hitting HTTP timeouts.
    with client.beta.messages.stream(**kwargs) as stream:
        for event in stream:
            show(event)
        return stream.get_final_message()


def run_turn(client, system: str, tools: list, handlers: dict, messages: list, used: dict) -> None:
    pauses = 0
    while True:
        resp = ask(client, system, tools, messages)
        print()
        u = resp.usage
        used["in"] += u.input_tokens + (u.cache_creation_input_tokens or 0)
        used["cached"] += u.cache_read_input_tokens or 0
        used["out"] += u.output_tokens
        # Keep full content (incl. compaction blocks) so server-side compaction works.
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "refusal":
            raise RuntimeError("request declined")
        if resp.stop_reason == "pause_turn" and pauses < 5:  # long server-side web work; resume it
            pauses += 1
            continue
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason == "max_tokens" and tool_uses:
            # A cut-off tool call may carry partial input; ask for a smaller step instead of running it.
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                 "content": "Error: output hit max_tokens; retry in smaller pieces."}
                for b in tool_uses]})
            continue
        if resp.stop_reason != "tool_use":
            return
        messages.append({"role": "user", "content": run_tools(tool_uses, handlers)})


def main() -> None:
    CHATS.mkdir(parents=True, exist_ok=True)
    memory = BetaLocalFilesystemMemoryTool(base_path=str(HOME))
    tools = LOCAL_TOOLS if LOCAL else [
        {"type": "bash_20250124", "name": "bash"},
        {"type": "text_editor_20250728", "name": "str_replace_based_edit_tool", "max_characters": MAX_OUT},
        memory.to_dict(),
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5, "max_content_tokens": 20000},
    ]
    handlers = {"bash": run_bash, "str_replace_based_edit_tool": run_editor, "editor": run_editor,
                "memory": memory.call}
    system = load_system()
    client = anthropic.Anthropic(base_url=OLLAMA_URL, api_key="ollama") if LOCAL else anthropic.Anthropic()
    if not (client.api_key or client.auth_token or client.credentials):
        sys.exit("No Claude API key: set ANTHROPIC_API_KEY, or run with --local to use a free local model.")
    messages, chat_file = load_last_chat() if "--resume" in sys.argv else ([], new_chat_file())
    attachments: list = []
    used = {"in": 0, "cached": 0, "out": 0}
    mode = "local, free" if LOCAL else f"effort={S['effort']}"
    print(f"mini_agent ({S['model']}, {mode}) in {ROOT}. /help for commands, Ctrl-D to quit.")
    while True:
        try:
            prompt = input("\n\033[1m> \033[0m").strip()
        except EOFError:
            break
        except KeyboardInterrupt:
            continue
        if not prompt:
            continue
        if prompt.startswith("/"):
            cmd, _, arg = prompt.partition(" ")
            arg = arg.strip()
            if cmd == "/help":
                print(HELP)
            elif cmd == "/new":
                messages, chat_file, attachments = [], new_chat_file(), []
                print("(new chat)")
            elif cmd == "/resume":
                messages, chat_file = load_last_chat()
            elif cmd == "/attach":
                path = Path(arg).expanduser()
                if path.is_file():
                    attachments.append(path)
                    print(f"(attached {path.name}; it goes with your next message)")
                else:
                    print(f"(no such file: {arg})")
            elif cmd == "/memory":
                print(memory.call({"command": "view", "path": "/memories"}))
            elif cmd == "/model" and arg:
                S["model"] = arg
                print(f"(model: {arg})")
            elif cmd == "/effort" and arg in EFFORTS:
                S["effort"] = arg
                print(f"(effort: {arg})")
            elif cmd == "/think":
                S["think"] = not S["think"]
                print(f"(thinking summaries {'on' if S['think'] else 'off'})")
            else:
                print(HELP)
            continue
        checkpoint = len(messages)  # roll back here if the turn fails, so history stays valid
        try:
            content = [attachment_block(p) for p in attachments] + [{"type": "text", "text": prompt}]
        except (OSError, ValueError) as e:
            print(f"(can't read attachment: {e}; attachments cleared, please resend)")
            attachments = []
            continue
        attachments = []
        messages.append({"role": "user", "content": content})
        try:
            run_turn(client, system, tools, handlers, messages, used)
            save_chat(chat_file, messages)
        except KeyboardInterrupt:
            print("\n(stopped)")
            del messages[checkpoint:]
        except anthropic.APIStatusError as e:
            print(f"API error {e.status_code}: {e.message}")
            del messages[checkpoint:]
        except anthropic.APIConnectionError:
            print("Network error; try again.")
            del messages[checkpoint:]
        except RuntimeError as e:
            print(f"({e})")
            del messages[checkpoint:]
        print(f"\033[2m[tokens: in {used['in']}, cache-read {used['cached']}, out {used['out']}]\033[0m")


if __name__ == "__main__":
    main()

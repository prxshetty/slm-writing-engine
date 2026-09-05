import json
import uuid
import asyncio
import difflib
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from api.services.file_storage import storage
from api.services.assist_helpers import extract_anchor_context, _load_simple_prompt
import llm
import config
from api.services import context_injector
from api.services import harness_env
from api.services.harness_registry import HARNESS_DESCRIPTORS

router = APIRouter(prefix="/api/assist", tags=["assist"])


def _resolve_harness_argv(harness_id: str, prompt: str, cwd: str, mode: str = "edit") -> list:
    """Build argv for a harness, honoring custom executable paths in settings
    (`harnesses: { "<id>": { "executable": "/custom/path" } }`)."""
    desc = HARNESS_DESCRIPTORS.get(harness_id)
    if not desc:
        raise ValueError(f"Unknown harness: {harness_id}")
    s = storage.get_settings()
    overrides = s.get("harnesses") or {}
    custom = (overrides.get(harness_id) or {}).get("executable")
    exe = custom or harness_env.which_harness(desc["command"])
    if not exe:
        raise ValueError(
            f"{desc['name']} not found — install it or set a custom path in Settings > Harnesses"
        )
    argv = [exe]
    if desc.get("subcommand"):
        argv.append(desc["subcommand"])
    model = (overrides.get(harness_id) or {}).get("model")
    if model and desc.get("model_flag"):
        argv += [desc["model_flag"], model]
    agent = (desc.get("mode_agents") or {}).get(mode)
    if agent and desc.get("agent_flag"):
        argv += [desc["agent_flag"], agent]
    if desc.get("format_args"):
        argv += list(desc["format_args"])
    # Workspace scope BEFORE the prompt: agy's --print swallows the next
    # arg, so the prompt must always be last.
    if desc.get("workspace_flag"):
        argv += [desc["workspace_flag"], cwd]
    if desc.get("extra_args"):
        argv += list(desc["extra_args"])
    if desc.get("prompt_flag"):
        argv += [desc["prompt_flag"], prompt]
    else:
        # "--" forces everything after to parse as the positional message.
        # Without it, a prompt starting with "-" (e.g. our "--- MARGIN
        # DIRECTIVE ---" header) is parsed as flags and the CLI dumps help.
        argv += ["--", prompt]
    if desc.get("cwd_flag"):
        argv += [desc["cwd_flag"], cwd]
    return argv

# Agent CLIs emit terminal formatting (colors, spinners: ✗ ✱ → $).
# Strip ANSI escape sequences so chat output/history stays readable.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _summarize_tool_input(tool: str, state: dict) -> str:
    """Compact one-line summary for a tool call (name + file, no content)."""
    inp = (state or {}).get("input") or {}
    for key in ("filePath", "file_path", "path", "file", "pattern", "command"):
        val = inp.get(key)
        if val:
            s = str(val)
            return s.split("/")[-1] if "/" in s and key != "command" else s[:80]
    return ""


def _tool_event_path(state: dict) -> str:
    """Full file path from a tool input, workspace-relative when possible.

    Lets the frontend hot-refresh the sidebar when the agent writes files.
    """
    inp = (state or {}).get("input") or {}
    for key in ("filePath", "file_path", "path", "file"):
        val = inp.get(key)
        if not val:
            continue
        p = str(val)
        try:
            return str(Path(p).resolve().relative_to(storage.workspace_dir.resolve()))
        except Exception:
            return p  # already relative, or outside the workspace
    return ""


def _queue_tool(tool: str, inp: dict):
    """Build a ('tool', {...}) queue item shared by all structured parsers."""
    state = {"input": inp}
    return ("tool", {
        "tool": tool,
        "detail": _summarize_tool_input(tool, state),
        "path": _tool_event_path(state) or None,
    })


def _parse_opencode_line(line: str):
    """Parse one `opencode run --format json` line into queue items.

    text → chunk, reasoning → thinking, tool_use → tool summary,
    step_finish → usage tokens. Diffs/tool outputs are dropped: the
    editor owns diff display and tool payloads can be huge.
    """
    line = line.strip()
    if not line:
        return []
    try:
        d = json.loads(line)
    except Exception:
        return [("chunk", _strip_ansi(line))]
    part = d.get("part") or {}
    ptype = part.get("type", "")
    if ptype == "text" and part.get("text"):
        return [("chunk", part["text"])]
    if ptype == "reasoning" and part.get("text"):
        return [("thinking", part["text"])]
    if d.get("type") == "tool_use":
        tool = part.get("tool", "tool")
        state = part.get("state") or {}
        if state.get("status") == "error":
            detail = _summarize_tool_input(tool, state)
            err_text = state.get("error") or ""
            label = f"{tool}{(' ' + detail) if detail else ''} failed"
            if err_text:
                label += f": {str(err_text)[:300]}"
            return [("error_text", _strip_ansi(label))]
        # opencode nests the input under state; fall back to part.input for
        # older layouts.
        return [_queue_tool(tool, state.get("input") or part.get("input") or {})]
    if ptype == "step-finish":
        toks = (part.get("tokens") or {})
        return [("usage", {
            "prompt_tokens": toks.get("input", 0),
            "completion_tokens": toks.get("output", 0),
        })]
    # Error events (e.g. provider rate limits, auth failures) must surface —
    # otherwise failures degrade to a bare "exited with code 1".
    if d.get("type") == "error":
        err = d.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else err
        if msg:
            return [("error_text", _strip_ansi(str(msg)))]
    if "error" in ptype:
        text = part.get("text") or part.get("message") or ""
        if text:
            return [("error_text", _strip_ansi(str(text)))]
    return []


def _parse_agy_line(line: str):
    """Parse one `agy --output-format stream-json` line.

    step_update frames stream agent_response text as incremental deltas and
    report tool steps (Gemini-style parameter keys, e.g. TargetFile) — tools
    emit on their DONE frame only, so each call yields one row. The result
    frame carries final usage and failure status. init frames and non-JSON
    stderr noise are dropped.
    """
    line = line.strip()
    if not line:
        return []
    try:
        d = json.loads(line)
    except Exception:
        return []
    event = d.get("event")
    if event == "step_update":
        step = d.get("step_update") or {}
        stype = step.get("step_type", "")
        if stype == "agent_response":
            delta = step.get("text_delta") or ""
            return [("chunk", delta)] if delta else []
        if "reasoning" in stype:
            delta = step.get("text_delta") or ""
            return [("thinking", delta)] if delta else []
        if stype == "tool" and step.get("state") == "DONE":
            params = (step.get("tool_info") or {}).get("parameters") or {}
            path = ""
            # Gemini-style capitalized keys: writes use TargetFile, reads
            # (view_file) use AbsolutePath.
            for key in ("TargetFile", "AbsolutePath", "FilePath", "Path"):
                if params.get(key):
                    path = str(params[key])
                    break
            detail = path.split("/")[-1] if path else str(params.get("Command") or "")[:80]
            return [("tool", {
                "tool": step.get("tool_name", "tool"),
                "detail": detail,
                "path": _tool_event_path({"input": {"path": path}}) or (path or None),
            })]
        return []
    if event == "result":
        res = d.get("result") or {}
        items = []
        if res.get("status") not in (None, "SUCCESS"):
            resp = str(res.get("response") or "")
            if resp:
                items.append(("error_text", _strip_ansi(resp)))
        usage = res.get("usage") or {}
        if usage:
            items.append(("usage", {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
            }))
        return items
    return []


def _parse_claude_line(line: str):
    """Parse one `claude -p --output-format stream-json --verbose` line.

    assistant → text/thinking/tool_use blocks; result → usage or error.
    system/user events are dropped (init noise, tool outputs).
    Non-JSON lines are dropped: stderr is merged into stdout and carries
    the CLI's own diagnostics, not chat content.
    """
    line = line.strip()
    if not line:
        return []
    try:
        d = json.loads(line)
    except Exception:
        return []
    items = []
    if d.get("type") == "assistant":
        for block in (d.get("message") or {}).get("content") or []:
            btype = block.get("type")
            if btype == "text" and block.get("text"):
                items.append(("chunk", block["text"]))
            elif btype == "thinking" and block.get("thinking"):
                items.append(("thinking", block["thinking"]))
            elif btype == "tool_use":
                items.append(_queue_tool(block.get("name", "tool"), block.get("input") or {}))
    elif d.get("type") == "result":
        if d.get("is_error"):
            items.append(("error_text", _strip_ansi(str(d.get("result") or "Claude Code error"))))
        usage = d.get("usage") or {}
        if usage.get("input_tokens") is not None:
            items.append(("usage", {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
            }))
    return items


def _parse_codex_line(line: str, state: dict):
    """Parse one `codex exec --json` ThreadEvent line.

    item.updated/item.completed carry cumulative text for
    agent_message/reasoning items — state remembers what was already
    streamed so only deltas are emitted. file_change and
    command_execution become tool events; turn.completed carries usage.
    Non-JSON lines (merged stderr diagnostics) are dropped.
    """
    line = line.strip()
    if not line:
        return []
    try:
        d = json.loads(line)
    except Exception:
        return []
    items = []
    etype = d.get("type", "")
    if etype in ("item.updated", "item.completed"):
        item = d.get("item") or {}
        itype = item.get("type", "")
        if itype in ("agent_message", "reasoning"):
            key = f"{itype}:{item.get('id', '')}"
            text = item.get("text") or ""
            prev = state.get(key, "")
            delta = text[len(prev):] if text.startswith(prev) else text
            state[key] = text
            if delta:
                items.append(("chunk" if itype == "agent_message" else "thinking", delta))
        elif itype == "file_change" and etype == "item.completed":
            for change in item.get("changes") or []:
                p = change.get("path") or ""
                if not p:
                    continue
                items.append(("tool", {
                    "tool": change.get("kind", "edit"),
                    "detail": p.split("/")[-1],
                    "path": _tool_event_path({"input": {"path": p}}) or p,
                }))
        elif itype == "command_execution" and etype == "item.completed":
            cmd = str(item.get("command") or "")[:80]
            items.append(("tool", {"tool": "bash", "detail": cmd, "path": None}))
        elif itype == "mcp_tool_call" and etype == "item.completed":
            items.append(("tool", {"tool": item.get("tool") or "mcp", "detail": item.get("server") or "", "path": None}))
    elif etype == "turn.completed":
        toks = d.get("usage") or {}
        items.append(("usage", {
            "prompt_tokens": toks.get("input_tokens", 0),
            "completion_tokens": toks.get("output_tokens", 0),
        }))
    elif etype == "turn.failed":
        msg = ((d.get("error") or {}).get("message")) or "Codex turn failed"
        if msg != state.get("_last_err"):
            state["_last_err"] = msg
            items.append(("error_text", _strip_ansi(str(msg))))
    elif etype == "error":
        # Codex emits the same message as a bare error event AND turn.failed;
        # emit each distinct message once.
        msg = d.get("message")
        if msg and str(msg) != state.get("_last_err"):
            state["_last_err"] = str(msg)
            items.append(("error_text", _strip_ansi(str(msg))))
    return items


async def run_harness(argv: list, cwd: str, stop_event: threading.Event, queue: asyncio.Queue):
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, cwd=cwd,
                                                stdin=asyncio.subprocess.DEVNULL,
                                                env=harness_env.normalized_env(),
                                                )
    try:
        while True:
            if stop_event.is_set():
                proc.terminate()
                break
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if not chunk:
                break
            queue.put_nowait(("chunk", _strip_ansi(chunk.decode("utf-8", errors="replace"))))
        await proc.wait()
        queue.put_nowait(("done", proc.returncode))
    except Exception as e:
        queue.put_nowait(("error", e))

def _resolve_simple_assist_client() -> llm.LLMClient:
    """Return an LLMClient configured with the active endpoint from settings,
    falling back to .env defaults if no endpoint is active."""
    s = storage.get_settings()
    ep_id = s.get("active_endpoint")
    if ep_id:
        endpoints = s.get("endpoints") or {}
        ep = endpoints.get(ep_id)
        if ep:
            is_thinking = ep.get("is_thinking", True)
            custom_tags = ep.get("custom_thinking_tags") or []
            custom_open = [t["open"] for t in custom_tags if isinstance(t, dict) and "open" in t]
            custom_close = [t["close"] for t in custom_tags if isinstance(t, dict) and "close" in t]
            return llm.LLMClient(
                model=ep.get("model") or None,
                base_url=ep.get("url") or None,
                api_key=ep.get("api_key") or None,
                is_thinking=is_thinking,
                custom_opening_tags=custom_open,
                custom_closing_tags=custom_close,
            )
    is_thinking = s.get("is_thinking", True)
    return llm.LLMClient(is_thinking=is_thinking)


def _build_session_history_text(session_id: Optional[str], settings: dict) -> str:
    """Past chat turns as plain text for harness prompts.

    Harnesses take a single prompt string (not a message list), so history
    goes in as labeled text. Bounded by history_turns like the endpoint path.
    """
    if not session_id:
        return ""
    logs = storage.get_simple_ai_logs()
    filtered = [
        log for log in logs
        if log.get("session_id") == session_id
        and log.get("mode") == "chat"
        and log.get("success", True)
    ]
    filtered.sort(key=lambda x: x.get("timestamp", ""))
    turns = int(settings.get("history_turns", 5))
    recent = filtered[-turns:] if turns > 0 else []
    if not recent:
        return ""
    lines = ["PAST_CONVERSATION:"]
    for log in recent:
        lines.append(f"USER: {log.get('instruction', '')}")
        lines.append(f"ASSISTANT: {log.get('output', '')}")
    return "\n".join(lines)


def _is_blocked(filepath: str, ignored: set) -> bool:
    """Check if a file is blocked — either directly or via its folder's manifest."""
    folder = filepath.split('/')[0]
    manifest_path = f"{folder}/{folder.upper()}.md"
    if manifest_path in ignored:
        return True
    if filepath in ignored:
        return True
    return False


def _workspace_index_line() -> str:
    """One-line pointer to the workspace's manifest indexes, for harness prompts.

    Pointers, not contents: harnesses read what they need themselves, and the
    manifests tell a fresh agent where characters/chapters/styles live.
    """
    s = storage.get_settings()
    ignored = set(s.get("ignored_ref_files") or [])
    names = []
    try:
        for f in sorted(storage.workspace_dir.glob("*/*.md")):
            if f.name != f"{f.parent.name.upper()}.md":
                continue
            rel = f"{f.parent.name}/{f.name}"
            if _is_blocked(rel, ignored):
                continue
            names.append(rel)
    except Exception as e:
        print(f"Error scanning workspace indexes: {e}")
        return ""
    if not names:
        return ""
    return "Workspace indexes (read to orient yourself): " + ", ".join(names)


def _inject_pinned_ref_files(system_parts: list, already_seen: set) -> list:
    """Append pinned ref file contents to the system prompt parts list."""
    s = storage.get_settings()
    pinned = s.get("pinned_ref_files") or []
    if not pinned:
        return system_parts
    available = {f["path"]: f["name"] for f in storage.list_input_files()}
    ignored = set(s.get("ignored_ref_files") or [])
    for pp in pinned:
        if pp in already_seen:
            continue
        if _is_blocked(pp, ignored):
            continue
        if pp not in available:
            continue
        try:
            content = storage.read_input_file(pp)
        except Exception:
            continue
        label = available[pp].replace("_", " ").replace(".md", "").upper()
        system_parts.append(f"--- PINNED CONTEXT: {label} ---\n{content}")
        already_seen.add(pp)
    return system_parts


def _pick_writer_max_tokens() -> int | None:
    """Pick max_tokens from default_verbosity setting. None if token limits disabled."""
    if config.DISABLE_TOKEN_LIMITS:
        return None
    s = storage.get_settings()
    mapping = {"concise": 250, "balanced": 500, "expansive": 1000, "none": None}
    val = mapping.get(s.get("default_verbosity", "balanced"), 500)
    return val


class SimpleAssistRequest(BaseModel):
    content: str = ""
    message: str
    mode: str = "chat"
    session_id: Optional[str] = None
    history: List[Dict[str, Any]] = Field(default_factory=list)
    selected_text: Optional[str] = None
    cursor_paragraph_text: Optional[str] = None
    ref_files: Optional[List[Dict[str, Any]]] = None
    available_files: List[Dict[str, str]] = Field(default_factory=list)
    active_filename: Optional[str] = None
    skip_planner: bool = False
    harness: Optional[str] = "api" 


@router.get("/simple/logs")
def get_simple_logs():
    return storage.get_simple_ai_logs()


@router.delete("/simple/session/{session_id}")
def delete_session_logs(session_id: str):
    storage.delete_simple_ai_logs_by_session(session_id)
    return {"status": "ok"}

_active_stop_events: Dict[str, threading.Event] = {}

@router.post("/simple/stop/{session_id}")
def stop_simple_generation(session_id: str):
    event = _active_stop_events.get(session_id)
    if event:
        event.set()
    return {"status": "ok"}


def _log_simple_assist(
    mode: str,
    system_prompt: str,
    user_prompt: str,
    response: str,
    instruction: str,
    session_id: Optional[str] = None,
    selected_text: Optional[str] = None,
    text_before: Optional[str] = None,
    text_after: Optional[str] = None,
    ref_files: Optional[List[Dict[str, Any]]] = None,
    edit_mode: Optional[str] = None,
    planner_system_prompt: Optional[str] = None,
    planner_user_prompt: Optional[str] = None,
    planner_output: Optional[str] = None,
    success: bool = True,
    cursor_paragraph_index: Optional[int] = None,
    model_used: Optional[str] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    thinking_output: Optional[str] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> None:
    log_entry = {
        "id": f"simple_{uuid.uuid4().hex}",
        "timestamp": datetime.utcnow().isoformat(),
        "mode": mode,
        "session_id": session_id,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "output": response,
        "instruction": instruction,
        "selected_text": selected_text,
        "text_before": text_before,
        "text_after": text_after,
        "ref_files": ref_files,
        "success": success,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    if thinking_output is not None:
        log_entry["thinking_output"] = thinking_output
    if tool_calls:
        log_entry["tool_calls"] = tool_calls
    if edit_mode is not None:
        log_entry["edit_mode"] = edit_mode
    if planner_system_prompt is not None:
        log_entry["planner_system_prompt"] = planner_system_prompt
    if planner_user_prompt is not None:
        log_entry["planner_user_prompt"] = planner_user_prompt
    if planner_output is not None:
        log_entry["planner_output"] = planner_output
    if cursor_paragraph_index is not None:
        log_entry["cursor_paragraph_index"] = cursor_paragraph_index
    if model_used is not None:
        log_entry["model_used"] = model_used
    storage.save_simple_ai_log(log_entry)



def _get_active_context_window(settings: dict) -> int:
    active_ep = settings.get("active_endpoint")
    if active_ep:
        endpoints = settings.get("endpoints") or {}
        ep = endpoints.get(active_ep)
        if ep and ep.get("context_window"):
            try:
                return int(ep.get("context_window"))
            except ValueError:
                pass
    return int(settings.get("default_context_window") or 8192)


def _build_chat_messages(
    session_id: Optional[str],
    system_prompt: str,
    current_user_msg: str,
    settings: dict,
) -> list[dict]:
    if not session_id:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": current_user_msg}
        ]

    logs = storage.get_simple_ai_logs()
    filtered_logs = [
        log for log in logs
        if log.get("session_id") == session_id
        and log.get("mode") == "chat"
        and log.get("success", True)
    ]
    filtered_logs.sort(key=lambda x: x.get("timestamp", ""))

    history_turns = int(settings.get("history_turns", 5))
    threshold_pct = 85
    context_window = _get_active_context_window(settings)
    threshold_tokens = (threshold_pct / 100.0) * context_window

    history_pairs = filtered_logs[-history_turns:] if history_turns > 0 else []

    while True:
        messages = [{"role": "system", "content": system_prompt}]
        for log in history_pairs:
            messages.append({"role": "user", "content": log.get("instruction", "")})
            messages.append({"role": "assistant", "content": log.get("output", "")})
        messages.append({"role": "user", "content": current_user_msg})

        if not history_pairs:
            break

        # Estimate total tokens: sum(len(content) / 4.0)
        total_tokens = sum(len(msg["content"]) / 4.0 for msg in messages)
        if total_tokens <= threshold_tokens:
            break

        history_pairs.pop(0)

    return messages


def _build_planner_history(session_id: Optional[str], settings: dict) -> str:
    if not session_id:
        return ""
    
    logs = storage.get_simple_ai_logs()
    filtered_logs = [
        log for log in logs
        if log.get("session_id") == session_id
        and log.get("mode") == "edit_plan"
        and log.get("success", True)
    ]
    filtered_logs.sort(key=lambda x: x.get("timestamp", ""))
    
    history_turns = int(settings.get("history_turns", 5))
    recent_logs = filtered_logs[-history_turns:] if history_turns > 0 else []
    
    if not recent_logs:
        return ""
    
    lines = ["RECENT_EDITS:"]
    for idx, log in enumerate(recent_logs):
        instruction = log.get("instruction", "")
        pl_out = log.get("planner_output")
        refined_query = ""
        context_files = []
        if pl_out:
            try:
                if isinstance(pl_out, str):
                    try:
                        pl_dict = json.loads(pl_out)
                    except Exception:
                        start, end = pl_out.find("{"), pl_out.rfind("}") + 1
                        if start != -1 and end > start:
                            pl_dict = json.loads(pl_out[start:end])
                        else:
                            pl_dict = {}
                else:
                    pl_dict = pl_out
                context_files = pl_dict.get("context_needed", [])
                refined_query = pl_dict.get("refined_query", "")
            except Exception:
                pass
        lines.append(f"[Turn {idx + 1}] USER: \"{instruction}\" → REFINED: \"{refined_query}\" → FILES: {json.dumps(context_files)}")
    
    return "\n".join(lines)



def run_planner(
    content: str,
    message: str,
    selected_text: Optional[str] = None,
    cursor_paragraph_text: Optional[str] = None,
    session_id: Optional[str] = None,
) -> tuple[dict, str, str, str, Optional[dict], str]:
    system = _load_simple_prompt("simple-planner.md")
    
    user_prompt_lines = [f"USER_INSTRUCTION:\n{message}\n"]

    s = storage.get_settings()

    # Build document outline if enabled
    if s.get("planner_include_outline", False):
        paragraphs = [p for p in content.split('\n\n') if p.strip()]
        outline_lines = []
        for i, p in enumerate(paragraphs):
            preview = p[:60].replace('\n', ' ')
            outline_lines.append(f"[{i}] {preview}...")
        outline_text = "\n".join(outline_lines)
        user_prompt_lines.append(f"DOCUMENT_OUTLINE:\n{outline_text}\n")
    
    if selected_text:
        user_prompt_lines.append(f"SELECTED_TEXT:\n{selected_text}\n")
    elif cursor_paragraph_text:
        user_prompt_lines.append(f"ANCHOR_PARAGRAPH_TEXT:\n{cursor_paragraph_text}\n")
    
    # Inject planner history
    history_str = _build_planner_history(session_id, s)
    if history_str:
        user_prompt_lines.append(f"{history_str}\n")
    
    manifest_sections = []
    ignored_manifests = set(s.get("ignored_ref_files") or [])
    try:
        for f in storage.workspace_dir.glob("*/*.md"):
            folder_name = f.parent.name
            if f.name == f"{folder_name.upper()}.md":
                if _is_blocked(f"{folder_name}/{f.name}", ignored_manifests):
                    continue
                raw = f.read_text(encoding="utf-8")
                if raw.strip():
                    manifest_sections.append(f"--- {folder_name.upper()} ---\n{raw.strip()}")
    except Exception as e:
        print(f"Error scanning manifests: {e}")
    if manifest_sections:
        user_prompt_lines.append("AVAILABLE_CONTEXT:\n" + "\n\n".join(manifest_sections))
    
    user = "\n".join(user_prompt_lines)
    client = _resolve_simple_assist_client()
    raw = client.generate_to_completion(
        system_prompt=system,
        user_prompt=user,
        temperature=0.1,
        max_tokens=None
    )
    model_used = getattr(client, "last_model_used", client.model)
    
    try:
        return json.loads(raw), system, user, raw, client.last_usage, model_used
    except Exception:
        try:
            start, end = raw.find("{"), raw.rfind("}") + 1
            return json.loads(raw[start:end]), system, user, raw, client.last_usage, model_used
        except Exception:
            return {"context_needed": [], "refined_query": message}, system, user, raw, client.last_usage, model_used




def build_generator_prompts(
    paragraph_before: str,
    target_paragraph: str,
    paragraph_after: str,
    query: str,
    context_needed: List[str],
    available_files: List[Dict[str, str]] = None,
) -> tuple[str, str]:
    
    system_parts = [_load_simple_prompt("simple-writer.md")]
    
    available = available_files if available_files is not None else []
    available_paths = [f['path'] for f in available]
    
    s = storage.get_settings()
    ignored_paths = set(s.get("ignored_ref_files") or [])
    
    for filepath in context_needed:
        actual_path = filepath
        if actual_path not in available_paths:
            matches = difflib.get_close_matches(filepath, available_paths, n=1, cutoff=0.5)
            if matches:
                actual_path = matches[0]
            else:
                # Fallback: check if the first part of the filename matches (e.g. "kaelen")
                req_base = filepath.split('/')[-1].split('_')[0].lower()
                for p in available_paths:
                    if p.split('/')[-1].lower().startswith(req_base):
                        actual_path = p
                        break
        
        if _is_blocked(actual_path, ignored_paths):
            continue
            
        context_injector.inject(filepath, actual_path, system_parts, available_paths, s)
    already_seen = set(context_needed)
    system_parts = _inject_pinned_ref_files(system_parts, already_seen)
    
    user_parts = [
        f"PARAGRAPH_BEFORE:\n{paragraph_before}",
    ]
    if target_paragraph:
        user_parts.append(f"TARGET:\n{target_paragraph}")
        
    user_parts.append(f"PARAGRAPH_AFTER:\n{paragraph_after}")
    user_parts.append(f"INSTRUCTION:\n{query}")
    
    return "\n\n".join(system_parts), "\n\n".join(user_parts)



async def _run_planner_turn(payload: SimpleAssistRequest, edit_mode: str, loop):
    """Run the planner and log the turn. Returns (plan, system, user, raw)."""
    plan, planner_system, planner_user, planner_raw, planner_usage, planner_model = await loop.run_in_executor(
        None,
        lambda: run_planner(
            payload.content,
            payload.message,
            payload.selected_text,
            payload.cursor_paragraph_text,
            payload.session_id,
        )
    )

    await loop.run_in_executor(
        None,
        lambda: _log_simple_assist(
            mode="edit_plan",
            session_id=payload.session_id,
            system_prompt=planner_system,
            user_prompt=planner_user,
            response=planner_raw,
            instruction=payload.message,
            selected_text=payload.selected_text,
            edit_mode=edit_mode,
            success=True,
            model_used=planner_model,
            planner_output=planner_raw,
            prompt_tokens=planner_usage.get("prompt_tokens", 0) if planner_usage else 0,
            completion_tokens=planner_usage.get("completion_tokens", 0) if planner_usage else 0,
            total_tokens=planner_usage.get("total_tokens", 0) if planner_usage else 0,
        )
    )
    return plan, planner_system, planner_user, planner_raw


def _compose_chat_prompts(payload: SimpleAssistRequest, message: str):
    """Build (full_system, user_message, settings) shared by endpoint + harness chat."""
    full_system = _load_simple_prompt("simple-chat.md")

    settings = storage.get_settings()
    history_str = _build_planner_history(payload.session_id, settings)
    if history_str:
        full_system += f"\n\n{history_str}"

    if payload.content:
        if payload.active_filename:
            full_system += f"\n\nHere is the file the user is currently viewing: {payload.active_filename}\n{payload.content}"
        else:
            full_system += f"\n\nHere is the user's document for context:\n{payload.content}"

    user_message = message
    if payload.selected_text:
        user_message = f"SELECTED_TEXT:\n{payload.selected_text}\n\nUSER_MESSAGE:\n{user_message}"
    elif payload.cursor_paragraph_text:
        user_message = f"ANCHOR_PARAGRAPH_TEXT:\n{payload.cursor_paragraph_text}\n\nUSER_MESSAGE:\n{user_message}"

    return full_system, user_message, settings


@router.post("/simple")
async def simple_assist(payload: SimpleAssistRequest):

    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Missing message")

    mode = payload.mode.strip().lower()

    async def event_generator():
        planner_system = None
        planner_user = None
        planner_raw = None
        system_prompt = ""
        user_prompt = ""
        edit_mode = "replace" if payload.selected_text else "insert"
        stop_event = threading.Event()
        
        if payload.session_id:
            _active_stop_events[payload.session_id] = stop_event

        try:
            if payload.harness and payload.harness not in ("api", "none"):
                yield {"data": json.dumps({"status": "generating"})}
                # Resolve the active file first and flush the editor buffer
                # so the agent operates on fresh file state.
                active_path = None
                if payload.active_filename:
                    active_path = next(
                        (f.get("path") for f in (payload.available_files or [])
                         if f.get("name") == payload.active_filename),
                        None,
                    )
                    if active_path:
                        try:
                            storage.update_input_file(active_path, payload.content)
                        except Exception as e:
                            print(f"Harness pre-flush failed for {active_path}: {e}")
                loop = asyncio.get_running_loop()
                if mode == "edit":
                    # Harnesses plan and fetch context themselves: the endpoint
                    # planner is SLM plumbing (JSON schema drills, file
                    # selection) and the writer template is a splice contract
                    # for text the backend inserts — neither fits an agent that
                    # edits files directly. Minimal briefing instead: the
                    # user's referent, the instruction, and index pointers.
                    yield {"data": json.dumps({
                        "status": "context_resolved",
                        "context_needed": []
                    })}
                    user_parts = []
                    if active_path:
                        user_parts.append(f"ACTIVE_FILE: {active_path}")
                    if payload.selected_text:
                        user_parts.append(f"SELECTED_TEXT:\n{payload.selected_text}")
                    elif payload.cursor_paragraph_text:
                        user_parts.append(f"ANCHOR_PARAGRAPH_TEXT:\n{payload.cursor_paragraph_text}")
                    user_parts.append(f"INSTRUCTION:\n{message}")
                    # Past work, bounded by history_turns: prior edits plus
                    # prior chat turns, so follow-ups ("do that again, but…")
                    # resolve without re-explaining.
                    hist_settings = storage.get_settings()
                    edits_hist = _build_planner_history(payload.session_id, hist_settings)
                    if edits_hist:
                        user_parts.append(edits_hist)
                    past_conv = _build_session_history_text(payload.session_id, hist_settings)
                    if past_conv:
                        user_parts.append(past_conv)
                    index_line = _workspace_index_line()
                    if index_line:
                        user_parts.append(index_line)
                    user_prompt = "\n\n".join(user_parts)
                    # Standing instructions (the deliverable — edit files, not
                    # reply — and taste) live in prompts/harness-edit.md,
                    # editable under Settings > Context. Without them agents
                    # answer with prose instead of editing (12 reads, 0 edits
                    # observed on opencode build).
                    system_prompt = _load_simple_prompt("harness-edit.md").strip()
                    harness_prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
                else:
                    # Same chat composition as the endpoint path, plus a
                    # pointer to the workspace indexes: endpoint chat has no
                    # tools to follow them, a harness does.
                    full_system, user_message, _ = _compose_chat_prompts(payload, message)
                    system_prompt, user_prompt = full_system, user_message
                    parts = [full_system]
                    index_line = _workspace_index_line()
                    if index_line:
                        parts.append(index_line)
                    parts.append(f"--- USER MESSAGE ---\n{user_message}")
                    harness_prompt = "\n\n".join(parts)
                # Absolute: harnesses scope their writable workspace to the
                # path we pass (agy --add-dir treats a missing/broken dir as
                # "no writable workspace" and edits fall back to its scratch).
                workspace = str(storage.workspace_dir.resolve())
                argv = _resolve_harness_argv(
                    payload.harness, harness_prompt, workspace, mode=mode
                )
                loop = asyncio.get_running_loop()
                hqueue: asyncio.Queue = asyncio.Queue()
                stream = HARNESS_DESCRIPTORS[payload.harness].get("stream")
                codex_state: dict = {}
                loop.create_task(run_harness(argv, workspace, stop_event, hqueue))
                full_harness_output = ""
                full_harness_thinking = ""
                harness_returncode = 0
                tool_calls: list = []
                usage_prompt = 0
                usage_completion = 0
                line_buf = ""
                # All four harnesses emit JSON lines; the text branch is a
                # safety fallback for stream formats we haven't wired. Chunk
                # boundaries don't align with lines, so buffer until \n.
                while True:
                    msg_type, val = await hqueue.get()
                    if msg_type == "chunk":
                        if not stream:
                            full_harness_output += _strip_ansi(val)
                            yield {"data": json.dumps({"status": "chunk", "chunk": _strip_ansi(val)})}
                        else:
                            line_buf += val
                            *complete, line_buf = line_buf.split("\n")
                            for line in complete:
                                if stream == "codex":
                                    events = _parse_codex_line(line, codex_state)
                                elif stream == "claude":
                                    events = _parse_claude_line(line)
                                elif stream == "agy":
                                    events = _parse_agy_line(line)
                                else:
                                    events = _parse_opencode_line(line)
                                for qtype, qval in events:
                                    if qtype == "chunk":
                                        full_harness_output += qval
                                        yield {"data": json.dumps({"status": "chunk", "chunk": qval})}
                                    elif qtype == "thinking":
                                        full_harness_thinking += qval
                                        yield {"data": json.dumps({"status": "thinking_chunk", "chunk": qval})}
                                    elif qtype == "tool":
                                        tool_calls.append(qval)
                                        tool_evt = {"status": "tool", "tool": qval["tool"], "detail": qval["detail"]}
                                        if qval.get("path"):
                                            tool_evt["path"] = qval["path"]
                                        yield {"data": json.dumps(tool_evt)}
                                    elif qtype == "usage":
                                        usage_prompt += qval.get("prompt_tokens", 0)
                                        usage_completion += qval.get("completion_tokens", 0)
                                    elif qtype == "error_text":
                                        full_harness_output += ("\n" if full_harness_output else "") + qval
                                        yield {"data": json.dumps({"status": "chunk", "chunk": ("\n" + qval)})}
                    elif msg_type == "done":
                        harness_returncode = val if isinstance(val, int) else 0
                        break
                    elif msg_type == "error":
                        raise val
                if harness_returncode != 0:
                    tail_lines = full_harness_output.strip().splitlines()[-5:]
                    tail = "\n".join(tail_lines).strip() or "no output captured"
                    raise RuntimeError(
                        f"{HARNESS_DESCRIPTORS[payload.harness]['name']} exited with code {harness_returncode}. Last output:\n{tail}"
                    )
                s = storage.get_settings()
                harness_model = ((s.get("harnesses") or {}).get(payload.harness) or {}).get("model")
                # Any harness that reported no usage: fall back to a len/4
                # estimate so token counts are never blank. Documented in
                # docs/configuration/harnesses.md.
                if usage_prompt == 0 and harness_prompt:
                    usage_prompt = max(1, len(harness_prompt) // 4)
                if usage_completion == 0 and full_harness_output:
                    usage_completion = max(1, len(full_harness_output) // 4)
                await loop.run_in_executor(
                    None,
                    lambda: _log_simple_assist(
                        mode="chat" if mode == "chat" else "edit_write",
                        session_id=payload.session_id,
                        # The CLI invocation itself carries model/agent/
                        # workspace; the full prompt is logged as user_prompt.
                        system_prompt=" ".join(
                            a if len(a) < 60 else a[:60] + "…" for a in argv
                        ),
                        user_prompt=harness_prompt,
                        response=full_harness_output,
                        instruction=payload.message,
                        selected_text=payload.selected_text,
                        ref_files=payload.ref_files,
                        success=True,
                        model_used=f"{payload.harness}:{harness_model or 'default'}",
                        prompt_tokens=usage_prompt,
                        completion_tokens=usage_completion,
                        total_tokens=usage_prompt + usage_completion,
                        thinking_output=full_harness_thinking or None,
                        tool_calls=tool_calls or None,
                    )
                )
                yield {"data": json.dumps({"status": "harness_done", "harness": payload.harness})}
                return

            if mode == "edit":
                if not payload.skip_planner:
                    yield {"data": json.dumps({"status": "planning"})}

                    loop = asyncio.get_running_loop()
                    plan, planner_system, planner_user, planner_raw = await _run_planner_turn(payload, edit_mode, loop)

                    context_needed = plan.get("context_needed", [])
                    query = plan.get("refined_query") or payload.message
                else:
                    context_needed = []
                    query = payload.message

                yield {"data": json.dumps({
                    "status": "context_resolved",
                    "context_needed": context_needed
                })}

                yield {"data": json.dumps({"status": "generating"})}

                paragraph_before, target_paragraph, paragraph_after, resolved_idx, replace = extract_anchor_context(
                    payload.content, payload.selected_text, payload.cursor_paragraph_text
                )

                system_prompt, user_prompt = build_generator_prompts(
                    paragraph_before, target_paragraph, paragraph_after, query, context_needed, payload.available_files
                )

                max_toks = _pick_writer_max_tokens()
                client = _resolve_simple_assist_client()

                queue = asyncio.Queue()
                loop = asyncio.get_running_loop()

                def run_writer_stream():
                    try:
                        gen = client.generate(
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            stream=True,
                            temperature=0.7,
                            max_tokens=max_toks,
                            stop_event=stop_event
                        )
                        for chunk_type, chunk_text in gen:
                            if stop_event.is_set():
                                break
                            loop.call_soon_threadsafe(queue.put_nowait, (chunk_type, chunk_text))
                        loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
                    except Exception as e:
                        loop.call_soon_threadsafe(queue.put_nowait, ("error", e))

                # Start streaming in executor thread
                loop.run_in_executor(None, run_writer_stream)

                full_raw = ""
                full_thinking = ""
                while True:
                    msg_type, val = await queue.get()
                    if msg_type == "chunk":
                        full_raw += val
                        yield {"data": json.dumps({"status": "chunk", "chunk": val})}
                    elif msg_type == "thinking":
                        full_thinking += val
                        yield {"data": json.dumps({"status": "thinking_chunk", "chunk": val})}
                    elif msg_type == "done":
                        break
                    elif msg_type == "error":
                        raise val

                actual_model = getattr(client, "last_model_used", client.model)
                writer_usage = client.last_usage or {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }

                clean_raw = full_raw.strip()
                if clean_raw.startswith("```"):
                    lines = clean_raw.splitlines()
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    clean_raw = "\n".join(lines).strip()

                # Belt-and-suspenders: strip any reasoning tags that leaked into the stream
                writer_output = client._clean_reasoning(clean_raw)

                await loop.run_in_executor(
                    None,
                    lambda: _log_simple_assist(
                        mode="edit_write",
                        session_id=payload.session_id,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        response=full_raw,
                        instruction=payload.message,
                        selected_text=payload.selected_text,
                        text_before=paragraph_before,
                        text_after=paragraph_after,
                        ref_files=payload.ref_files,
                        edit_mode="replace" if replace else "insert",
                        planner_system_prompt=planner_system,
                        planner_user_prompt=planner_user,
                        planner_output=planner_raw,
                        success=True,
                        cursor_paragraph_index=resolved_idx,
                        model_used=actual_model,
                        prompt_tokens=writer_usage.get("prompt_tokens", 0),
                        completion_tokens=writer_usage.get("completion_tokens", 0),
                        total_tokens=writer_usage.get("total_tokens", 0),
                        thinking_output=full_thinking or None,
                    )
                )

                yield {"data": json.dumps({
                    "status": "applied",
                    "output": writer_output,
                    "cursor_paragraph_index": resolved_idx,
                    "model_used": actual_model
                })}

            else:  # chat
                yield {"data": json.dumps({"status": "generating"})}

                system_prompt = _load_simple_prompt("simple-chat.md")
                client = _resolve_simple_assist_client()
                full_system, user_message, settings = _compose_chat_prompts(payload, message)

                messages = _build_chat_messages(payload.session_id, full_system, user_message, settings)

                user_prompt = ""
                for msg in messages:
                    if msg["role"] == "user":
                        user_prompt += f"User: {msg['content']}\n\n"
                    elif msg["role"] == "assistant":
                        user_prompt += f"Assistant: {msg['content']}\n\n"
                user_prompt = user_prompt.strip()

                system_prompt = full_system

                loop = asyncio.get_running_loop()
                queue = asyncio.Queue()

                def run_chat_stream():
                    try:
                        gen = client.generate_stream_with_history(
                            messages=messages,
                            temperature=0.7,
                            max_tokens=_pick_writer_max_tokens(),
                            stop_event=stop_event
                        )
                        for chunk_type, chunk_text in gen:
                            if stop_event.is_set():
                                break
                            loop.call_soon_threadsafe(queue.put_nowait, (chunk_type, chunk_text))
                        loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
                    except Exception as e:
                        loop.call_soon_threadsafe(queue.put_nowait, ("error", e))

                # Start streaming in executor thread
                loop.run_in_executor(None, run_chat_stream)

                full_chat = ""
                full_thinking = ""
                while True:
                    msg_type, val = await queue.get()
                    if msg_type == "chunk":
                        full_chat += val
                        yield {"data": json.dumps({"status": "chunk", "chunk": val})}
                    elif msg_type == "thinking":
                        full_thinking += val
                        yield {"data": json.dumps({"status": "thinking_chunk", "chunk": val})}
                    elif msg_type == "done":
                        break
                    elif msg_type == "error":
                        raise val

                actual_model = getattr(client, "last_model_used", client.model)
                chat_usage = client.last_usage or {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }
                
                await loop.run_in_executor(
                    None,
                    lambda: _log_simple_assist(
                        mode="chat",
                        session_id=payload.session_id,
                        system_prompt=full_system,
                        user_prompt=user_prompt,
                        response=full_chat,
                        instruction=message,
                        ref_files=payload.ref_files,
                        selected_text=payload.selected_text,
                        success=True,
                        model_used=actual_model,
                        prompt_tokens=chat_usage.get("prompt_tokens", 0),
                        completion_tokens=chat_usage.get("completion_tokens", 0),
                        total_tokens=chat_usage.get("total_tokens", 0),
                        thinking_output=full_thinking or None,
                    )
                )

                yield {"data": json.dumps({
                    "status": "chat",
                    "output": full_chat,
                    "model_used": actual_model
                })}
        except Exception as e:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda: _log_simple_assist(
                        mode=mode,
                        session_id=payload.session_id,
                        system_prompt=system_prompt or "",
                        user_prompt=user_prompt or payload.message,
                        response=f"Error: {str(e)}",
                        instruction=payload.message,
                        selected_text=payload.selected_text,
                        text_before=locals().get('paragraph_before', None),
                        text_after=locals().get('paragraph_after', None),
                        ref_files=payload.ref_files,
                        edit_mode=edit_mode if mode == "edit" else None,
                        success=False,
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                    )
                )
            except Exception as log_ex:
                print(f"Failed to log error simple assist: {log_ex}")

            yield {"data": json.dumps({
                "status": "error",
                "detail": str(e)
            })}
        finally:
            if payload.session_id and payload.session_id in _active_stop_events:
                del _active_stop_events[payload.session_id]

    return EventSourceResponse(event_generator())


class PromptSaveRequest(BaseModel):
    content: str


@router.get("/prompts")
def list_prompts():
    """Retrieve all available markdown prompt files from the prompts directory."""
    prompts_dir = Path(__file__).parent.parent.parent / "prompts"
    if not prompts_dir.exists():
        return []

    files = []
    for f in prompts_dir.glob("*.md"):
        files.append({"name": f.name, "path": f.name})
    return sorted(files, key=lambda x: x["name"])


@router.get("/prompts/{filename}")
def get_prompt_content(filename: str):
    """Retrieve the content of a specific markdown prompt file."""
    prompts_dir = Path(__file__).parent.parent.parent / "prompts"
    file_path = prompts_dir / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Prompt file not found")

    return {"content": file_path.read_text(encoding="utf-8")}


@router.post("/prompts/{filename}")
def save_prompt_content(filename: str, payload: PromptSaveRequest):
    """Save the content of a specific markdown prompt file. Creates file and directories if they don't exist."""
    prompts_dir = Path(__file__).parent.parent.parent / "prompts"
    file_path = prompts_dir / filename

    try:
        prompts_dir.mkdir(parents=True, exist_ok=True)
        file_path.write_text(payload.content, encoding="utf-8")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save prompt: {str(e)}")

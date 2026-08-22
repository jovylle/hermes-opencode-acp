"""OpenCode-compatible shim that forwards Hermes requests to `opencode acp`.

This adapter lets Hermes treat the OpenCode ACP server as a chat-style
backend. Each request starts a short-lived ACP session, sends the formatted
conversation as a single prompt, collects text chunks, and converts the result
back into the minimal shape Hermes expects from an OpenAI client.

Wire protocol: JSON-RPC 2.0 over stdio (Agent Client Protocol v1).
OpenCode's `opencode acp` speaks the same protocol as Copilot's
`copilot --acp --stdio`, so this client mirrors copilot_acp_client.py
with provider-specific adjustments.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shlex
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from agent.file_safety import get_read_block_error, get_write_denied_error, is_write_approval_required
from agent.redact import redact_sensitive_text
from tools.environments.local import hermes_subprocess_env

ACP_MARKER_BASE_URL = "acp://opencode"
_DEFAULT_TIMEOUT_SECONDS = 900.0

_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_TOOL_CALL_JSON_RE = re.compile(r"\{\s*\"id\"\s*:\s*\"[^\"]+\"\s*,\s*\"type\"\s*:\s*\"function\"\s*,\s*\"function\"\s*:\s*\{.*?\}\s*\}", re.DOTALL)
# OpenCode's OWN tool-call serialization (what `opencode acp` actually emits):
#   <tool_call>
#   <parameter=arguments>{"city":"Tokyo"}</parameter>
#   <parameter=name>get_weather</parameter>
#   </function>            <- stray closer, opencode emits it without an opener
#   </tool_call>
# The <parameter=name>/<parameter=arguments> tags may appear in either order.
_TOOL_CALL_OPENCODE_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_OPENCODE_PARAM_NAME_RE = re.compile(r"<parameter=name>(.*?)</parameter>", re.DOTALL)
_OPENCODE_PARAM_ARGS_RE = re.compile(r"<parameter=arguments>(.*?)</parameter>", re.DOTALL)


def _resolve_command() -> str:
    return (
        os.getenv("HERMES_OPENCODE_ACP_COMMAND", "").strip()
        or os.getenv("OPENCODE_BIN", "").strip()
        or "opencode"
    )


def _resolve_args() -> list[str]:
    raw = os.getenv("HERMES_OPENCODE_ACP_ARGS", "").strip()
    if not raw:
        return ["acp"]
    return shlex.split(raw)


def _resolve_home_dir() -> str:
    home = os.environ.get("HOME", "").strip()
    if home:
        return home
    expanded = os.path.expanduser("~")
    if expanded and expanded != "~":
        return expanded
    try:
        import pwd
        resolved = pwd.getpwuid(os.getuid()).pw_dir.strip()
        if resolved:
            return resolved
    except Exception:
        pass
    return "/tmp"


def _build_subprocess_env(model: str | None = None) -> dict[str, str]:
    env = hermes_subprocess_env(inherit_credentials=True)
    home = _resolve_home_dir()
    env["HOME"] = home
    from hermes_constants import apply_subprocess_home_env
    apply_subprocess_home_env(env)
    # Pass model to OpenCode via env var. OpenCode resolves its model
    # from config at startup; OPENCODE_MODEL overrides the config default.
    # Strip "opencode/" prefix if present (e.g. "opencode/deepseek-v4-flash"
    # → "deepseek-v4-flash") since OpenCode's env var expects bare model names.
    if model:
        bare = model
        if bare.lower().startswith("opencode/"):
            bare = bare[len("opencode/"):]
        env["OPENCODE_MODEL"] = bare
    return env


def _jsonrpc_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _permission_denied(message_id: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "result": {
            "outcome": {
                "outcome": "cancelled",
            }
        },
    }


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text (roughly 4 chars per token for English)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _format_messages_as_prompt(
    messages: list[dict[str, Any]],
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
) -> str:
    """Format the FULL conversation as a single ACP prompt.

    Used for first-turn / cold-start where OpenCode has no session context.
    For persistent sessions, use _format_incremental_prompt() instead.
    """
    sections: list[str] = [
        "You are being used as the active ACP agent backend for Hermes.",
        "Use ACP capabilities to complete tasks.",
        "IMPORTANT: If you take an action with a tool, you MUST output a tool call as a <tool_call> block — EITHER OpenAI shape (<tool_call>{...json...}</tool_call> with id/type/function{name,arguments}) OR your native format (<tool_call><parameter=name>fn</parameter><parameter=arguments>{json}</parameter></tool_call>). The host parses both.",
        "If no tool is needed, answer normally.",
    ]
    if model:
        sections.append(f"Hermes requested model hint: {model}")

    if isinstance(tools, list) and tools:
        tool_specs: list[dict[str, Any]] = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            fn = t.get("function") or {}
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            tool_specs.append(
                {
                    "name": name.strip(),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                }
            )
        if tool_specs:
            sections.append(
                "Available tools (OpenAI function schema). "
                "When using a tool, emit ONLY a <tool_call> block: "
                "<tool_call>{json}</tool_call> with id/type/function{name,arguments} "
                "(arguments must be a JSON string), or your native "
                "<tool_call><parameter=name>fn</parameter><parameter=arguments>{json}</parameter></tool_call>.\n"
                + json.dumps(tool_specs, ensure_ascii=False)
            )

    if tool_choice is not None:
        sections.append(f"Tool choice hint: {json.dumps(tool_choice, ensure_ascii=False)}")

    transcript = _render_transcript(messages)
    if transcript:
        sections.append("Conversation transcript:\n\n" + transcript)
    sections.append("Continue the conversation from the latest user request.")
    return "\n\n".join(section.strip() for section in sections if section and section.strip())


def _render_transcript(messages: list[dict[str, Any]]) -> str:
    """Render a message list as a labeled transcript for ACP prompting.

    Assistant tool calls are rendered inline so OpenCode can see which
    function it requested even when the content is empty.
    """
    transcript: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown").strip().lower()
        if role not in {"system", "user", "assistant", "tool"}:
            role = "context"
        rendered = _render_message_content(message.get("content"))
        parts: list[str] = []
        if rendered:
            parts.append(rendered)
        if role == "assistant":
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                    if not isinstance(fn, dict):
                        continue
                    name = fn.get("name")
                    if not isinstance(name, str) or not name.strip():
                        continue
                    args = fn.get("arguments")
                    if not isinstance(args, str):
                        args = json.dumps(args, ensure_ascii=False)
                    parts.append(f"Tool call: {name}({args})")
        if not parts:
            continue
        label = {
            "system": "System",
            "user": "User",
            "assistant": "Assistant",
            "tool": "Tool",
            "context": "Context",
        }.get(role, role.title())
        transcript.append(f"{label}:\n" + "\n".join(parts))
    return "\n\n".join(transcript)


def _format_incremental_prompt(messages: list[dict[str, Any]]) -> str:
    """Build the minimal prompt for a persistent ACP session.

    - No tool roundtrip pending: return just the latest user text — the
      OpenCode session already holds everything before it.
    - Tool roundtrip pending (assistant tool_calls and/or tool results
      AFTER the latest user message): return a compact transcript of the
      tail so OpenCode sees what it called and what the tool returned.
    - No user message at all: return "" so the caller falls back to the
      full-conversation format.
    """
    last_user_idx = -1
    for i, message in enumerate(messages):
        if isinstance(message, dict) and str(message.get("role") or "").lower() == "user":
            last_user_idx = i
    if last_user_idx == -1:
        return ""
    tail = messages[last_user_idx:]
    if len(tail) == 1:
        return _render_message_content(tail[0].get("content"))
    return _render_transcript(tail)


def _render_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "").strip()
        if "content" in content and isinstance(content.get("content"), str):
            return str(content.get("content") or "").strip()
        return json.dumps(content, ensure_ascii=True)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return str(content).strip()


def _build_openai_tool_call(
    *,
    call_id: str,
    name: str,
    arguments: str,
) -> ChatCompletionMessageToolCall:
    return ChatCompletionMessageToolCall(
        id=call_id,
        call_id=call_id,
        response_item_id=None,
        type="function",
        function=Function(name=name, arguments=arguments),
    )


def _completion_to_stream_chunks(completion: SimpleNamespace) -> list[SimpleNamespace]:
    choice = completion.choices[0]
    message = choice.message
    tool_call_deltas = None
    if message.tool_calls:
        tool_call_deltas = []
        for index, tool_call in enumerate(message.tool_calls):
            tool_call_deltas.append(
                SimpleNamespace(
                    index=index,
                    id=getattr(tool_call, "id", None),
                    type=getattr(tool_call, "type", "function"),
                    function=SimpleNamespace(
                        name=getattr(tool_call.function, "name", None),
                        arguments=getattr(tool_call.function, "arguments", None),
                    ),
                )
            )

    delta = SimpleNamespace(
        role="assistant",
        content=message.content or None,
        tool_calls=tool_call_deltas,
        reasoning_content=message.reasoning_content,
        reasoning=message.reasoning,
    )
    data_chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                delta=delta,
                finish_reason=choice.finish_reason,
            )
        ],
        model=completion.model,
        usage=None,
    )
    usage_chunk = SimpleNamespace(
        choices=[],
        model=completion.model,
        usage=completion.usage,
    )
    return [data_chunk, usage_chunk]


def _extract_tool_calls_from_text(text: str) -> tuple[list[ChatCompletionMessageToolCall], str]:
    if not isinstance(text, str) or not text.strip():
        return [], ""

    extracted: list[ChatCompletionMessageToolCall] = []
    consumed_spans: list[tuple[int, int]] = []

    def _try_add_tool_call(raw_json: str) -> bool:
        try:
            obj = json.loads(raw_json)
        except Exception:
            return False
        if not isinstance(obj, dict):
            return False
        fn = obj.get("function")
        if not isinstance(fn, dict):
            return False
        fn_name = fn.get("name")
        if not isinstance(fn_name, str) or not fn_name.strip():
            return False
        fn_args = fn.get("arguments", "{}")
        if not isinstance(fn_args, str):
            fn_args = json.dumps(fn_args, ensure_ascii=False)
        call_id = obj.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = f"acp_call_{len(extracted)+1}"
        extracted.append(
            _build_openai_tool_call(
                call_id=call_id,
                name=fn_name.strip(),
                arguments=fn_args,
            )
        )
        return True

    def _try_add_opencode_call(block: str) -> bool:
        """Parse one opencode-native <tool_call>…</tool_call> block."""
        name_m = _OPENCODE_PARAM_NAME_RE.search(block)
        fn_name = name_m.group(1).strip() if name_m else ""
        if not fn_name:
            return False
        args_m = _OPENCODE_PARAM_ARGS_RE.search(block)
        fn_args = args_m.group(1).strip() if args_m else "{}"
        call_id = f"acp_call_{len(extracted)+1}"
        extracted.append(
            _build_openai_tool_call(
                call_id=call_id,
                name=fn_name,
                arguments=fn_args,
            )
        )
        return True

    # Pass 1: OpenAI JSON shape — <tool_call>{...}</tool_call>
    for m in _TOOL_CALL_BLOCK_RE.finditer(text):
        if _try_add_tool_call(m.group(1)):
            consumed_spans.append((m.start(), m.end()))

    # Pass 1b: bare OpenAI-shape JSON object without the wrapper tags
    if not extracted:
        for m in _TOOL_CALL_JSON_RE.finditer(text):
            if _try_add_tool_call(m.group(0)):
                consumed_spans.append((m.start(), m.end()))

    # Pass 2: opencode-native <parameter=name>/<parameter=arguments> XML.
    # Runs even when pass 1 found calls (a mixed transcript can have both).
    for m in _TOOL_CALL_OPENCODE_RE.finditer(text):
        if _try_add_opencode_call(m.group(1)):
            consumed_spans.append((m.start(), m.end()))

    if not consumed_spans:
        return extracted, text.strip()

    consumed_spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in consumed_spans:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))

    parts: list[str] = []
    cursor = 0
    for start, end in merged:
        if cursor < start:
            parts.append(text[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(text):
        parts.append(text[cursor:])

    cleaned = "\n".join(p.strip() for p in parts if p and p.strip()).strip()
    return extracted, cleaned


def _ensure_path_within_cwd(path_text: str, cwd: str) -> Path:
    candidate = Path(path_text)
    if not candidate.is_absolute():
        raise PermissionError("ACP file-system paths must be absolute.")
    resolved = candidate.resolve()
    root = Path(cwd).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"Path '{resolved}' is outside the session cwd '{root}'.") from exc
    return resolved


class _ACPChatCompletions:
    def __init__(self, client: "OpenCodeACPClient"):
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        return self._client._create_chat_completion(**kwargs)


class _ACPChatNamespace:
    def __init__(self, client: "OpenCodeACPClient"):
        self.completions = _ACPChatCompletions(client)


class OpenCodeACPClient:
    """Minimal OpenAI-client-compatible facade for OpenCode ACP."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
        acp_command: str | None = None,
        acp_args: list[str] | None = None,
        acp_cwd: str | None = None,
        acp_model: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        model: str | None = None,
        **_: Any,
    ):
        self.api_key = api_key or "opencode-acp"
        self.base_url = base_url or ACP_MARKER_BASE_URL
        self._default_headers = dict(default_headers or {})
        self._acp_command = acp_command or command or _resolve_command()
        self._acp_args = list(acp_args or args or _resolve_args())
        self._acp_cwd = str(Path(acp_cwd or os.getcwd()).resolve())
        # Model for this ACP session. Accepts acp_model (explicit),
        # model (from fallback chain / client_kwargs), or None (use
        # OpenCode's config default). Stored as "opencode/<name>" or
        # bare "<name>" — the bare form is passed to OpenCode via env.
        self._acp_model = (acp_model or model or "").strip() or None
        self.chat = _ACPChatNamespace(self)
        self.is_closed = False
        # ── Persistent session state ──
        self._proc: subprocess.Popen[str] | None = None
        self._proc_lock = threading.Lock()
        self._inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._next_id: int = 0
        self._session_id: str | None = None
        self._initialized: bool = False

    def close(self) -> None:
        proc: subprocess.Popen[str] | None
        with self._proc_lock:
            proc = self._proc
            self._proc = None
            self._session_id = None
            self._initialized = False
        self.is_closed = True
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _create_chat_completion(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        stream: bool = False,
        **_: Any,
    ) -> Any:
        # For persistent sessions (process already alive), send ONLY the
        # latest user message — OpenCode's session context has the rest.
        # For cold starts, send the full transcript (includes system prompt).
        if self._initialized and self._session_id:
            incremental = _format_incremental_prompt(messages or [])
            if incremental:
                prompt_text = incremental
            else:
                # No user message found — fall back to full format.
                prompt_text = _format_messages_as_prompt(
                    messages or [],
                    model=model,
                    tools=tools,
                    tool_choice=tool_choice,
                )
        else:
            prompt_text = _format_messages_as_prompt(
                messages or [],
                model=model,
                tools=tools,
                tool_choice=tool_choice,
            )
        if timeout is None:
            _effective_timeout = _DEFAULT_TIMEOUT_SECONDS
        elif isinstance(timeout, (int, float)):
            _effective_timeout = float(timeout)
        else:
            _candidates = [
                getattr(timeout, attr, None)
                for attr in ("read", "write", "connect", "pool", "timeout")
            ]
            _numeric = [float(v) for v in _candidates if isinstance(v, (int, float))]
            _effective_timeout = max(_numeric) if _numeric else _DEFAULT_TIMEOUT_SECONDS

        response_text, reasoning_text = self._run_prompt(
            prompt_text,
            timeout_seconds=_effective_timeout,
        )

        tool_calls, cleaned_text = _extract_tool_calls_from_text(response_text)

        # Estimate token usage (ACP protocol doesn't return usage data)
        prompt_tokens = _estimate_tokens(prompt_text)
        completion_tokens = _estimate_tokens(cleaned_text) + _estimate_tokens(reasoning_text)
        usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        )
        assistant_message = SimpleNamespace(
            content=cleaned_text,
            tool_calls=tool_calls,
            reasoning=reasoning_text or None,
            reasoning_content=reasoning_text or None,
            reasoning_details=None,
        )
        finish_reason = "tool_calls" if tool_calls else "stop"
        choice = SimpleNamespace(message=assistant_message, finish_reason=finish_reason)
        completion = SimpleNamespace(
            choices=[choice],
            usage=usage,
            model=model or "opencode-acp",
        )
        if stream:
            return _completion_to_stream_chunks(completion)
        return completion

    def _ensure_process(self, *, timeout_seconds: float) -> None:
        """Spawn opencode ACP process if not running; do handshake + session/new.

        The process persists across calls — this is what makes the 1:1
        Hermes-session → OpenCode-session mapping work.  If the process
        died mid-session, we respawn and re-initialize automatically.
        """
        with self._proc_lock:
            # Already alive and initialized — nothing to do.
            if self._proc is not None and self._proc.poll() is None and self._initialized:
                return

            # Tear down stale state if the process died.
            if self._proc is not None and self._proc.poll() is not None:
                self._proc = None
                self._initialized = False
                self._session_id = None

            if self._initialized:
                return

            # ── Spawn ──
            try:
                from hermes_cli._subprocess_compat import windows_hide_flags
                proc = subprocess.Popen(
                    [self._acp_command] + self._acp_args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', errors='replace',
                    bufsize=1,
                    cwd=self._acp_cwd,
                    env=_build_subprocess_env(model=self._acp_model),
                    creationflags=windows_hide_flags(),
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"Could not start OpenCode ACP command '{self._acp_command}'. "
                    "Install OpenCode CLI or set HERMES_OPENCODE_ACP_COMMAND/OPENCODE_BIN."
                ) from exc

            if proc.stdin is None or proc.stdout is None:
                proc.kill()
                raise RuntimeError("OpenCode ACP process did not expose stdin/stdout pipes.")

            self._proc = proc
            self.is_closed = False

            # Reset per-process state.
            self._inbox = queue.Queue()
            self._stderr_tail = deque(maxlen=40)
            self._next_id = 0

            def _stdout_reader() -> None:
                if proc.stdout is None:
                    return
                for line in proc.stdout:
                    try:
                        self._inbox.put(json.loads(line))
                    except Exception:
                        self._inbox.put({"raw": line.rstrip("\n")})

            def _stderr_reader() -> None:
                if proc.stderr is None:
                    return
                for line in proc.stderr:
                    self._stderr_tail.append(line.rstrip("\n"))

            self._stdout_thread = threading.Thread(target=_stdout_reader, daemon=True)
            self._stderr_thread = threading.Thread(target=_stderr_reader, daemon=True)
            self._stdout_thread.start()
            self._stderr_thread.start()

            # ── ACP handshake ──
            self._send_request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "fs": {
                            "readTextFile": True,
                            "writeTextFile": True,
                        }
                    },
                    "clientInfo": {
                        "name": "hermes-agent",
                        "title": "Hermes Agent",
                        "version": "0.0.0",
                    },
                },
                timeout_seconds=timeout_seconds,
            )
            session = self._send_request(
                "session/new",
                {
                    "cwd": self._acp_cwd,
                    "mcpServers": [],
                },
                timeout_seconds=timeout_seconds,
            ) or {}
            self._session_id = str(session.get("sessionId") or "").strip()
            if not self._session_id:
                raise RuntimeError("OpenCode ACP did not return a sessionId.")
            self._initialized = True

    def _send_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
        text_parts: list[str] | None = None,
        reasoning_parts: list[str] | None = None,
    ) -> Any:
        """Send a JSON-RPC request to the persistent ACP process."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            raise RuntimeError("OpenCode ACP process is not running.")
        if proc.stdin is None:
            raise RuntimeError("OpenCode ACP process stdin is closed.")

        self._next_id += 1
        request_id = self._next_id
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                msg = self._inbox.get(timeout=0.1)
            except queue.Empty:
                continue

            if self._handle_server_message(
                msg,
                process=proc,
                cwd=self._acp_cwd,
                text_parts=text_parts,
                reasoning_parts=reasoning_parts,
            ):
                continue

            if msg.get("id") != request_id:
                continue
            if "error" in msg:
                err = msg.get("error") or {}
                raise RuntimeError(
                    f"OpenCode ACP {method} failed: {err.get('message') or err}"
                )
            return msg.get("result")

        stderr_text = "\n".join(self._stderr_tail).strip()
        if proc.poll() is not None and stderr_text:
            raise RuntimeError(f"OpenCode ACP process exited early: {stderr_text}")
        raise TimeoutError(f"Timed out waiting for OpenCode ACP response to {method}.")

    def _run_prompt(self, prompt_text: str, *, timeout_seconds: float) -> tuple[str, str]:
        """Send a prompt and collect the response.  Process persists across calls."""
        self._ensure_process(timeout_seconds=timeout_seconds)
        try:
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            self._send_request(
                "session/prompt",
                {
                    "sessionId": self._session_id,
                    "prompt": [
                        {
                            "type": "text",
                            "text": prompt_text,
                        }
                    ],
                },
                timeout_seconds=timeout_seconds,
                text_parts=text_parts,
                reasoning_parts=reasoning_parts,
            )
            return "".join(text_parts), "".join(reasoning_parts)
        except Exception:
            # On any error, mark stale so next call respawns.
            self._initialized = False
            raise

    def _handle_server_message(
        self,
        msg: dict[str, Any],
        *,
        process: subprocess.Popen[str],
        cwd: str,
        text_parts: list[str] | None,
        reasoning_parts: list[str] | None,
    ) -> bool:
        method = msg.get("method")
        if not isinstance(method, str):
            return False

        if method == "session/update":
            params = msg.get("params") or {}
            update = params.get("update") or {}
            kind = str(update.get("sessionUpdate") or "").strip()
            content = update.get("content") or {}
            chunk_text = ""
            if isinstance(content, dict):
                chunk_text = str(content.get("text") or "")
            if kind == "agent_message_chunk" and chunk_text and text_parts is not None:
                text_parts.append(chunk_text)
            elif kind == "agent_thought_chunk" and chunk_text and reasoning_parts is not None:
                reasoning_parts.append(chunk_text)
            return True

        if process.stdin is None:
            return True

        message_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "session/request_permission":
            response = _permission_denied(message_id)
        elif method == "fs/read_text_file":
            try:
                path = _ensure_path_within_cwd(str(params.get("path") or ""), cwd)
                block_error = get_read_block_error(str(path))
                if block_error:
                    raise PermissionError(block_error)
                try:
                    content = path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    content = ""
                line = params.get("line")
                limit = params.get("limit")
                if isinstance(line, int) and line > 1:
                    lines = content.splitlines(keepends=True)
                    start = line - 1
                    end = start + limit if isinstance(limit, int) and limit > 0 else None
                    content = "".join(lines[start:end])
                if content:
                    content = redact_sensitive_text(content, force=True)
                response = {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "content": content,
                    },
                }
            except Exception as exc:
                response = _jsonrpc_error(message_id, -32602, str(exc))
        elif method == "fs/write_text_file":
            try:
                path = _ensure_path_within_cwd(str(params.get("path") or ""), cwd)
                denied = get_write_denied_error(str(path))
                if denied:
                    raise PermissionError(denied)
                if is_write_approval_required(str(path)):
                    raise PermissionError(
                        f"Write denied: '{path}' requires interactive approval "
                        "and cannot be written through the ACP file bridge."
                    )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(params.get("content") or ""), encoding="utf-8")
                response = {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": None,
                }
            except Exception as exc:
                response = _jsonrpc_error(message_id, -32602, str(exc))
        else:
            response = _jsonrpc_error(
                message_id,
                -32601,
                f"ACP client method '{method}' is not supported by Hermes yet.",
            )

        process.stdin.write(json.dumps(response) + "\n")
        process.stdin.flush()
        return True


# ── Module-level client cache ──
# Maps (command, args_tuple, cwd, model) → live OpenCodeACPClient.
# Prevents Hermes from spawning a new opencode process on every turn.
# Keyed by model so model-switch (/model) correctly starts a fresh process.
_CLIENT_CACHE: dict[tuple[str, tuple[str, ...], str, str | None], OpenCodeACPClient] = {}
_CLIENT_CACHE_LOCK = threading.Lock()


def get_or_create_client(
    *,
    command: str | None = None,
    args: list[str] | None = None,
    cwd: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> OpenCodeACPClient:
    """Return a cached OpenCodeACPClient or create a new one.

    The cache key includes model, so switching models via /model
    (which changes the fallback entry) spawns a fresh OpenCode process
    with the new OPENCODE_MODEL env var.  Same model = same client =
    persistent session across Hermes turns.
    """
    resolved_cmd = command or _resolve_command()
    resolved_args = tuple(args or _resolve_args())
    resolved_cwd = str(Path(cwd or os.getcwd()).resolve())
    # Normalise model for cache key: strip "opencode/" prefix.
    cache_model = model.strip() if model else None
    if cache_model and cache_model.lower().startswith("opencode/"):
        cache_model = cache_model[len("opencode/"):]

    key = (resolved_cmd, resolved_args, resolved_cwd, cache_model)

    with _CLIENT_CACHE_LOCK:
        client = _CLIENT_CACHE.get(key)
        if client is not None and not client.is_closed:
            return client

    # Create outside the lock (avoid holding lock during import/init).
    client = OpenCodeACPClient(
        command=command,
        args=args,
        acp_cwd=cwd,
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
    with _CLIENT_CACHE_LOCK:
        # Double-check: another thread may have created while we were.
        existing = _CLIENT_CACHE.get(key)
        if existing is not None and not existing.is_closed:
            client.close()
            return existing
        _CLIENT_CACHE[key] = client
    return client


def close_all_clients() -> int:
    """Terminate all cached ACP processes.  Call on agent teardown."""
    count = 0
    with _CLIENT_CACHE_LOCK:
        for client in _CLIENT_CACHE.values():
            try:
                client.close()
                count += 1
            except Exception:
                pass
        _CLIENT_CACHE.clear()
    return count


# Ensure lingering ACP processes are terminated when the Python process exits.
import atexit as _atexit
_atexit.register(close_all_clients)

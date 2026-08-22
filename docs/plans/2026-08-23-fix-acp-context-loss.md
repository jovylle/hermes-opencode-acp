# Fix ACP Context Loss, Token Estimation, and Tool Call Drops

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three bugs in `opencode_acp_client.py` that cause the OpenCode ACP agent to lose its way, report wrong token counts, and silently drop tool call content during streaming.

**Architecture:** Single-file fix in `plugin/opencode_acp_client.py`. Root causes: (1) incremental prompts strip system context, (2) token estimation uses crude char/4, (3) streaming chunk relay only captures two chunk types.

**Tech Stack:** Python, JSON-RPC 2.0, Agent Client Protocol v1

---

## Root Cause Analysis

### Bug 1: Incremental prompt drops system context

`_format_incremental_prompt()` (line 247) sends only the tail of conversation for persistent sessions. The system prompt ("You are being used as ACP agent...") and tool definitions are only in `_format_messages_as_prompt()` (cold start, line 145). After turn 1, OpenCode has no reminder of:
- Its role as an ACP agent backend
- Available tools and their schemas
- The `<tool_call>` output format requirement

**Effect:** Agent forgets how to use tools, loses its task, generates unstructured text.

### Bug 2: Token estimation is wrong

`_estimate_tokens()` (line 138) uses `len(text) // 4`. This:
- Underestimates for CJK/mixed text (tokens > 4 chars each)
- Overcounts whitespace and formatting
- Makes Hermes report wrong token counts in the status bar
- Can cause context budget miscalculation

### Bug 3: Tool call chunks silently dropped

`_handle_server_message()` (line 895) only captures `agent_message_chunk` and `agent_thought_chunk`. If OpenCode sends tool-call-related chunks under different types (e.g. `agent_tool_call_chunk`, `agent_tool_result_chunk`), they are silently ignored.

---

## Global Constraints

- Single file: `plugin/opencode_acp_client.py`
- No new dependencies
- Must remain backward-compatible with existing Hermes patches
- Must not break Copilot ACP (different file, but shared patterns)

---

### Task 1: Fix incremental prompt to include system context

**Files:**
- Modify: `plugin/opencode_acp_client.py:247-267` (`_format_incremental_prompt`)
- Modify: `plugin/opencode_acp_client.py:556-567` (caller in `_create_chat_completion`)

**Interfaces:**
- Consumes: `messages`, `tools`, `tool_choice`, `model` from `_create_chat_completion`
- Produces: prompt text that includes system context + tail of conversation

^- [x] **Step 1: Update `_format_incremental_prompt` to accept tools/model/tool_choice**

Change signature from:
```python
def _format_incremental_prompt(messages: list[dict[str, Any]]) -> str:
```
to:
```python
def _format_incremental_prompt(
    messages: list[dict[str, Any]],
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
) -> str:
```

^- [x] **Step 2: Add system context sections to incremental prompt**

The function currently returns just the transcript. After building the transcript, prepend the same system context sections that `_format_messages_as_prompt` includes: role instruction, model hint, tool definitions, tool choice hint. Then append "Continue the conversation from the latest user request."

Key: extract the "build system sections" logic from `_format_messages_as_prompt` into a shared helper `_build_system_sections(model, tools, tool_choice)` so both functions reuse it.

^- [x] **Step 3: Update the call site in `_create_chat_completion`**

At line 557, change:
```python
incremental = _format_incremental_prompt(messages or [])
```
to:
```python
incremental = _format_incremental_prompt(
    messages or [], model=model, tools=tools, tool_choice=tool_choice
)
```

^- [x] **Step 4: Verify no regressions**

Run: `cd /Volumes/DevSSD/fore/lab/hermes-opencode-acp && python3 -c "from plugin.opencode_acp_client import _format_incremental_prompt, _format_messages_as_prompt; print('import OK')"`
Expected: import succeeds

---

### Task 2: Improve token estimation

**Files:**
- Modify: `plugin/opencode_acp_client.py:138-142` (`_estimate_tokens`)

**Interfaces:**
- Consumes: raw text strings
- Produces: integer token count estimate

^- [x] **Step 1: Replace crude heuristic with word-boundary-aware estimation**

Replace:
```python
def _estimate_tokens(text: str) -> int:
    """Estimate token count from text (roughly 4 chars per token for English)."""
    if not text:
        return 0
    return max(1, len(text) // 4)
```

With:
```python
def _estimate_tokens(text: str) -> int:
    """Estimate token count from text.

    Uses a mixed heuristic: whitespace-delimited words (~1.3 tokens/word)
    plus a character-based fallback for CJK/whitespaceless segments.
    Better than raw len//4 for mixed-language content.
    """
    if not text:
        return 0
    # Count whitespace-separated words (covers English, most Latin scripts)
    words = text.split()
    if words:
        word_tokens = int(len(words) * 1.3)
    else:
        word_tokens = 0
    # Characters not covered by word splits (CJK, punctuation clusters)
    non_space = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))
    char_tokens = max(0, non_space - sum(len(w) for w in words)) // 3
    return max(1, word_tokens + char_tokens)
```

^- [x] **Step 2: Verify estimation is reasonable**

Run a quick smoke test:
```bash
python3 -c "
from plugin.opencode_acp_client import _estimate_tokens
# English text
en = 'Hello world, this is a test of the token estimation function.'
print(f'English ({len(en)} chars): {_estimate_tokens(en)} tokens')
# CJK text
cjk = '你好世界，这是一个测试函数。'
print(f'CJK ({len(cjk)} chars): {_estimate_tokens(cjk)} tokens')
# Mixed
mixed = 'Hello 你好 world 世界'
print(f'Mixed ({len(mixed)} chars): {_estimate_tokens(mixed)} tokens')
"
```

---

### Task 3: Capture all streaming chunk types

**Files:**
- Modify: `plugin/opencode_acp_client.py:895-907` (`_handle_server_message`)

**Interfaces:**
- Consumes: `msg` dict from ACP JSON-RPC stream
- Produces: appends to `text_parts` list

^- [x] **Step 1: Log unrecognized chunk types for debugging**

Add a catch-all that logs unknown chunk types instead of silently dropping them. This helps diagnose future issues without breaking the protocol.

Replace the current `session/update` handler block (lines 895-907) with:

```python
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
    elif kind and chunk_text and text_parts is not None:
        # Unknown chunk type with text content — include it rather than
        # silently dropping.  This covers tool_call_chunk, tool_result_chunk,
        # and any future chunk types OpenCode may add.
        text_parts.append(chunk_text)
    return True
```

The key change is the final `elif`: any chunk type with non-empty text that isn't one of the two known types still gets appended to `text_parts`. This prevents silent content loss.

^- [x] **Step 2: Verify no import errors**

Run: `cd /Volumes/DevSSD/fore/lab/hermes-opencode-acp && python3 -c "from plugin.opencode_acp_client import OpenCodeACPClient; print('OK')"`
Expected: import succeeds

---

### Task 4: Add integration smoke test

**Files:**
- Create: `tests/test_acp_client_fixes.py`

**Interfaces:**
- Consumes: the updated functions from `opencode_acp_client`
- Produces: test pass/fail

^- [x] **Step 1: Write test for incremental prompt includes system context**

```python
import sys
sys.path.insert(0, "/Volumes/DevSSD/fore/lab/hermes-opencode-acp/plugin")
from opencode_acp_client import _format_incremental_prompt, _format_messages_as_prompt

def test_incremental_includes_system_context():
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "What tools do you have?"},
    ]
    tools = [{"function": {"name": "read_file", "description": "Read a file", "parameters": {}}}]
    result = _format_incremental_prompt(messages, tools=tools)
    assert "ACP agent" in result, f"Missing ACP role instruction in: {result[:200]}"
    assert "read_file" in result, f"Missing tool definition in: {result[:200]}"
    assert "Continue the conversation" in result, f"Missing continuation instruction"
    print("PASS: incremental prompt includes system context")

def test_incremental_without_tools():
    messages = [
        {"role": "user", "content": "Hello"},
    ]
    result = _format_incremental_prompt(messages)
    assert "ACP agent" in result, f"Missing ACP role instruction"
    assert "Continue the conversation" in result
    print("PASS: incremental prompt works without tools")

def test_token_estimation():
    from opencode_acp_client import _estimate_tokens
    assert _estimate_tokens("") == 0
    assert _estimate_tokens("hello") >= 1
    # Should be in reasonable range, not wildly off
    long_text = "Hello world, this is a longer piece of text for testing." * 10
    tokens = _estimate_tokens(long_text)
    assert 50 < tokens < 500, f"Token count {tokens} seems wrong for {len(long_text)} chars"
    print(f"PASS: token estimation = {tokens} for {len(long_text)} chars")

test_incremental_includes_system_context()
test_incremental_without_tools()
test_token_estimation()
print("\nAll tests passed!")
```

^- [x] **Step 2: Run the test**

Run: `cd /Volumes/DevSSD/fore/lab/hermes-opencode-acp && python3 tests/test_acp_client_fixes.py`
Expected: All tests passed!

---

## Summary

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| Agent loses way / forgets tools | Incremental prompt strips system context | Always include ACP role + tool defs in every turn |
| Wrong token count | `len(text) // 4` heuristic | Word-boundary + CJK-aware estimation |
| Tool call content dropped | Only 2 chunk types captured | Catch-all for unknown chunk types with text |

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin"))
from opencode_acp_client import (
    _format_incremental_prompt,
    _format_messages_as_prompt,
    _estimate_tokens,
    OpenCodeACPClient,
)


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
    assert "What tools do you have?" in result, "Missing latest user text (tail)"
    print("PASS: incremental prompt includes system context + tail")


def test_incremental_without_tools():
    messages = [
        {"role": "user", "content": "Hello"},
    ]
    result = _format_incremental_prompt(messages)
    assert "ACP agent" in result, f"Missing ACP role instruction"
    assert "Continue the conversation" in result
    print("PASS: incremental prompt works without tools")


def test_incremental_empty_messages():
    assert _format_incremental_prompt([]) == "", "Empty messages should return ''"
    # No user message → full-format fallback path
    msgs = [{"role": "system", "content": "sys"}, {"role": "assistant", "content": "hi"}]
    assert _format_incremental_prompt(msgs) == "", "No user msg should return ''"
    print("PASS: incremental prompt fallback on empty/no-user")


def test_incremental_tool_roundtrip_tail():
    # Tool results arriving AFTER the latest user message (no new user
    # prompt yet) must be fed back so OpenCode sees what it called and
    # what the tool returned.
    messages = [
        {"role": "user", "content": "Add the curl call"},
        {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "content": '{"ok": true}', "tool_call_id": "1"},
    ]
    result = _format_incremental_prompt(messages)
    assert "Tool call: read_file" in result, "Tool roundtrip tail missing tool call info"
    assert '"ok": true' in result, "Tool result missing from tail"
    print("PASS: incremental prompt renders tool roundtrip tail")


def test_incremental_new_user_turn_after_roundtrip():
    # A NEW user message after the roundtrip: OpenCode's own session
    # history holds the prior call, so the tail is just the new request.
    messages = [
        {"role": "user", "content": "Add the curl call"},
        {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "content": '{"ok": true}', "tool_call_id": "1"},
        {"role": "user", "content": "now what"},
    ]
    result = _format_incremental_prompt(messages)
    assert "now what" in result, "Latest user message missing"
    print("PASS: incremental prompt sends just the new user request")


def test_full_prompt_unchanged_shape():
    messages = [{"role": "user", "content": "Hello"}]
    tools = [{"function": {"name": "ls", "description": "List", "parameters": {}}}]
    result = _format_messages_as_prompt(messages, tools=tools)
    assert "Conversation transcript" in result
    assert "ls" in result
    assert "Hello" in result
    print("PASS: full-conversation prompt still renders system + transcript")


def test_token_estimation():
    assert _estimate_tokens("") == 0
    assert _estimate_tokens("hello") >= 1
    long_text = "Hello world, this is a longer piece of text for testing." * 10
    tokens = _estimate_tokens(long_text)
    assert 50 < tokens < 500, f"Token count {tokens} seems wrong for {len(long_text)} chars"
    cjk = "你好世界，这是一个测试函数。" * 5
    cjk_tokens = _estimate_tokens(cjk)
    # CJK must not be crushed to ~1 token: ~1 token per CJK char.
    assert cjk_tokens >= len(cjk) // 2, f"CJK undercount {cjk_tokens} for {len(cjk)} chars"
    print(f"PASS: token estimation EN={tokens} CJK={cjk_tokens}")


test_incremental_includes_system_context()
test_incremental_without_tools()
test_incremental_empty_messages()
test_incremental_tool_roundtrip_tail()
test_full_prompt_unchanged_shape()
test_token_estimation()
print("\nAll tests passed!")
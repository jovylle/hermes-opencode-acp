# OpenCode ACP Provider for Hermes Agent

Connect Hermes Agent to [OpenCode](https://opencode.ai) via the [Agent Client Protocol (ACP)](https://github.com/agentclientprotocol/agent-client-protocol). This lets Hermes use OpenCode as a coding agent backend — same protocol as Copilot ACP, but driving OpenCode's agentic loop instead.

## What it does

When configured, Hermes spawns `opencode acp` as a subprocess and communicates via JSON-RPC 2.0 over stdio (ACP v1). Hermes keeps a **persistent** ACP session (one `opencode` process per model, reused across turns):

1. Spawns `opencode acp` and handshakes (initialize → session/new)
2. First turn: sends the full conversation as a single prompt
3. Later turns: sends only the new user message (or the tool-roundtrip tail)
4. Returns an OpenAI-compatible completion, including parsed tool calls

OpenCode handles model selection and plugin fallbacks — Hermes drives the ACP wire. Tool calls OpenCode emits in its native `<tool_call><parameter=name>…</parameter><parameter=arguments>{json}</parameter></tool_call>` XML are parsed back into OpenAI function-call shape so Hermes's tool loop can execute them.

## Prerequisites

- **OpenCode CLI** installed and on PATH (or set `OPENCODE_BIN`)
  ```bash
  npm i -g opencode-ai@latest
  # or
  brew install anomalyco/tap/opencode
  ```
- **OpenCode auth configured**: `opencode auth login` or set provider env vars (OPENROUTER_API_KEY, etc.)
- **Verify**: `opencode run 'hello'` should work standalone

## Install

```bash
REPO=~/hermes-opencode-acp  # adjust if cloned elsewhere

# Copy plugin files into Hermes
cp "$REPO/plugin/opencode_acp_client.py" ~/.hermes/hermes-agent/agent/
mkdir -p ~/.hermes/hermes-agent/plugins/model-providers/opencode-acp
cp "$REPO/plugin/opencode_acp_provider.py" ~/.hermes/hermes-agent/plugins/model-providers/opencode-acp/__init__.py

# Apply patches (skip if already patched)
cd ~/.hermes/hermes-agent
git apply "$REPO/patches/"*.patch
```

## Configure

### Via `hermes setup` / `hermes model`

```bash
hermes setup
# Select "OpenCode" → "OpenCode ACP"
```

### Via `config.yaml`

```yaml
model:
  provider: opencode-acp
  base_url: acp://opencode
  api_mode: chat_completions
```

### Via environment variables

```bash
# Override the OpenCode binary path
export OPENCODE_BIN=/path/to/opencode
# or
export HERMES_OPENCODE_ACP_COMMAND=/path/to/opencode

# Override ACP args (default: ["acp"])
export HERMES_OPENCODE_ACP_ARGS="acp"

# Override the ACP base URL marker
export OPENCODE_ACP_BASE_URL="acp://opencode"
```

## How it works

```
Hermes                         OpenCode
  │                               │
  │── spawn: opencode acp ──────>│
  │                               │
  │<── JSON-RPC initialize ──────│  (handshake)
  │── initialize response ──────>│
  │                               │
  │<── session/new ──────────────│  (create session)
  │── sessionId ────────────────>│
  │                               │
  │<── session/prompt ───────────│  (send conversation)
  │   agent_thought_chunk ...     │  (streaming reasoning)
  │   agent_message_chunk ...     │  (streaming text)
  │<── stopReason: end_turn ────│
  │                               │
  │── OpenAI-compatible ─────────│  (return to Hermes)
  │   completion object           │
```

## Differences from Copilot ACP

| | Copilot ACP | OpenCode ACP |
|---|---|---|
| Binary | `copilot` | `opencode` |
| ACP args | `--acp --stdio` | `acp` |
| Provider slug | `copilot-acp` | `opencode-acp` |
| Base URL marker | `acp://copilot` | `acp://opencode` |
| Auth | Copilot CLI login | OpenCode auth |
| Model selection | GitHub Copilot catalog | OpenCode's config |

## Troubleshooting

**"Could not find the OpenCode CLI command"**
- Install OpenCode: `npm i -g opencode-ai@latest`
- Or set `OPENCODE_BIN=/full/path/to/opencode`

**OpenCode ACP exits immediately**
- Check `opencode run 'hello'` works standalone
- Check OpenCode auth: `opencode providers`

**No streaming text**
- The model may not support streaming via ACP
- Try a different model in OpenCode's config

## License

MIT
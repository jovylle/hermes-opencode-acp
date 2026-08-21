# hermes-opencode-acp

OpenCode ACP provider for [Hermes Agent](https://github.com/NousResearch/hermes-agent) — lets Hermes use [OpenCode](https://opencode.ai) as a coding agent backend via the Agent Client Protocol (ACP).

## What it does

Hermes spawns `opencode acp` as a subprocess and communicates via JSON-RPC 2.0 over stdio (ACP v1). OpenCode handles model selection, tool use, and plugin fallbacks — Hermes drives the ACP wire.

```
Hermes                         OpenCode
  │                               │
  │── spawn: opencode acp ──────>│
  │<── JSON-RPC initialize ──────│  handshake
  │<── session/new ──────────────│  create session
  │<── session/prompt ───────────│  send conversation
  │   agent_thought_chunk ...     │  streaming reasoning
  │   agent_message_chunk ...     │  streaming text
  │<── stopReason: end_turn ────│
  │── OpenAI-compatible ─────────│  return to Hermes
  │   completion object           │
```

### Persistent sessions

Unlike the default per-turn spawn, this implementation keeps the ACP process alive across turns. Same model = same process, same session context. Turn 2+ are ~60% faster.

### Dynamic model picker

`hermes fallback add` → pick `opencode-acp` → queries `opencode models` → shows all available models in an interactive picker.

## Prerequisites

- **Hermes Agent** (with ACP support)
- **OpenCode CLI** installed:
  ```bash
  npm i -g opencode-ai@latest
  # or
  brew install anomalyco/tap/opencode
  ```
- **OpenCode auth**: `opencode auth login` or set provider env vars
- **Verify**: `opencode run 'hello'` works standalone

## Install

### 1. Copy the ACP client into Hermes

```bash
# Copy the ACP client
cp opencode_acp_client.py ~/.hermes/hermes-agent/agent/

# Copy the provider plugin
mkdir -p ~/.hermes/hermes-agent/plugins/model-providers/opencode-acp
cp opencode_acp_provider.py ~/.hermes/hermes-agent/plugins/model-providers/opencode-acp/__init__.py
```

### 2. Apply core patches

These patch Hermes core files to recognize `opencode-acp` as a provider:

```bash
cd ~/.hermes/hermes-agent

# Apply each patch
git apply patches/001-auth-opencode-acp-status.patch
git apply patches/002-model-picker-dynamic.patch
git apply patches/003-runtime-helpers-acp.patch
git apply patches/004-auxiliary-client-acp.patch
```

### 3. Restart Hermes

```bash
# Restart the gateway
hermes gateway restart
```

## Configure

### Set as primary model

```bash
hermes model
# Pick "OpenCode" → "OpenCode ACP" → pick a model
```

### Add as fallback

```bash
hermes fallback add
# Pick "OpenCode ACP" → pick a model
```

### Manual config.yaml

```yaml
model:
  provider: opencode-acp
  model: opencode/deepseek-v4-flash
  base_url: acp://opencode
  api_mode: chat_completions

fallback_providers:
  - provider: opencode-acp
    model: opencode/kimi-k2.6
    base_url: acp://opencode
```

### Environment variables

```bash
# Override the OpenCode binary path
export OPENCODE_BIN=/path/to/opencode
# or
export HERMES_OPENCODE_ACP_COMMAND=/path/to/opencode

# Override ACP args
export HERMES_OPENCODE_ACP_ARGS="acp"
```

## How it works

### Architecture

```
Hermes Session
  └─ OpenCodeACPClient (cached by model+cwd)
       └─ ONE opencode subprocess (persistent)
            └─ ONE ACP session (ses_xxx)
                 ├─ Turn 1: full transcript (system + user)
                 ├─ Turn 2: incremental (just latest message)
                 └─ Turn 3: incremental (just latest message)
```

### Key features

- **Persistent sessions**: Process stays alive across turns. Same model reuses the same process.
- **Model selection**: `OPENCODE_MODEL` env var tells OpenCode which model to use. Dynamic model picker via `opencode models`.
- **Fallback chain**: Each model gets its own cached client. Same model = reuse. Different model = fresh process.
- **Crash recovery**: If the process dies, next turn spawns a fresh one automatically.

### Files

| File | Purpose |
|------|---------|
| `opencode_acp_client.py` | ACP client — handles subprocess lifecycle, JSON-RPC, streaming |
| `opencode_acp_provider.py` | Provider profile — registers `opencode-acp` in Hermes provider registry |
| `patches/001-*.patch` | Auth status resolution for opencode-acp |
| `patches/002-*.patch` | Dynamic model picker via `opencode models` |
| `patches/003-*.patch` | Runtime helpers — use cached ACP client |
| `patches/004-*.patch` | Auxiliary client — resolve opencode-acp provider |

## Differences from Copilot ACP

| | Copilot ACP | OpenCode ACP |
|---|---|---|
| Binary | `copilot` | `opencode` |
| ACP args | `--acp --stdio` | `acp` |
| Provider slug | `copilot-acp` | `opencode-acp` |
| Base URL marker | `acp://copilot` | `acp://opencode` |
| Auth | Copilot CLI login | OpenCode auth |
| Model selection | GitHub Copilot catalog | OpenCode's config |
| Models available | ~10 | 225+ |

## Troubleshooting

**"Could not find the OpenCode CLI command"**
- Install OpenCode: `npm i -g opencode-ai@latest`
- Or set `OPENCODE_BIN=/full/path/to/opencode`

**ACP session times out on first turn**
- The `session/new` response can be large (60K+ chars with all model options)
- First turn may take 15-25s; subsequent turns are 5-10s

**Model not changing**
- Check `OPENCODE_MODEL` is set: `echo $OPENCODE_MODEL`
- Verify OpenCode sees the model: `opencode models | grep your-model`

**Process crashes mid-conversation**
- Context is lost (unavoidable — process died)
- Next turn auto-spawns a fresh process
- Check OpenCode logs: `opencode run 'hello'`

## License

MIT

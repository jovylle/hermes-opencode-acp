---
name: hermes-opencode-acp
description: OpenCode ACP provider for Hermes Agent — use OpenCode as a coding agent backend
---
![Screenshot](./public/images/image.png)
# hermes-opencode-acp

**🌐 Landing page: [hermes-opencode-acp.uft1.com](https://hermes-opencode-acp.uft1.com)** — static, hosted on Cloudflare Pages (free). Updates when `site/index.html` changes: `npx wrangler pages deploy site --project-name hermes-opencode-acp`.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Use [OpenCode](https://opencode.ai) as a coding agent backend for [Hermes Agent](https://github.com/NousResearch/hermes-agent) via the Agent Client Protocol (ACP).

Connects Hermes to OpenCode via JSON-RPC over stdio. OpenCode handles model selection, tool use, and plugin fallbacks — Hermes drives the ACP wire.

> **⚠️ Early / Experimental** — This is early-stage code and may have bugs. Tested with Hermes Agent on macOS; other platforms or Hermes versions may behave differently. If something breaks, please open an issue. Contributions welcome.

> **👀 Upstream watch** — Hermes Agent core is planning its own generalized
> ACP client (NousResearch/hermes-agent#5257, OpenCode already on their
> 14-agent list). We are closely tracking it: when it lands, this repo's
> patch set gets absorbed into core and can be deleted — config stays
> compatible. See [UPSTREAM.md](./UPSTREAM.md) for the migration checklist.

## Features

- **200+ models** — access OpenCode's full catalog (Anthropic, OpenAI, Google, DeepSeek, etc.) without managing API keys individually
- **Full ACP protocol** — initialize, streaming, token estimation, model negotiation
- **Persistent sessions** — one OpenCode process per model, reused across turns (no respawn overhead)

## Use Cases

**1. Access OpenCode's full model catalog**

OpenCode has 200+ models across dozens of providers with built-in auth. No need to manage API keys individually — OpenCode handles it.

**2. Use OpenCode's agentic loop as a provider**

OpenCode has its own tool use, plugin fallbacks, and context management. When Hermes delegates to OpenCode via ACP, it gets all of that for free.

**3. Model flexibility without config sprawl**

Pick any model from OpenCode's catalog as your Hermes model or fallback. `hermes fallback add` now shows the full list — no more guessing model IDs.

**4. Free tier via OpenCode Go**

OpenCode offers free credits for certain models. Run Hermes with free models via OpenCode's Go tier, then fall back to paid when exhausted.

**5. Fallback chain integration**

OpenCode ACP models work in Hermes's fallback chain. If your primary model is rate-limited, try OpenCode models before going to other providers.

## Quick Start

### 1. Install OpenCode CLI

Skip if already installed.

```bash
which opencode && opencode run 'hello' && echo "OpenCode OK — skip to step 2"

# Install (pick one):
brew install anomalyco/tap/opencode   # macOS
npm i -g opencode-ai@latest          # npm

# Verify
opencode run 'hello'
```

### 2. Authenticate OpenCode

Skip if already authenticated (`opencode providers` shows your providers).

```bash
opencode auth login
# or set provider env vars (OPENROUTER_API_KEY, ANTHROPIC_API_KEY, etc.)
```

### 3. Clone this repo

Skip if already cloned.

```bash
ls ~/hermes-opencode-acp/README.md && echo "Repo exists — skip to step 4"

git clone https://github.com/jovylle/hermes-opencode-acp.git
cd hermes-opencode-acp
```

### 4. Run the installer

Skip if already installed (check `ls ~/.hermes/plugins/model-providers/opencode-acp/__init__.py`).

```bash
REPO=/Volumes/DevSSD/fore/lab/hermes-opencode-acp  # adjust if cloned elsewhere
"$REPO/install-hermes-core-patch.sh"
```

The script installs the provider plugin to
`~/.hermes/plugins/model-providers/opencode-acp/` — this lives OUTSIDE the
Hermes git checkout, so `hermes update` never touches or stashes it.
No Hermes-core patch is needed: since v1.1.0 the plugin supplies its own
ACP client through the `create_client` provider seam (same mechanism as
Hermes's built-in `copilot-acp`).

It is idempotent — safe to re-run any time. Then enable it:

```bash
hermes plugins enable opencode-acp-provider
hermes plugins validate ~/.hermes/plugins/model-providers/opencode-acp
```

### 5. Configure Hermes to use OpenCode ACP

```bash
# Interactive — shows 200+ models from OpenCode
hermes model
# Pick: OpenCode → OpenCode ACP → select model

# Optional: add to fallback chain
hermes fallback add
# Pick: OpenCode ACP → pick a model
```

### 6. Restart and use

```bash
hermes gateway restart
hermes chat  # works!
```

## Configuration

### Via `hermes model`

```bash
hermes model
# Select: OpenCode → OpenCode ACP
# Shows 200+ models from OpenCode's catalog
```

### Via `config.yaml`

```yaml
model:
  provider: opencode-acp
  model: opencode/deepseek-v4-flash
  base_url: acp://opencode
  api_mode: chat_completions
```

### Via environment variables

```bash
export OPENCODE_BIN=/path/to/opencode
export HERMES_OPENCODE_ACP_COMMAND=/path/to/opencode
export HERMES_OPENCODE_ACP_ARGS="acp"
export OPENCODE_ACP_BASE_URL="acp://opencode"
```

### Fallback chain

```yaml
fallback_providers:
  - provider: opencode-acp
    model: opencode/mimo-v2.5-free
    base_url: acp://opencode
  - provider: opencode-acp
    model: opencode/kimi-k2.6
    base_url: acp://opencode
```

> **Model switching keeps the session (1:1 continuation).**  The client cache
> is keyed by `(command, args, cwd)` — NOT by model.  When Hermes falls back
> to a different model (or you `/model`-switch), the *same* OpenCode process
> and session stay alive and the new model is applied in place via
> `session/set_config_option` (verified live on opencode 1.18.20: the session
> recalls earlier context after the switch).  No fresh process, no history loss.

> **Model picking is ACP-native.**  `/model` and `hermes model` now probe the
> `opencode acp` server itself for its advertised model catalog (the same list
> it validates `session/set_config_option` against) instead of the GitHub
> Copilot catalog.  `opencode models` CLI remains the fallback, then the static
> list.

## Architecture

```
Hermes ──ACP JSON-RPC──> OpenCode ──HTTP──> LLM Provider
  │                          │
  │ persistent process       │ model selection + tools
  │ streaming response       │ plugin fallbacks
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
| Session persistence | Per-turn (new process) | Persistent (same process) |

## After a Hermes update

`hermes update` is safe with this setup — but the core-tree patch must be
re-applied afterwards, and if upstream changed nearby code, regenerated.

```bash
hermes update

# 1. Re-apply (no-op if the tree is already patched)
/Volumes/DevSSD/fore/lab/hermes-opencode-acp/install-hermes-core-patch.sh

# 2. Restart the gateway from a separate shell
hermes gateway restart && hermes gateway status

# 3. Verify the fallback provider resolves
hermes chat -q "say ok" -Q --max-turns 1
```

**If step 1 fails** ("error: patch does not apply" or conflict markers):
upstream shipped changes that collide with the patch. Regenerate it:

```bash
cd ~/.hermes/hermes-agent
git status --porcelain          # see which files have conflicts/dirty state
git checkout -- .               # discard broken partial apply
rm -f agent/opencode_acp_client.py
```

Then re-apply by hand onto the new version:

```bash
cd /Volumes/DevSSD/fore/lab/hermes-opencode-acp
cp plugin/opencode_acp_client.py ~/.hermes/hermes-agent/agent/
cd ~/.hermes/hermes-agent

# re-do each routing edit from the old patch; the hunks that still apply:
git apply --3way "$OLD_REPO/patches/hermes-core-v0.20.5.patch" || true

# resolve conflicts in an editor (search for <<<<<<<), then regenerate:
git diff > /Volumes/DevSSD/fore/lab/hermes-opencode-acp/patches/hermes-core-v<NEWVER>.patch
git diff --cached >> /Volumes/DevSSD/fore/lab/hermes-opencode-acp/patches/hermes-core-v<NEWVER>.patch
git reset -q                    # unstage everything — keep main's index clean
```

Delete stale `patches/hermes-core-v<oldver>.patch` once the new one verifies.
The plugin dir (`~/.hermes/plugins/model-providers/opencode-acp/`) needs no
attention on updates — updates never touch `$HERMES_HOME`.

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

**Model list not showing**
- Run `opencode models` directly to verify OpenCode CLI works
- If empty, check OpenCode auth with `opencode providers`

## License

MIT

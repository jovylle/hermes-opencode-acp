# Upstream Tracking

Hermes Agent upstream is building its own generalized ACP client. When it
lands, this project's purpose gets absorbed into core and these patches can
be deleted. This file tracks what to watch and how to migrate.

## The successor: NousResearch/hermes-agent#5257

**"feat: Generalized ACP client for multi-agent CLI orchestration"** (open,
needs-decision, P4 — created 2026-04-05, untouched for months).

Proposal:
- `agent/acp_client.py` — generic `ACPClient` refactored from `copilot_acp_client.py`
- `agent/acp_agent_registry.py` — registry mapping 14 agents (Claude Code,
  Codex, Gemini, Cursor, Copilot, Kiro, KiloCode, **OpenCode**, Kimi, Qwen,
  Cline, Amp, Droid, iFlow) to ACP launch commands, with
  `HERMES_ACP_{NAME}_COMMAND` env overrides
- Provider overlays per agent (`claude-acp`, `codex-acp`, ...)
- Any `acp://{agent}` URL routes to the generic client
- `copilot_acp_client.py` becomes a thin backwards-compatible wrapper

OpenCode is already on their 14-agent list.

## Related upstream issues

- **#19493** (open, P3) — Auto-detect `opencode acp` command syntax for
  `delegate_task` subagents. Upstream already verified OpenCode ACP works
  end-to-end with Hermes via `CopilotACPClient`; proposal is to make
  `_resolve_args()` return `["acp"]` when the command basename is `opencode`.
- **#16282** (closed, sweeper:not-planned) — Generic ACP-harness request was
  closed; their chosen route is #5257, not per-harness feature requests.
  This is why we are NOT filing PRs or new issues — nothing to join.

## Migration checklist (when #5257 or equivalent lands in core)

1. Check whether upstream's registry names the provider slug `opencode-acp`.
   - If yes: `config.yaml` keeps working as-is (`provider: opencode-acp`,
     `base_url: acp://opencode`). Just delete the patch set and the local
     client/provider files, and upstream handles the rest.
   - If no (e.g. `opencode` or `oc-acp`): one-line change in `config.yaml`
     plus any fallback-chain entries — no client changes needed.
2. `git -C ~/.hermes/hermes-agent pull` — confirm the tree no longer needs
   `patches/*.patch`; the 14 modified files should revert to clean upstream.
3. Remove `agent/opencode_acp_client.py` and
   `plugins/model-providers/opencode-acp/` from the live install.
4. Verify: `hermes chat` (model through OpenCode ACP) + `hermes model`
   (pick a model, switch live) — same smoke tests as the original rollout.
5. Archive this repo (or repurpose as the migration guide); update this file
   to point at the final upstream state.

## Maintenance context

- Patches were originally built against hermes-agent @ **222465d**; by the
  time they were re-verified the local tree had moved to **bdc5b1f74**
  (thousands of commits later) and patch 010 (models.py) had already needed
  context rescue once. Upstream moves fast (~125 merged PRs per release
  window) — expect drift.
- When regenerating patches after a change, tag/fork the exact hermes-agent
  baseline you diffed against (`git diff -- <file>` from the patched tree),
  and record that baseline commit in the commit message of the regeneration.
- Live install = `~/.hermes/hermes-agent` (this repo's patches applied,
  client + provider copied in), already in sync with the latest upstream
  as of 2026-08.
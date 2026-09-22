#!/usr/bin/env bash
# install-plugin.sh
#
# Installs the opencode-acp provider plugin into the Hermes user-dir.
# No Hermes-core patch is needed (v1.2.0+ supplies its own ACP client
# via the create_client provider seam, like built-in copilot-acp),
# so this survives `hermes update` untouched. Idempotent — safe to re-run.
#
# (Kept the old filename as a symlink target; prefer calling it
#  install-plugin.sh going forward.)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$HOME/.hermes/plugins/model-providers"
rm -rf "$HOME/.hermes/plugins/model-providers/opencode-acp"
cp -R "$REPO_DIR/plugin" "$HOME/.hermes/plugins/model-providers/opencode-acp"
rm -rf "$HOME/.hermes/plugins/model-providers/opencode-acp/__pycache__"
echo "[ok] plugin installed: ~/.hermes/plugins/model-providers/opencode-acp"
echo "     next: hermes plugins enable opencode-acp-provider"
echo "           hermes plugins validate ~/.hermes/plugins/model-providers/opencode-acp"

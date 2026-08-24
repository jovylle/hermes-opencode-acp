#!/usr/bin/env bash
# install-hermes-core-patch.sh
#
# Re-applies the opencode-acp integration to the local Hermes checkout.
# Run this AFTER every successful `hermes update`.
#
# What it does:
#   1. Copies plugin/ -> ~/.hermes/plugins/model-providers/opencode-acp/
#      (user plugin dir is OUTSIDE the git checkout — updates never touch it)
#   2. Applies patches/hermes-core-v<ver>.patch to ~/.hermes/hermes-agent as
#      UNCOMMITTED working-tree changes (never commit them on main — the
#      updater hard-resets diverged mains and would destroy committed work;
#      uncommitted changes go through the autostash flow instead).
#
# If step 2 fails after an update, upstream probably shipped conflicting
# changes: regenerate the patch (see README section "After a Hermes update").
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
HERMES_REPO="${HERMES_REPO:-$HOME/.hermes/hermes-agent}"
PATCH="$REPO_DIR"/patches/hermes-core-v*.patch

# --- 1. user-dir plugin -----------------------------------------------------
mkdir -p "$HOME/.hermes/plugins/model-providers"
rm -rf "$HOME/.hermes/plugins/model-providers/opencode-acp"
cp -R "$REPO_DIR/plugin" "$HOME/.hermes/plugins/model-providers/opencode-acp"
rm -rf "$HOME/.hermes/plugins/model-providers/opencode-acp/__pycache__"
echo "[ok] plugin installed: ~/.hermes/plugins/model-providers/opencode-acp"

# --- 2. core-tree routing patch ---------------------------------------------
cd "$HERMES_REPO"

if grep -q "opencode_acp_client" agent/agent_runtime_helpers.py 2>/dev/null; then
    echo "[ok] core patch already applied — nothing to do"
    exit 0
fi

PATCHES=( "$REPO_DIR"/patches/hermes-core-v*.patch )
if [ ${#PATCHES[@]} -eq 0 ] || [ ! -e "${PATCHES[0]}" ]; then
    echo "[fail] no patch file found in $REPO_DIR/patches/" >&2
    exit 1
fi

if ! git diff --quiet -- . ':!agent' ':!hermes_cli'; then
    echo "[warn] unrelated local changes present in $HERMES_REPO — patch applies on top of them" >&2
fi

git apply --3way "${PATCHES[-1]}"
echo "[ok] core patch applied (uncommitted) in $HERMES_REPO"
echo "     next: restart the gateway from a separate shell, then verify the provider"

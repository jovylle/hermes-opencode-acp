"""OpenCode ACP provider profile.

OpenCode ACP uses an external ACP subprocess -- NOT the standard
REST transport. The profile supplies its own client via
:meth:`ProviderProfile.create_client` (same seam as in-tree
``copilot-acp``), so no Hermes-core patch is needed.

The actual communication happens via JSON-RPC 2.0 over stdio
(Agent Client Protocol v1), handled by this plugin's
``opencode_acp_client.OpenCodeACPClient``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_PLUGIN_DIR = Path(__file__).parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from providers import register_provider
from providers.base import ProviderProfile


class OpenCodeACPProfile(ProviderProfile):
    """OpenCode ACP -- external process, no REST models endpoint."""

    def create_client(self, **client_kwargs: Any) -> Any:
        """Build the ACP stdio shim rather than an HTTP client."""
        from opencode_acp_client import OpenCodeACPClient

        return OpenCodeACPClient(**client_kwargs)

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Live catalog from a short-lived ``opencode acp`` session.

        None when the CLI is missing or the probe fails — callers fall
        back to their next source.
        """
        try:
            from hermes_cli.auth import resolve_external_process_provider_credentials
        except Exception:
            resolve_external_process_provider_credentials = None  # type: ignore[assignment]
        try:
            if resolve_external_process_provider_credentials is not None:
                creds = resolve_external_process_provider_credentials(self.name)
                if not str(creds.get("base_url") or "").startswith("acp://"):
                    return None
                from opencode_acp_client import probe_opencode_models

                return probe_opencode_models(
                    command=creds.get("command"),
                    args=creds.get("args"),
                    timeout=timeout,
                ) or None
            from opencode_acp_client import probe_opencode_models

            return probe_opencode_models(timeout=timeout) or None
        except Exception:
            return None


opencode_acp = OpenCodeACPProfile(
    name="opencode-acp",
    aliases=("opencode_acp", "oc-acp", "opencode-acp-agent"),
    api_mode="chat_completions",
    env_vars=(),  # Managed by ACP subprocess
    base_url="acp://opencode",  # ACP internal scheme
    auth_type="external_process",
    process_command="opencode",
    process_args=("acp",),
    process_command_env_vars=("HERMES_OPENCODE_ACP_COMMAND", "OPENCODE_BIN"),
    process_args_env_var="HERMES_OPENCODE_ACP_ARGS",
)

register_provider(opencode_acp)

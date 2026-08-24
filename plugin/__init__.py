"""OpenCode ACP provider profile.

OpenCode ACP uses an external ACP subprocess -- NOT the standard
REST transport.  ``base_url = "acp://opencode"`` is a marker that
triggers the special routing in runtime_provider.py and
agent_runtime_helpers.py, similar to copilot-acp's ``acp://copilot``.

The actual communication happens via JSON-RPC 2.0 over stdio
(Agent Client Protocol v1), handled by
``agent.opencode_acp_client.OpenCodeACPClient``.
"""

from providers import register_provider
from providers.base import ProviderProfile


class OpenCodeACPProfile(ProviderProfile):
    """OpenCode ACP -- external process, no REST models endpoint."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Model listing is handled by the ACP subprocess."""
        return None


opencode_acp = OpenCodeACPProfile(
    name="opencode-acp",
    aliases=("opencode_acp", "oc-acp", "opencode-acp-agent"),
    api_mode="chat_completions",
    env_vars=(),  # Managed by ACP subprocess
    base_url="acp://opencode",  # ACP internal scheme
    auth_type="external_process",
)

register_provider(opencode_acp)

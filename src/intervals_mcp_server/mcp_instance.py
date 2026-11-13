"""
Shared MCP instance module.

This module provides a shared FastMCP instance that can be imported by both
the server module and tool modules without creating cyclic imports.
"""

import os

from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error
from mcp.server.transport_security import TransportSecuritySettings  # pylint: disable=import-error

from intervals_mcp_server.api.client import setup_api_client


def transport_security_from_env() -> TransportSecuritySettings | None:
    """
    Extra public hostnames (comma-separated MCP_ALLOWED_HOSTS) for a reverse
    proxy that cannot rewrite the Host header, e.g. Tailscale Funnel.

    DNS-rebinding protection stays on: localhost plus exactly these hosts are
    accepted. Returns None (FastMCP defaults) when the variable is unset.
    """
    extra = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if not extra:
        return None
    local = ["127.0.0.1", "localhost", "[::1]"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{h}:*" for h in local] + extra,
        allowed_origins=[f"http://{h}:*" for h in local] + [f"https://{h}" for h in extra],
    )


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


# FastMCP's constructor defaults override FASTMCP_HOST/FASTMCP_PORT, so read them here.
mcp: FastMCP = FastMCP(  # pylint: disable=invalid-name
    "intervals-icu",
    lifespan=setup_api_client,
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
    transport_security=transport_security_from_env(),
    # Streamable HTTP only: plain JSON replies instead of an SSE stream, and no
    # Mcp-Session-Id bookkeeping. Some hosted clients cope better with these.
    json_response=_env_flag("FASTMCP_JSON_RESPONSE"),
    stateless_http=_env_flag("FASTMCP_STATELESS_HTTP"),
)

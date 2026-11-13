"""Tests for the secret path-token gate."""

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from intervals_mcp_server.path_token import PathTokenGate, validate_path_token

TOKEN = "A" * 20 + "b-c_" + "9" * 20


async def _echo_app(scope, receive, send):
    body = f"{scope.get('root_path', '')}|{scope['path']}".encode()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body})


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000")


@pytest.mark.parametrize("bad", ["short", "x" * 31, "a" * 40 + "/", "a" * 40 + "?", "a" * 40 + "%2F"])
def test_validate_rejects_bad_tokens(bad):
    with pytest.raises(ValueError):
        validate_path_token(bad)


def test_gate_refuses_to_construct_with_weak_token():
    with pytest.raises(ValueError):
        PathTokenGate(_echo_app, "tooshort")


@pytest.mark.asyncio
async def test_correct_token_passes_and_sets_root_path():
    async with _client(PathTokenGate(_echo_app, TOKEN)) as c:
        r = await c.get(f"/{TOKEN}/sse")
    assert r.status_code == 200
    assert r.text == f"/{TOKEN}|/{TOKEN}/sse"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/sse",
        "/messages/",
        "/",
        f"/{TOKEN}",  # token with nothing after it
        f"/{TOKEN[:-1]}/sse",  # one char short
        f"/{TOKEN}x/sse",  # one char long
        f"/{TOKEN.lower()}/sse",  # case matters
        f"/x/{TOKEN}/sse",  # token not first
    ],
)
async def test_wrong_or_missing_token_is_404(path):
    async with _client(PathTokenGate(_echo_app, TOKEN)) as c:
        r = await c.get(path)
    assert r.status_code == 404
    assert r.text == "Not Found"


@pytest.mark.asyncio
async def test_real_sse_app_routes_message_endpoint_behind_token():
    """With the real FastMCP app, the message route resolves only under the token."""
    app = PathTokenGate(FastMCP("t").sse_app(), TOKEN)
    async with _client(app) as c:
        inside = await c.post(f"/{TOKEN}/messages/?session_id=00000000000000000000000000000000", json={})
        outside = await c.post("/messages/?session_id=00000000000000000000000000000000", json={})
    # Reaches the MCP transport (unknown session) rather than the gate's 404.
    assert inside.status_code != 404 or inside.text != "Not Found"
    assert outside.status_code == 404 and outside.text == "Not Found"


def test_allowed_hosts_unset_keeps_fastmcp_defaults(monkeypatch):
    from intervals_mcp_server.mcp_instance import transport_security_from_env

    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    assert transport_security_from_env() is None


@pytest.mark.asyncio
async def test_allowed_hosts_accepts_listed_host_only(monkeypatch):
    from intervals_mcp_server.mcp_instance import transport_security_from_env

    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "box.tail1234.ts.net")
    sec = transport_security_from_env()
    assert sec.enable_dns_rebinding_protection
    app = PathTokenGate(FastMCP("t", transport_security=sec).sse_app(), TOKEN)
    url = f"/{TOKEN}/messages/?session_id=00000000000000000000000000000000"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://box.tail1234.ts.net") as c:
        ok = await c.post(url, json={})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://evil.example") as c:
        bad = await c.post(url, json={})
    assert ok.status_code != 421 and "Invalid Host" not in ok.text
    assert bad.status_code == 421 or "Invalid Host" in bad.text


@pytest.mark.asyncio
async def test_streamable_http_route_only_under_token():
    """POST /<token>/mcp reaches the Streamable HTTP route; /mcp without the token is 404."""
    app = PathTokenGate(FastMCP("t").streamable_http_app(), TOKEN)
    async with _client(app) as c:
        outside = await c.post("/mcp", json={})
        wrong = await c.post(f"/{TOKEN[:-1]}/mcp", json={})
    assert outside.status_code == 404 and outside.text == "Not Found"
    assert wrong.status_code == 404 and wrong.text == "Not Found"

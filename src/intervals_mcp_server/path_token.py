"""
Secret path-token gate for the HTTP transports.

Some MCP clients (for example claude.ai custom connectors) cannot send custom
headers, so the shared secret lives in the URL instead:

    https://<host>/<MCP_PATH_TOKEN>/sse

The gate compares the first path segment against the token in constant time.
On a match it sets ``root_path`` to ``/<token>``; Starlette strips it for
routing and the SSE transport prefixes it to the message endpoint it hands the
client, so every follow-up POST also has to carry the token. Anything else gets
a bare 404 so the server's existence is not advertised.
"""

from __future__ import annotations

import hmac
import re

from starlette.types import ASGIApp, Receive, Scope, Send

MIN_TOKEN_LENGTH = 32
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_path_token(token: str) -> None:
    """Raise ValueError unless the token is long and URL-safe."""
    if len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"MCP_PATH_TOKEN must be at least {MIN_TOKEN_LENGTH} characters.")
    if not _TOKEN_RE.match(token):
        raise ValueError("MCP_PATH_TOKEN may only contain A-Z, a-z, 0-9, '-' and '_'.")


class PathTokenGate:  # pylint: disable=too-few-public-methods
    """ASGI middleware that only lets requests under ``/<token>/`` through."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        validate_path_token(token)
        self.app = app
        self._token = token.encode()
        self._prefix = "/" + token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)  # lifespan etc.
            return

        path: str = scope.get("path", "")
        first_segment = path[1:].split("/", 1)[0] if path.startswith("/") else ""
        has_rest = len(path) > len(first_segment) + 1  # require "/<token>/..."
        if has_rest and hmac.compare_digest(first_segment.encode(), self._token):
            root_path = scope.get("root_path", "").rstrip("/") + self._prefix
            await self.app(dict(scope, root_path=root_path), receive, send)
            return

        await _not_found(scope, send)


async def _not_found(scope: Scope, send: Send) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    await send(
        {
            "type": "http.response.start",
            "status": 404,
            "headers": [(b"content-type", b"text/plain"), (b"content-length", b"9")],
        }
    )
    await send({"type": "http.response.body", "body": b"Not Found"})

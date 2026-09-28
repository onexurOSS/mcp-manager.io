# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Resolve which MCP transport to run on, from environment config.

stdio remains the default -- an existing self-hosted single-process deployment that sets
none of these variables gets byte-identical behaviour to before this module existed.
HTTP is additive and opt-in via MANAGER_MCP_TRANSPORT.

"http" is FastMCP's name for the MCP spec's current Streamable HTTP transport (confirmed
against the installed fastmcp==3.4.5: FastMCP.run_http_async treats "http" and
"streamable-http" as synonyms, routing both through the same handler). "sse" is the
older, now-legacy HTTP transport, kept only because FastMCP itself still accepts it --
not recommended for new deployments.

This module resolves config only. It does not change the single-Manager-instance-per-
process model: get_client()/get_policy() in server.py are unaware of transport and stay
exactly as they are regardless of which value comes out of here.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

VALID_TRANSPORTS = frozenset({"stdio", "http", "streamable-http", "sse"})
RECOMMENDED_HTTP_TRANSPORT = "http"

TRANSPORT_ENV = "MANAGER_MCP_TRANSPORT"
HTTP_HOST_ENV = "MANAGER_MCP_HTTP_HOST"
HTTP_PORT_ENV = "MANAGER_MCP_HTTP_PORT"
HTTP_AUTH_TOKEN_ENV = "MANAGER_MCP_HTTP_AUTH_TOKEN"

NO_AUTH_WARNING = (
    "manager-mcp: HTTP transport has no transport-level authentication configured "
    f"({HTTP_AUTH_TOKEN_ENV} is unset). Any client that can reach this host:port can "
    "call every tool this process exposes. This is only safe when network placement "
    "(loopback binding, a private Docker network, a gateway/reverse proxy in front) is "
    f"the sole access control. Set {HTTP_AUTH_TOKEN_ENV} to require a bearer token, or "
    "confirm network isolation is genuinely in place."
)


class TransportConfigError(ValueError):
    """Invalid transport configuration (unknown transport name, non-integer port)."""


@dataclass(frozen=True)
class TransportConfig:
    transport: str
    # None for either means "let FastMCP use its own default" (127.0.0.1:8000 as of
    # fastmcp 3.4.5) rather than this module inventing its own default port to track.
    host: str | None = None
    port: int | None = None
    # None means no transport-level auth is configured -- see NO_AUTH_WARNING. A shared
    # secret across the whole process, checked before a request reaches tool dispatch;
    # deliberately not per-caller identity or a full OAuth flow, that's the gateway's job.
    auth_token: str | None = None

    @property
    def is_http(self) -> bool:
        return self.transport != "stdio"


def resolve_transport_config(environ: dict[str, str] | None = None) -> TransportConfig:
    env = environ if environ is not None else os.environ

    transport = env.get(TRANSPORT_ENV, "stdio").strip().lower() or "stdio"
    if transport not in VALID_TRANSPORTS:
        raise TransportConfigError(
            f"{TRANSPORT_ENV}={transport!r} is not a recognised transport. "
            f"Valid values: {', '.join(sorted(VALID_TRANSPORTS))}."
        )

    if transport == "stdio":
        # Host/port are HTTP-only concepts; silently ignoring them in stdio mode (rather
        # than erroring) matches this repo's existing tolerance for irrelevant env vars
        # being set (e.g. write-scope envs when no write tools apply).
        return TransportConfig(transport="stdio")

    host = env.get(HTTP_HOST_ENV, "").strip() or None
    port_raw = env.get(HTTP_PORT_ENV, "").strip()
    port: int | None = None
    if port_raw:
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise TransportConfigError(f"{HTTP_PORT_ENV}={port_raw!r} is not an integer.") from exc

    auth_token = env.get(HTTP_AUTH_TOKEN_ENV, "").strip() or None

    return TransportConfig(transport=transport, host=host, port=port, auth_token=auth_token)


class BearerTokenMiddleware:
    """Raw ASGI middleware: rejects any HTTP request missing a matching
    ``Authorization: Bearer <token>`` header, before it reaches the MCP app.

    Plain ASGI rather than Starlette's BaseHTTPMiddleware deliberately -- Streamable
    HTTP's responses can be long-lived event streams, and BaseHTTPMiddleware buffers the
    whole response body, which would break streaming. A closed connection (401) never
    reaches that far, so this only matters for the pass-through case, but pass-through
    is the common case.
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        presented = headers.get(b"authorization", b"")
        if presented != self._expected:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"error":"unauthorized"}',
                }
            )
            return

        await self.app(scope, receive, send)


def warn_if_no_http_auth(config: TransportConfig) -> None:
    """Print NO_AUTH_WARNING to stderr when HTTP mode has no bearer token configured.

    A separate function (not inlined in resolve_transport_config) so it's callable --
    and testable -- independent of config resolution, and so it only ever fires once,
    at actual server startup, not on every config read.
    """
    if config.is_http and config.auth_token is None:
        print(NO_AUTH_WARNING, file=sys.stderr)

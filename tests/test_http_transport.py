# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Real over-the-wire HTTP transport tests.

These start the actual public entrypoint (manager_mcp.server.main) in a subprocess with
MANAGER_MCP_TRANSPORT=http, then connect a real fastmcp.Client over a real TCP socket --
not an in-process call_tool() shortcut -- so a pass here proves the transport itself
works, not just that the tool/policy logic is correct (that's already covered elsewhere,
transport-agnostically, by the existing scope/denylist unit tests).

MANAGER_API_URL points at 127.0.0.1:9 (the "discard" port, connection refused) exactly
like the existing stdio permission tests -- any call that reaches the network fails with
a distinguishable "not reachable" message, so a policy rejection (scope or denylist) can
never be confused with a network failure.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.asyncio

try:
    from fastmcp import Client
except ImportError:  # pragma: no cover - fastmcp is a hard dependency in practice
    Client = None

_UNREACHABLE_MANAGER_URL = "http://127.0.0.1:9/api2"
_STARTUP_TIMEOUT_S = 10.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            try:
                s.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.1)
    raise TimeoutError(f"Nothing listening on 127.0.0.1:{port} after {timeout_s}s")


class _HttpServer:
    """Launches `manager-mcp` in real HTTP mode as a subprocess for the test's duration."""

    def __init__(
        self, write_scopes: str = "", delete_scopes: str = "", auth_token: str | None = None
    ) -> None:
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        env = {
            **os.environ,
            "MANAGER_API_URL": _UNREACHABLE_MANAGER_URL,
            "MANAGER_API_KEY": "test-key",
            "MANAGER_MCP_WRITE_SCOPES": write_scopes,
            "MANAGER_MCP_DELETE_SCOPES": delete_scopes,
            "MANAGER_MCP_TRANSPORT": "http",
            "MANAGER_MCP_HTTP_HOST": "127.0.0.1",
            "MANAGER_MCP_HTTP_PORT": str(self.port),
            "FASTMCP_SHOW_SERVER_BANNER": "false",
        }
        if auth_token is not None:
            env["MANAGER_MCP_HTTP_AUTH_TOKEN"] = auth_token
        self.process = subprocess.Popen(
            [sys.executable, "-c", "from manager_mcp.server import main; main()"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def __enter__(self) -> _HttpServer:
        try:
            _wait_for_port(self.port, _STARTUP_TIMEOUT_S)
        except TimeoutError:
            self.process.terminate()
            out = self.process.stdout.read() if self.process.stdout else ""
            self.process.wait(timeout=5)
            raise AssertionError(f"HTTP server never started listening.\nOutput:\n{out}")
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


@pytest.mark.skipif(Client is None, reason="fastmcp not installed")
async def test_http_server_starts_and_exposes_the_expected_read_tools() -> None:
    with _HttpServer() as server:
        async with Client(server.url) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
    assert "get_server_info" in names
    assert "list_records" in names
    # Same 30-read-tool-only guarantee the stdio test_mcp_permissions.py asserts for a
    # no-scopes configuration -- proves HTTP mode doesn't expose anything extra.
    assert len(names) == 30
    assert not any(name.startswith("create_") or name.startswith("delete_") for name in names)


@pytest.mark.skipif(Client is None, reason="fastmcp not installed")
async def test_representative_read_tool_works_over_http() -> None:
    """get_server_info: a real registered read tool, no Manager API call involved --
    isolates "does the transport correctly carry a tool call and its result" from
    "is ManagerClient reachable", which is a separate, already-covered concern."""
    with _HttpServer() as server:
        async with Client(server.url) as client:
            result = await client.call_tool("get_server_info", {})
    payload = result.data if hasattr(result, "data") else json.loads(result.content[0].text)
    assert payload["name"] == "manager-mcp"
    assert isinstance(payload["registered_tool_count"], int)
    assert payload["registered_tool_count"] == 30


@pytest.mark.skipif(Client is None, reason="fastmcp not installed")
async def test_scope_gated_write_tool_is_rejected_over_http_exactly_as_over_stdio() -> None:
    """No write scopes granted -- mirrors tests/test_mcp_permissions.py's
    test_correction_tools_cannot_cross_into_a_scope_that_is_not_enabled, over a real
    HTTP connection instead of an in-process call_tool()."""
    with _HttpServer(write_scopes="") as server:
        async with Client(server.url) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert "propose_correction" not in names  # not even registered without a scope

            # Calling it anyway (as a client that guessed wrong, or an older cached tool
            # list) over the real HTTP connection must surface a real MCP tool error, the
            # same as it would over stdio -- not a silent hang or a transport-swallowed
            # exception.
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "propose_correction", {"resource": "sales_invoices", "fields": {}}
                )
    assert "Unknown tool" in str(exc_info.value) or "not found" in str(exc_info.value).lower()


@pytest.mark.skipif(Client is None, reason="fastmcp not installed")
async def test_scope_and_policy_configuration_is_identical_regardless_of_transport() -> None:
    """get_server_info reports the exact scope/policy configuration in effect
    (write_scopes_configured, effective_write_scopes, etc.) -- comparing this between an
    HTTP run and a stdio run with byte-identical env vars is a direct, executable proof
    that transport selection never changes WritePolicy/ManagerClient construction, per
    Step 2's constraint, not just an inference from reading the code."""
    scope_env = {
        "MANAGER_API_URL": _UNREACHABLE_MANAGER_URL,
        "MANAGER_API_KEY": "test-key",
        "MANAGER_MCP_WRITE_SCOPES": "banking,sales",
        "MANAGER_MCP_DELETE_SCOPES": "ledger",
    }

    # stdio side: same in-process call_tool() pattern test_mcp_permissions.py already
    # uses, run in a fresh child interpreter (tools register once per process).
    child = (
        "import asyncio, json, os\n"
        "from manager_mcp import server as s\n"
        "async def main():\n"
        "    s.reset_client()\n"
        "    s.register_task_tools()\n"
        "    s.register_write_tools()\n"
        "    r = await s.mcp.call_tool('get_server_info', {})\n"
        "    payload = r.data if hasattr(r, 'data') else r.structured_content\n"
        "    print('RESULT:' + json.dumps(payload))\n"
        "asyncio.run(main())\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", child],
        env={**os.environ, **scope_env},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    stdio_line = next(x for x in proc.stdout.splitlines() if x.startswith("RESULT:"))
    stdio_payload = json.loads(stdio_line[len("RESULT:") :])

    with _HttpServer(write_scopes="banking,sales", delete_scopes="ledger") as server:
        async with Client(server.url) as client:
            http_result = await client.call_tool("get_server_info", {})
    http_payload = http_result.data if hasattr(http_result, "data") else json.loads(
        http_result.content[0].text
    )

    for field in (
        "write_scopes_configured",
        "delete_scopes_configured",
        "effective_write_scopes",
        "effective_delete_scopes",
        "registered_tool_count",
    ):
        assert http_payload[field] == stdio_payload[field], f"{field} differs between transports"


async def test_no_auth_token_prints_the_warning_to_stderr() -> None:
    with _HttpServer() as server:
        # Give the warning (printed once at startup, before uvicorn's own banner) time
        # to actually land in the pipe.
        time.sleep(0.3)
        server.process.terminate()
        try:
            server.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.process.kill()
        output = server.process.stdout.read() if server.process.stdout else ""
    assert "no transport-level authentication" in output


@pytest.mark.skipif(Client is None, reason="fastmcp not installed")
async def test_auth_token_configured_rejects_requests_without_it() -> None:
    import httpx

    with _HttpServer(auth_token="s3cret") as server:
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.post(
                server.url,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": "application/json, text/event-stream"},
            )
        assert resp.status_code == 401

        # Wrong token is rejected exactly like no token.
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.post(
                server.url,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Authorization": "Bearer wrong-token",
                },
            )
        assert resp.status_code == 401


@pytest.mark.skipif(Client is None, reason="fastmcp not installed")
async def test_auth_token_configured_allows_requests_with_correct_token() -> None:
    from fastmcp.client.transports import StreamableHttpTransport

    with _HttpServer(auth_token="s3cret") as server:
        transport = StreamableHttpTransport(server.url, headers={"Authorization": "Bearer s3cret"})
        async with Client(transport) as client:
            result = await client.call_tool("get_server_info", {})
    payload = result.data if hasattr(result, "data") else json.loads(result.content[0].text)
    assert payload["name"] == "manager-mcp"

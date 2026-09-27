# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""The Manager API key is never logged, returned by a tool or included in an error.

Every case runs with a distinctive sentinel key. The sentinel must not appear in tool
output, in any exception message (including chained causes) or in log records captured at
DEBUG level for every logger, including httpx and httpcore.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from manager_mcp.client import ManagerClient
from manager_mcp.resources import resolve
from manager_mcp.scopes import ScopeConfigError
from manager_mcp.server import mcp, reset_client

BASE = "http://example.test/api2"
SENTINEL = "SENTINEL-KEY-0123456789abcdef"


@pytest.fixture(autouse=True)
def _env_and_client(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("MANAGER_API_URL", BASE)
    monkeypatch.setenv("MANAGER_API_KEY", SENTINEL)
    monkeypatch.delenv("MANAGER_MCP_WRITE_SCOPES", raising=False)
    monkeypatch.delenv("MANAGER_MCP_DELETE_SCOPES", raising=False)
    reset_client()
    caplog.set_level(logging.DEBUG)
    yield
    reset_client()


def _chain_text(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(f"{type(cur).__name__}: {cur}")
        parts.append(repr(cur))
        cur = cur.__cause__ or cur.__context__
    return "\n".join(parts)


def _assert_clean(caplog: pytest.LogCaptureFixture, *texts: str) -> None:
    for text in texts:
        assert SENTINEL not in text
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert SENTINEL not in logged


async def _read_customers_error(status: int, body: str = "") -> str:
    path = resolve("aged_receivables").path  # type: ignore[union-attr]
    respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(status, text=body))
    with pytest.raises(Exception) as excinfo:
        await mcp.call_tool("aged_receivables", {})
    return _chain_text(excinfo.value)


@pytest.mark.asyncio
@respx.mock
async def test_key_absent_from_successful_tool_output_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = resolve("aged_receivables").path  # type: ignore[union-attr]
    respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json={"customers": []}))
    result = await mcp.call_tool("aged_receivables", {})
    _assert_clean(caplog, json.dumps(result.structured_content), str(result.content))


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 502])
async def test_key_absent_from_http_error_messages(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    text = await _read_customers_error(status, "upstream failure")
    _assert_clean(caplog, text)


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.ConnectTimeout("late")],
)
async def test_key_absent_from_connection_errors(
    error: httpx.RequestError, caplog: pytest.LogCaptureFixture
) -> None:
    path = resolve("aged_receivables").path  # type: ignore[union-attr]
    respx.get(f"{BASE}{path}").mock(side_effect=error)
    with pytest.raises(Exception) as excinfo:
        await mcp.call_tool("aged_receivables", {})
    _assert_clean(caplog, _chain_text(excinfo.value))


@pytest.mark.asyncio
async def test_key_absent_from_server_info_and_resource_listing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    info = await mcp.call_tool("get_server_info", {})
    listing = await mcp.call_tool("list_resources", {})
    _assert_clean(
        caplog,
        json.dumps(info.structured_content, default=str),
        json.dumps(listing.structured_content, default=str),
    )


@pytest.mark.asyncio
async def test_key_absent_from_every_tool_definition() -> None:
    tools = await mcp.list_tools()
    blob = json.dumps(
        [{"n": t.name, "d": t.description, "p": t.parameters} for t in tools], default=str
    )
    assert SENTINEL not in blob


@pytest.mark.asyncio
@respx.mock
async def test_denied_write_error_does_not_contain_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = ManagerClient(BASE, SENTINEL)
    route = respx.post(f"{BASE}/journal-entry-form").mock(return_value=httpx.Response(200))
    with pytest.raises(Exception) as excinfo:
        await client.post("/journal-entry-form", json={})
    assert not route.called
    _assert_clean(caplog, _chain_text(excinfo.value))
    await client.aclose()


def test_key_absent_from_client_repr_and_scope_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ManagerClient(BASE, SENTINEL)
    assert SENTINEL not in repr(client)
    assert SENTINEL not in repr(client.policy)
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "not-a-scope")
    reset_client()
    with pytest.raises(ScopeConfigError) as excinfo:
        from manager_mcp.server import get_policy

        get_policy()
    assert SENTINEL not in _chain_text(excinfo.value)

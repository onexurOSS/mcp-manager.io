"""Quotes scoped write tools (respx; no live Manager)."""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp.client import ManagerClient
from manager_mcp.scopes import WritePolicy, WritesDeniedError
from manager_mcp.server import mcp, register_write_tools, reset_client


@pytest.fixture
def quotes_policy() -> WritePolicy:
    return WritePolicy(frozenset({"quotes"}), frozenset({"quotes"}))


@pytest.mark.asyncio
@respx.mock
async def test_create_update_delete_sales_quote(quotes_policy: WritePolicy) -> None:
    key = "11111111-1111-1111-1111-111111111111"
    create_route = respx.post("http://example.test/api2/sales-quote-form").mock(
        return_value=httpx.Response(201, json={"Key": key, "Description": "probe"})
    )
    put_route = respx.put(f"http://example.test/api2/sales-quote-form/{key}").mock(
        return_value=httpx.Response(200, json={"Key": key, "Description": "updated"})
    )
    delete_route = respx.delete(f"http://example.test/api2/sales-quote-form/{key}").mock(
        return_value=httpx.Response(204)
    )

    client = ManagerClient(
        "http://example.test/api2",
        "secret",
        policy=quotes_policy,
    )
    created = await client.post(
        "/sales-quote-form",
        json={"Description": "probe", "Customer": "c"},
    )
    assert created["Key"] == key
    updated = await client.put(
        f"/sales-quote-form/{key}",
        json={"Key": key, "Description": "updated"},
    )
    assert updated["Description"] == "updated"
    assert await client.delete(f"/sales-quote-form/{key}") is None
    assert create_route.called
    assert put_route.called
    assert delete_route.called
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_create_purchase_quote(quotes_policy: WritePolicy) -> None:
    respx.post("http://example.test/api2/purchase-quote-form").mock(
        return_value=httpx.Response(
            201,
            json={"Key": "22222222-2222-2222-2222-222222222222", "Supplier": "s"},
        )
    )
    client = ManagerClient(
        "http://example.test/api2",
        "secret",
        policy=quotes_policy,
    )
    body = await client.post(
        "/purchase-quote-form",
        json={"Supplier": "s", "Date": "2026-07-28", "Description": "pq"},
    )
    assert body["Key"].startswith("2222")
    await client.aclose()


@pytest.mark.asyncio
async def test_delete_denied_without_delete_scope() -> None:
    client = ManagerClient(
        "http://example.test/api2",
        "secret",
        policy=WritePolicy(frozenset({"quotes"}), frozenset()),
    )
    with pytest.raises(WritesDeniedError, match="DELETE_SCOPES"):
        await client.delete("/sales-quote-form/abc")
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_denylisted_quote_email_template_never_called() -> None:
    route = respx.post(
        "http://example.test/api2/email-template-for-sales-quote-form"
    ).mock(return_value=httpx.Response(201, json={"Key": "x"}))
    client = ManagerClient(
        "http://example.test/api2",
        "secret",
        policy=WritePolicy(frozenset({"quotes"}), frozenset({"quotes"})),
    )
    with pytest.raises(WritesDeniedError, match="denylist"):
        await client.post("/email-template-for-sales-quote-form", json={})
    assert not route.called
    await client.aclose()


def test_quotes_tool_names_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "quotes")
    monkeypatch.setenv("MANAGER_MCP_DELETE_SCOPES", "quotes")
    for name in ("MANAGER_MCP_ALLOW_WRITES", "ALLOW_WRITES", "MANAGER_MCP_WRITES"):
        monkeypatch.delenv(name, raising=False)
    reset_client()

    captured: list[str] = []

    def fake_tool(*_args: object, **kwargs: object):
        def deco(fn: object) -> object:
            captured.append(str(kwargs.get("name") or getattr(fn, "__name__", "")))
            return fn

        return deco

    monkeypatch.setattr(mcp, "tool", fake_tool)
    register_write_tools()
    assert set(captured) == {
        "create_sales_quote",
        "update_sales_quote",
        "delete_sales_quote",
        "create_purchase_quote",
        "update_purchase_quote",
        "delete_purchase_quote",
    }


def test_write_only_registers_create_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "quotes")
    monkeypatch.delenv("MANAGER_MCP_DELETE_SCOPES", raising=False)
    for name in ("MANAGER_MCP_ALLOW_WRITES", "ALLOW_WRITES", "MANAGER_MCP_WRITES"):
        monkeypatch.delenv(name, raising=False)
    reset_client()
    captured: list[str] = []

    def fake_tool(*_args: object, **kwargs: object):
        def deco(fn: object) -> object:
            captured.append(str(kwargs.get("name") or getattr(fn, "__name__", "")))
            return fn

        return deco

    monkeypatch.setattr(mcp, "tool", fake_tool)
    register_write_tools()
    assert set(captured) == {
        "create_sales_quote",
        "update_sales_quote",
        "create_purchase_quote",
        "update_purchase_quote",
    }
    assert not any(n.startswith("delete_") for n in captured)

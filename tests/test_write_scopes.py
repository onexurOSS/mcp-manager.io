"""Scoped write registration + client authorize for non-quote domains (respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp.client import ManagerClient
from manager_mcp.scopes import WritePolicy, WritesDeniedError
from manager_mcp.server import mcp, register_write_tools, reset_client
from manager_mcp.writable import WRITABLE, implemented_for_scope


@pytest.mark.parametrize(
    ("scope", "form_path"),
    [
        ("orders", "/sales-order-form"),
        ("parties", "/customer-form"),
        ("items", "/inventory-item-form"),
        ("sales", "/sales-invoice-form"),
        ("purchases", "/purchase-invoice-form"),
        ("banking", "/receipt-form"),
        ("payroll", "/employee-form"),
        ("ledger", "/journal-entry-form"),
    ],
)
@pytest.mark.asyncio
@respx.mock
async def test_post_allowed_for_scope(scope: str, form_path: str) -> None:
    respx.post(f"http://example.test/api2{form_path}").mock(
        return_value=httpx.Response(201, json={"Key": "k"})
    )
    client = ManagerClient(
        "http://example.test/api2",
        "secret",
        policy=WritePolicy(frozenset({scope}), frozenset()),
    )
    assert await client.post(form_path, json={}) == {"Key": "k"}
    await client.aclose()


@pytest.mark.asyncio
async def test_cross_scope_denied() -> None:
    client = ManagerClient(
        "http://example.test/api2",
        "secret",
        policy=WritePolicy(frozenset({"orders"}), frozenset()),
    )
    with pytest.raises(WritesDeniedError, match="WRITE_SCOPES"):
        await client.post("/customer-form", json={"Name": "x"})
    await client.aclose()


def test_all_scopes_have_implemented_resources() -> None:
    expected = {
        "quotes",
        "orders",
        "parties",
        "items",
        "sales",
        "purchases",
        "banking",
        "payroll",
        "ledger",
    }
    found = {w.scope for w in WRITABLE.values() if w.implemented}
    assert found == expected


def test_parties_tool_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "parties")
    monkeypatch.setenv("MANAGER_MCP_DELETE_SCOPES", "parties")
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
        "create_customer",
        "update_customer",
        "delete_customer",
        "create_supplier",
        "update_supplier",
        "delete_supplier",
    }


def test_orders_implemented_stems() -> None:
    stems = {w.tool_stem for w in implemented_for_scope("orders")}
    assert stems == {"sales_order", "purchase_order"}

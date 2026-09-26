"""Live read tools via mcp.call_tool()."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_list_resources_returns_tools_list(call_tool) -> None:
    out = await call_tool("list_resources")
    names = {r["name"] for r in out["resources"]}
    assert "customers" in names
    assert out["read_only"] is False


@pytest.mark.asyncio
async def test_list_records_customers(call_tool) -> None:
    out = await call_tool("list_records", {"resource": "customers"})
    assert isinstance(out["items"], list)
    for item in out["items"]:
        assert "key" in item or "Key" in item


@pytest.mark.asyncio
async def test_get_record_customer_by_key(call_tool, temp_customer) -> None:
    out = await call_tool("get_record", {"resource": "customers", "key": temp_customer})
    assert out["body"]["Key"] == temp_customer
    assert out["body"]["Name"]


@pytest.mark.asyncio
async def test_aged_receivables_returns_data(call_tool) -> None:
    out = await call_tool("aged_receivables")
    assert out["report"] == "aged_receivables"
    assert out["body"] is not None


@pytest.mark.asyncio
async def test_aged_payables_returns_data(call_tool) -> None:
    out = await call_tool("aged_payables")
    assert out["report"] == "aged_payables"
    assert out["body"] is not None


@pytest.mark.asyncio
async def test_bank_balances_returns_data(call_tool) -> None:
    out = await call_tool("bank_balances")
    assert out["report"] == "bank_balances"
    assert out["body"] is not None


@pytest.mark.asyncio
async def test_trial_balance_returns_data(call_tool) -> None:
    out = await call_tool("trial_balance")
    assert out["report"] == "trial_balance"
    assert out["body"] is not None


@pytest.mark.asyncio
async def test_profit_and_loss_returns_data(call_tool) -> None:
    out = await call_tool("profit_and_loss")
    assert out["report"] == "profit_and_loss"
    assert out["body"] is not None


@pytest.mark.asyncio
async def test_balance_sheet_returns_data(call_tool) -> None:
    out = await call_tool("balance_sheet")
    assert out["report"] == "balance_sheet"
    assert out["body"] is not None


@pytest.mark.asyncio
async def test_tax_summary_returns_data(call_tool) -> None:
    out = await call_tool("tax_summary")
    assert out["report"] == "tax_summary"
    assert out["body"] is not None

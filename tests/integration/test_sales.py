"""Live sales task tools."""

from __future__ import annotations

import pytest

from manager_mcp.task_tools import (
    convert_quote_to_invoice,
    issue_quote,
    issue_sales_invoice,
    void_document,
)

pytestmark = pytest.mark.integration

_TODAY = "2026-07-29"


def _invoice_fields(customer: str, income_account: str, amount: float = 100.0) -> dict:
    return {
        "Customer": customer,
        "IssueDate": _TODAY,
        "Lines": [
            {
                "Account": income_account,
                "LineDescription": "Integration line",
                "Qty": 1,
                "SalesUnitPrice": amount,
            }
        ],
    }


def _quote_fields(customer: str, income_account: str, amount: float = 75.0) -> dict:
    return {
        "Customer": customer,
        "IssueDate": _TODAY,
        "Description": "Integration quote",
        "Lines": [
            {
                "Account": income_account,
                "LineDescription": "Quote line",
                "Qty": 1,
                "SalesUnitPrice": amount,
            }
        ],
    }


@pytest.mark.asyncio
async def test_issue_sales_invoice_returns_ok_and_key(
    live_client,
    temp_customer,
    income_account_key,
) -> None:
    out = await issue_sales_invoice(
        live_client,
        live_client.policy,
        _invoice_fields(temp_customer, income_account_key),
    )
    key = out["keys"]["sales_invoice"]
    try:
        assert out["status"] == "ok"
        assert key
        fetched = await live_client.get(f"/sales-invoice-form/{key}")
        assert fetched["Customer"] == temp_customer
    finally:
        await void_document(live_client, live_client.policy, "sales_invoices", key)


@pytest.mark.asyncio
async def test_void_document_removes_invoice(
    live_client,
    temp_customer,
    income_account_key,
) -> None:
    out = await issue_sales_invoice(
        live_client,
        live_client.policy,
        _invoice_fields(temp_customer, income_account_key, amount=50.0),
    )
    key = out["keys"]["sales_invoice"]
    await void_document(live_client, live_client.policy, "sales_invoices", key)
    listed = await live_client.get(
        "/sales-invoices",
        params={"skip": 0, "pageSize": 50},
    )
    items = listed.get("salesInvoices", []) if isinstance(listed, dict) else []
    keys = {str(item.get("Key") or item.get("key")) for item in items if isinstance(item, dict)}
    assert key not in keys


@pytest.mark.asyncio
async def test_issue_quote_returns_ok_and_key(
    live_client,
    temp_customer,
    income_account_key,
) -> None:
    out = await issue_quote(
        live_client,
        live_client.policy,
        _quote_fields(temp_customer, income_account_key),
    )
    key = out["keys"]["sales_quote"]
    try:
        assert out["status"] == "ok"
        assert key
    finally:
        await void_document(live_client, live_client.policy, "sales_quotes", key)


@pytest.mark.asyncio
async def test_convert_quote_to_invoice_creates_invoice(
    live_client,
    temp_customer,
    income_account_key,
) -> None:
    quote_out = await issue_quote(
        live_client,
        live_client.policy,
        _quote_fields(temp_customer, income_account_key, amount=80.0),
    )
    quote_key = quote_out["keys"]["sales_quote"]
    invoice_key = ""
    try:
        out = await convert_quote_to_invoice(
            live_client,
            live_client.policy,
            quote_key,
        )
        assert out["status"] == "ok"
        invoice_key = out["keys"]["sales_invoice"]
        assert invoice_key
        assert out["keys"]["quote"] == quote_key
    finally:
        if invoice_key:
            await void_document(live_client, live_client.policy, "sales_invoices", invoice_key)
        await void_document(live_client, live_client.policy, "sales_quotes", quote_key)

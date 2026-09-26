"""Live customer deposit workflow."""

from __future__ import annotations

import pytest

from manager_mcp import task_tools
from manager_mcp.task_tools import (
    apply_deposit_to_invoice,
    issue_deposit_invoice,
    issue_sales_invoice,
    record_customer_deposit,
    void_document,
)

pytestmark = pytest.mark.integration

_TODAY = "2026-07-29"


@pytest.mark.asyncio
async def test_record_customer_deposit_precondition_when_no_deposit_account(
    live_client,
    temp_customer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_account(_client: object) -> None:
        return None

    monkeypatch.setattr(task_tools, "_find_deposit_bank_account", _no_account)
    out = await record_customer_deposit(
        live_client,
        live_client.policy,
        customer=temp_customer,
        amount=50.0,
        date=_TODAY,
        bank_account=None,
    )
    assert out["status"] == "precondition_failed"
    assert out["keys"] == {}
    assert out["preconditions"]["ok"] is False
    assert out["next_steps"]


@pytest.mark.asyncio
async def test_record_customer_deposit_happy_path(
    live_client,
    temp_customer,
    deposit_bank_account_key,
) -> None:
    out = await record_customer_deposit(
        live_client,
        live_client.policy,
        customer=temp_customer,
        amount=50.0,
        date=_TODAY,
        bank_account=deposit_bank_account_key,
    )
    receipt_key = out["keys"]["receipt"]
    try:
        assert out["status"] == "ok"
        assert receipt_key
        assert out["keys"]["deposit_account"] == deposit_bank_account_key
    finally:
        await void_document(live_client, live_client.policy, "receipts", receipt_key)


@pytest.mark.asyncio
async def test_record_customer_deposit_discovers_deposit_account(
    live_client,
    temp_customer,
    deposit_bank_account_key,
) -> None:
    out = await record_customer_deposit(
        live_client,
        live_client.policy,
        customer=temp_customer,
        amount=25.0,
        date=_TODAY,
    )
    receipt_key = out["keys"].get("receipt", "")
    try:
        assert out["status"] == "ok"
        assert receipt_key
        assert out["keys"]["deposit_account"] == deposit_bank_account_key
    finally:
        if receipt_key:
            await void_document(live_client, live_client.policy, "receipts", receipt_key)


@pytest.mark.asyncio
async def test_issue_deposit_invoice_returns_ok(
    live_client,
    temp_customer,
    income_account_key,
) -> None:
    out = await issue_deposit_invoice(
        live_client,
        live_client.policy,
        {
            "Customer": temp_customer,
            "IssueDate": _TODAY,
            "Description": "Deposit invoice",
            "Lines": [
                {
                    "Account": income_account_key,
                    "LineDescription": "Deposit",
                    "Qty": 1,
                    "SalesUnitPrice": 50.0,
                }
            ],
        },
    )
    key = out["keys"]["sales_quote"]
    try:
        assert out["status"] == "ok"
        assert key
        assert "deposit" in str(out["body"].get("Description", "")).casefold()
    finally:
        await void_document(live_client, live_client.policy, "sales_quotes", key)


@pytest.mark.asyncio
async def test_apply_deposit_to_invoice_posts_journal(
    live_client,
    temp_customer,
    deposit_bank_account_key,
    income_account_key,
) -> None:
    deposit_out = await record_customer_deposit(
        live_client,
        live_client.policy,
        customer=temp_customer,
        amount=30.0,
        date=_TODAY,
        bank_account=deposit_bank_account_key,
    )
    receipt_key = deposit_out["keys"]["receipt"]
    invoice_out = await issue_sales_invoice(
        live_client,
        live_client.policy,
        {
            "Customer": temp_customer,
            "IssueDate": _TODAY,
            "Lines": [
                {
                    "Account": income_account_key,
                    "LineDescription": "Deposit apply test",
                    "Qty": 1,
                    "SalesUnitPrice": 30.0,
                }
            ],
        },
    )
    invoice_key = invoice_out["keys"]["sales_invoice"]
    journal_key = ""
    try:
        out = await apply_deposit_to_invoice(
            live_client,
            live_client.policy,
            {
                "Date": _TODAY,
                "Narration": "Apply customer deposit to invoice",
                "Lines": [
                    {
                        "Account": deposit_bank_account_key,
                        "Credit": 30.0,
                    },
                    {
                        "Account": income_account_key,
                        "Debit": 30.0,
                    },
                ],
            },
        )
        assert out["status"] == "ok"
        journal_key = out["keys"]["journal_entry"]
        assert journal_key
    finally:
        if journal_key:
            await void_document(live_client, live_client.policy, "journal_entries", journal_key)
        await void_document(live_client, live_client.policy, "receipts", receipt_key)
        await void_document(live_client, live_client.policy, "sales_invoices", invoice_key)

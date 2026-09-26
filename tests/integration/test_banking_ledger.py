"""Live banking and ledger task tools."""

from __future__ import annotations

import pytest

from manager_mcp.task_tools import (
    post_journal_entry,
    record_expense,
    transfer_between_accounts,
    void_document,
)

pytestmark = pytest.mark.integration

_TODAY = "2026-07-29"


@pytest.mark.asyncio
async def test_transfer_between_accounts_returns_ok(
    live_client,
    bank_account_key,
    second_bank_account_key,
) -> None:
    out = await transfer_between_accounts(
        live_client,
        live_client.policy,
        {
            "Date": _TODAY,
            "From": bank_account_key,
            "To": second_bank_account_key,
            "Amount": 10.0,
        },
    )
    key = out["keys"]["transfer"]
    try:
        assert out["status"] == "ok"
        assert key
    finally:
        await void_document(live_client, live_client.policy, "inter_account_transfers", key)


@pytest.mark.asyncio
async def test_post_journal_entry_returns_ok(
    live_client,
    income_account_key,
) -> None:
    retained = None
    coa = await live_client.get("/chart-of-accounts", params={"skip": 0, "pageSize": 50})
    if isinstance(coa, dict):
        for item in coa.get("chartOfAccounts") or []:
            if isinstance(item, dict) and item.get("Key") and item["Key"] != income_account_key:
                retained = str(item["Key"])
                break
    if not retained:
        pytest.skip("integration: need a second account for journal entry")
    out = await post_journal_entry(
        live_client,
        live_client.policy,
        {
            "Date": _TODAY,
            "Lines": [
                {"Account": income_account_key, "Credit": 5.0},
                {"Account": retained, "Debit": 5.0},
            ],
        },
    )
    key = out["keys"]["journal_entry"]
    try:
        assert out["status"] == "ok"
        assert key
    finally:
        await void_document(live_client, live_client.policy, "journal_entries", key)


@pytest.mark.asyncio
async def test_record_expense_returns_ok(
    live_client,
    temp_supplier,
    expense_account_key,
) -> None:
    out = await record_expense(
        live_client,
        live_client.policy,
        {
            "Supplier": temp_supplier,
            "IssueDate": _TODAY,
            "Lines": [
                {
                    "Account": expense_account_key,
                    "LineDescription": "Integration expense",
                    "Qty": 1,
                    "UnitPrice": 15.0,
                }
            ],
        },
        via="purchases",
    )
    key = out["keys"]["purchase_invoice"]
    try:
        assert out["status"] == "ok"
        assert key
    finally:
        await void_document(live_client, live_client.policy, "purchase_invoices", key)

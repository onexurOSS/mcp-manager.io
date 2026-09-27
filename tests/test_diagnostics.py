# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Structured-evidence-only diagnostics (respx; no live Manager, no guessing).

Mocks reflect the real, live-verified Manager shape: list endpoints return
a flattened camelCase summary with no Lines[] at all; Lines/AR-AP
allocation fields exist only on the per-record form endpoint (PascalCase).
Every diagnostic that needs Lines therefore lists (cheap) then fetches
each record's form (paging.fetch_all_forms) -- tests mock both tiers.

Every duplicate/broken-reference/unallocated check here is asserted on
exact structured-field equality. Tests also assert the negative: a
near-match that differs on one structured field must NOT be flagged.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp.client import ManagerClient
from manager_mcp.diagnostics import (
    account_ledger,
    bank_activity,
    find_broken_invoice_references,
    find_duplicate_transactions,
    find_records,
    find_suspense_candidate_accounts,
    find_unallocated_transactions,
    general_ledger_summary,
    verify_invoice_balance,
)

BASE = "http://example.test/api2"


def _list_envelope(items_key: str, rows: list[dict]) -> dict:
    return {"totalRecords": len(rows), items_key: rows}


def _mock_list(resource_path: str, items_key: str, rows: list[dict]) -> None:
    respx.get(f"{BASE}/{resource_path}").mock(
        return_value=httpx.Response(200, json=_list_envelope(items_key, rows))
    )


def _mock_form(form_path: str, key: str, body: dict) -> None:
    respx.get(f"{BASE}/{form_path}/{key}").mock(return_value=httpx.Response(200, json=body))


@pytest.fixture
def client() -> ManagerClient:
    return ManagerClient(BASE, "k")


_LEDGER_LIST_PATHS = {
    "receipts": ("receipts", "receipts"),
    "payments": ("payments", "payments"),
    "journal_entries": ("journal-entries", "journalEntries"),
    "inter_account_transfers": ("inter-account-transfers", "interAccountTransfers"),
    "sales_invoices": ("sales-invoices", "salesInvoices"),
    "purchase_invoices": ("purchase-invoices", "purchaseInvoices"),
}


def _mock_all_ledger_sources_empty() -> None:
    for path, items_key in _LEDGER_LIST_PATHS.values():
        _mock_list(path, items_key, [])


# --------------------------------------------------------------------------
# find_records (list-level, cheap filter)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_find_records_exact_match_only(client: ManagerClient) -> None:
    _mock_list(
        "customers",
        "customers",
        [{"key": "c1", "name": "Acme Ltd"}, {"key": "c2", "name": "Acme Ltd 2"}],
    )
    out = await find_records(client, "customers", {"name": "Acme Ltd"})
    await client.aclose()
    assert [i["key"] for i in out] == ["c1"]


# --------------------------------------------------------------------------
# find_unallocated_transactions
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_find_unallocated_receipts(client: ManagerClient) -> None:
    _mock_list("receipts", "receipts", [{"key": "r1"}, {"key": "r2"}])
    _mock_form(
        "receipt-form",
        "r1",
        {
            "Key": "r1",
            "Date": "2025-03-15",
            "Lines": [{"Amount": 10, "AccountsReceivableSalesInvoice": "inv-1"}],
        },
    )
    _mock_form(
        "receipt-form",
        "r2",
        {"Key": "r2", "Date": "2025-03-15", "Lines": [{"Amount": 5, "Account": "misc-account"}]},
    )
    out = await find_unallocated_transactions(client, "receipts")
    await client.aclose()
    assert [i["Key"] for i in out] == ["r2"]


# --------------------------------------------------------------------------
# find_broken_invoice_references (missing-invoice payment pattern, general form)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_find_broken_invoice_references_flags_missing_invoice(client: ManagerClient) -> None:
    _mock_list("payments", "payments", [{"key": "pay-a"}, {"key": "pay-b"}])
    _mock_form(
        "payment-form",
        "pay-a",
        {
            "Key": "pay-a",
            "Date": "2025-03-15",
            "Supplier": "example-supplier-key",
            "Lines": [{"Amount": 412.50, "PurchaseInvoice": "missing-inv-key"}],
        },
    )
    _mock_form(
        "payment-form",
        "pay-b",
        {
            "Key": "pay-b",
            "Date": "2025-03-15",
            "Supplier": "example-supplier-key",
            "Lines": [{"Amount": 2.50, "PurchaseInvoice": "missing-inv-key"}],
        },
    )
    respx.get(f"{BASE}/purchase-invoice-form/missing-inv-key").mock(
        return_value=httpx.Response(404, json={})
    )
    out = await find_broken_invoice_references(client, "payments")
    await client.aclose()
    assert {i["key"] for i in out} == {"pay-a", "pay-b"}
    assert all(i["missing_invoice_key"] == "missing-inv-key" for i in out)


@pytest.mark.asyncio
@respx.mock
async def test_find_broken_invoice_references_ignores_resolved_invoice(
    client: ManagerClient,
) -> None:
    _mock_list("payments", "payments", [{"key": "pay-b"}])
    _mock_form(
        "payment-form",
        "pay-b",
        {
            "Key": "pay-b",
            "Date": "2025-03-15",
            "Lines": [{"Amount": 100, "PurchaseInvoice": "real-inv"}],
        },
    )
    respx.get(f"{BASE}/purchase-invoice-form/real-inv").mock(
        return_value=httpx.Response(200, json={"Key": "real-inv"})
    )
    out = await find_broken_invoice_references(client, "payments")
    await client.aclose()
    assert out == []


@pytest.mark.asyncio
async def test_find_broken_invoice_references_rejects_wrong_resource(
    client: ManagerClient,
) -> None:
    with pytest.raises(ValueError, match="receipts.*payments"):
        await find_broken_invoice_references(client, "sales_invoices")
    await client.aclose()


# --------------------------------------------------------------------------
# find_duplicate_transactions
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_exact_duplicate_flagged(client: ManagerClient) -> None:
    row = {
        "Key": "pi-1",
        "Supplier": "sup-1",
        "Reference": "INV-100",
        "IssueDate": "2025-03-15",
        "Lines": [{"Account": "expenses", "Qty": 1.0, "PurchaseUnitPrice": 50.0}],
    }
    dup = {**row, "Key": "pi-2"}
    _mock_list("purchase-invoices", "purchaseInvoices", [{"key": "pi-1"}, {"key": "pi-2"}])
    _mock_form("purchase-invoice-form", "pi-1", row)
    _mock_form("purchase-invoice-form", "pi-2", dup)
    respx.get(f"{BASE}/purchase-invoices", params={"skip": 0, "pageSize": 1}).mock(
        return_value=httpx.Response(
            200,
            json=_list_envelope(
                "purchaseInvoices",
                [
                    {"key": "pi-1", "invoiceAmount": {"value": 50.0}},
                    {"key": "pi-2", "invoiceAmount": {"value": 50.0}},
                ],
            ),
        )
    )
    out = await find_duplicate_transactions(client, "purchase_invoices")
    await client.aclose()
    assert out["duplicate_count"] == 2
    assert set(out["duplicate_groups"][0]["keys"]) == {"pi-1", "pi-2"}
    assert out["unresolved_count"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_partial_match_is_unresolved_not_duplicate(client: ManagerClient) -> None:
    # Same Supplier + Reference, but no IssueDate -- must NOT be
    # auto-flagged as a duplicate on partial evidence; unresolved instead.
    row1 = {
        "Key": "pi-1",
        "Supplier": "sup-1",
        "Reference": "INV-100",
        "Lines": [{"Account": "expenses", "Qty": 1.0, "PurchaseUnitPrice": 50.0}],
    }
    row2 = {
        "Key": "pi-2",
        "Supplier": "sup-1",
        "Reference": "INV-100",
        "Lines": [{"Account": "expenses", "Qty": 1.0, "PurchaseUnitPrice": 999.0}],
    }
    _mock_list("purchase-invoices", "purchaseInvoices", [{"key": "pi-1"}, {"key": "pi-2"}])
    _mock_form("purchase-invoice-form", "pi-1", row1)
    _mock_form("purchase-invoice-form", "pi-2", row2)
    out = await find_duplicate_transactions(client, "purchase_invoices")
    await client.aclose()
    assert out["duplicate_count"] == 0
    assert out["unresolved_count"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_different_amount_not_flagged_as_duplicate(client: ManagerClient) -> None:
    row1 = {
        "Key": "pi-1",
        "Supplier": "sup-1",
        "Reference": "INV-100",
        "IssueDate": "2025-03-15",
        "Lines": [{"Account": "expenses", "Qty": 1.0, "PurchaseUnitPrice": 50.0}],
    }
    row2 = {
        "Key": "pi-2",
        "Supplier": "sup-1",
        "Reference": "INV-100",
        "IssueDate": "2025-03-15",
        "Lines": [{"Account": "expenses", "Qty": 1.0, "PurchaseUnitPrice": 51.0}],
    }
    _mock_list("purchase-invoices", "purchaseInvoices", [{"key": "pi-1"}, {"key": "pi-2"}])
    _mock_form("purchase-invoice-form", "pi-1", row1)
    _mock_form("purchase-invoice-form", "pi-2", row2)
    out = await find_duplicate_transactions(client, "purchase_invoices")
    await client.aclose()
    assert out["duplicate_count"] == 0


# --------------------------------------------------------------------------
# verify_invoice_balance
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_verify_invoice_balance_partial_payment(client: ManagerClient) -> None:
    _mock_form(
        "purchase-invoice-form",
        "inv-1",
        {
            "Key": "inv-1",
            "Lines": [
                {"Account": "hw", "Qty": 1.0, "PurchaseUnitPrice": 380.00},
                {"Account": "hw", "Qty": 1.0, "PurchaseUnitPrice": 35.00},
            ],
        },
    )
    respx.get(f"{BASE}/purchase-invoices", params={"skip": 0, "pageSize": 200}).mock(
        return_value=httpx.Response(
            200,
            json=_list_envelope(
                "purchaseInvoices",
                [
                    {
                        "key": "inv-1",
                        "invoiceAmount": {"value": 415.00},
                        "balanceDue": {"value": 0.0},
                    }
                ],
            ),
        )
    )
    _mock_list("payments", "payments", [{"key": "pay-a"}, {"key": "pay-b"}])
    _mock_form(
        "payment-form",
        "pay-a",
        {
            "Key": "pay-a",
            "Date": "2025-03-15",
            "Lines": [{"Amount": 412.50, "PurchaseInvoice": "inv-1"}],
        },
    )
    _mock_form(
        "payment-form",
        "pay-b",
        {
            "Key": "pay-b",
            "Date": "2025-03-15",
            "Lines": [{"Amount": 2.50, "PurchaseInvoice": "inv-1"}],
        },
    )
    out = await verify_invoice_balance(client, "purchase_invoices", "inv-1")
    await client.aclose()
    assert out["invoice_total"] == 415.00
    assert out["allocated_total"] == 415.00
    assert out["outstanding"] == 0.0
    assert out["manager_reported_balance_due"] == 0.0
    assert out["fully_paid"] is True
    assert {c["key"] for c in out["contributing"]} == {"pay-a", "pay-b"}


# --------------------------------------------------------------------------
# account_ledger / general_ledger_summary
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_account_ledger_filters_by_account(client: ManagerClient) -> None:
    _mock_all_ledger_sources_empty()
    _mock_list("payments", "payments", [{"key": "pay-b"}])
    _mock_form(
        "payment-form",
        "pay-b",
        {
            "Key": "pay-b",
            "Date": "2025-03-15",
            "Lines": [{"Amount": -40.0, "Account": "office-supplies"}],
        },
    )
    out = await account_ledger(client, "office-supplies")
    await client.aclose()
    assert out["net"] == -40.0
    assert out["entries"][0]["source"] == "payments"


@pytest.mark.asyncio
@respx.mock
async def test_general_ledger_summary_per_account_and_source(client: ManagerClient) -> None:
    _mock_all_ledger_sources_empty()
    _mock_list("journal-entries", "journalEntries", [{"key": "je-1"}])
    _mock_form(
        "journal-entry-form",
        "je-1",
        {
            "Key": "je-1",
            "Date": "2025-06-01",
            "Lines": [
                {"Account": "bank", "Amount": 100.0},
                {"Account": "expenses", "Amount": -100.0},
            ],
        },
    )
    out = await general_ledger_summary(client)
    await client.aclose()
    assert out["overall_net"] == 0.0
    assert "balanced" not in out  # not a valid trial-balance signal; see notice
    assert out["per_account_net"]["bank"] == 100.0
    assert out["per_account_net"]["expenses"] == -100.0
    assert out["per_source_net"]["journal_entries"] == 0.0


# --------------------------------------------------------------------------
# bank_activity
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_bank_activity_money_in_and_out(client: ManagerClient) -> None:
    _mock_list("receipts", "receipts", [{"key": "r1"}])
    _mock_form(
        "receipt-form",
        "r1",
        {
            "Key": "r1",
            "Date": "2025-03-15",
            "ReceivedIn": "bank-1",
            "Lines": [{"Amount": 200.0}],
        },
    )
    _mock_list("payments", "payments", [{"key": "p1"}])
    _mock_form(
        "payment-form",
        "p1",
        {
            "Key": "p1",
            "Date": "2025-03-15",
            "PaidFrom": "bank-1",
            "Lines": [{"Amount": 50.0}],
        },
    )
    _mock_list("inter-account-transfers", "interAccountTransfers", [])
    out = await bank_activity(client, "bank-1")
    await client.aclose()
    assert out["total_money_in"] == 200.0
    assert out["total_money_out"] == 50.0
    assert out["net_movement"] == 150.0


# --------------------------------------------------------------------------
# find_suspense_candidate_accounts
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_find_suspense_candidate_accounts_by_structured_name(client: ManagerClient) -> None:
    _mock_list(
        "chart-of-accounts",
        "chartOfAccounts",
        [
            {"key": "a1", "name": "Suspense Account"},
            {"key": "a2", "name": "Office Supplies"},
        ],
    )
    out = await find_suspense_candidate_accounts(client)
    await client.aclose()
    assert [a["key"] for a in out] == ["a1"]

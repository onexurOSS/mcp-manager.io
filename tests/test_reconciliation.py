# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""reconcile_period composition (respx; no live Manager).

Focus: the report is built entirely from Manager transaction data, every
exception carries Manager Keys, and unverified/unavailable report shapes
(P&L, balance sheet, tax summary) are surfaced with an explicit notice
rather than a fabricated total.

Mocks reflect the real, live-verified two-tier shape: a cheap list
envelope (camelCase, `key` only needed here) followed by a per-record
form fetch (PascalCase, carries Lines) for every transaction-bearing
resource reconcile_period touches.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp.client import ManagerClient
from manager_mcp.reconciliation import reconcile_period

BASE = "http://example.test/api2"


def _envelope(items_key: str, items: list) -> dict:
    return {"totalRecords": len(items), items_key: items}


@pytest.fixture
def client() -> ManagerClient:
    return ManagerClient(BASE, "k")


def _mock_form(form_path: str, key: str, body: dict) -> None:
    respx.get(f"{BASE}/{form_path}/{key}").mock(return_value=httpx.Response(200, json=body))


def _mock_baseline() -> None:
    respx.get(f"{BASE}/sales-invoices").mock(
        return_value=httpx.Response(200, json=_envelope("salesInvoices", []))
    )
    respx.get(f"{BASE}/purchase-invoices").mock(
        return_value=httpx.Response(
            200,
            json=_envelope(
                "purchaseInvoices", [{"key": "inv-1", "invoiceAmount": {"value": 412.50}}]
            ),
        )
    )
    _mock_form(
        "purchase-invoice-form",
        "inv-1",
        {"Key": "inv-1", "Supplier": "example-supplier", "IssueDate": "2025-03-15", "Lines": []},
    )
    respx.get(f"{BASE}/receipts").mock(
        return_value=httpx.Response(200, json=_envelope("receipts", []))
    )
    respx.get(f"{BASE}/payments").mock(
        return_value=httpx.Response(200, json=_envelope("payments", [{"key": "pay-a"}]))
    )
    _mock_form(
        "payment-form",
        "pay-a",
        {
            "Key": "pay-a",
            "Date": "2025-03-15",
            "Lines": [{"Amount": 412.50, "PurchaseInvoice": "inv-1"}],
        },
    )
    respx.get(f"{BASE}/journal-entries").mock(
        return_value=httpx.Response(200, json=_envelope("journalEntries", []))
    )
    respx.get(f"{BASE}/inter-account-transfers").mock(
        return_value=httpx.Response(200, json=_envelope("interAccountTransfers", []))
    )
    respx.get(f"{BASE}/chart-of-accounts").mock(
        return_value=httpx.Response(200, json=_envelope("chartOfAccounts", []))
    )
    respx.get(f"{BASE}/profit-and-loss-statement-transactions").mock(
        return_value=httpx.Response(200, json={"profitAndLossStatementTransactions": []})
    )
    respx.get(f"{BASE}/balance-sheet-transactions").mock(
        return_value=httpx.Response(200, json={"balanceSheetTransactions": []})
    )
    respx.get(f"{BASE}/tax-summary-transactions").mock(
        return_value=httpx.Response(200, json={"taxSummaryTransactions": []})
    )


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_period_shape_and_keys(client: ManagerClient) -> None:
    _mock_baseline()
    out = await reconcile_period(client, "2025-01-01", "2025-12-31")
    await client.aclose()

    assert out["period"] == {"from": "2025-01-01", "to": "2025-12-31"}
    assert out["purchase_invoices"]["current_count"] == 1
    assert out["purchase_invoices"]["total"] == 412.50
    assert out["supplier_payments"]["allocated"] == 1
    assert out["supplier_payments"]["unallocated"] == 0
    assert out["accounts_payable"]["unallocated_payments"] == 0
    assert out["accounts_payable"]["broken_references"] == []

    # P&L/VAT sections must not fabricate a computed total.
    assert "notice" in out["profit_and_loss"]
    assert "not" in out["profit_and_loss"]["notice"].casefold()
    assert "notice" in out["vat"]
    assert out["limitations"]  # explicit limitations are always surfaced


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_period_surfaces_broken_reference_with_key(client: ManagerClient) -> None:
    respx.get(f"{BASE}/sales-invoices").mock(
        return_value=httpx.Response(200, json=_envelope("salesInvoices", []))
    )
    respx.get(f"{BASE}/purchase-invoices").mock(
        return_value=httpx.Response(200, json=_envelope("purchaseInvoices", []))
    )
    respx.get(f"{BASE}/receipts").mock(
        return_value=httpx.Response(200, json=_envelope("receipts", []))
    )
    respx.get(f"{BASE}/payments").mock(
        return_value=httpx.Response(200, json=_envelope("payments", [{"key": "pay-a"}]))
    )
    _mock_form(
        "payment-form",
        "pay-a",
        {
            "Key": "pay-a",
            "Date": "2025-03-15",
            "Lines": [{"Amount": 412.50, "PurchaseInvoice": "missing-inv"}],
        },
    )
    respx.get(f"{BASE}/purchase-invoice-form/missing-inv").mock(
        return_value=httpx.Response(404, json={})
    )
    respx.get(f"{BASE}/journal-entries").mock(
        return_value=httpx.Response(200, json=_envelope("journalEntries", []))
    )
    respx.get(f"{BASE}/inter-account-transfers").mock(
        return_value=httpx.Response(200, json=_envelope("interAccountTransfers", []))
    )
    respx.get(f"{BASE}/chart-of-accounts").mock(
        return_value=httpx.Response(200, json=_envelope("chartOfAccounts", []))
    )
    respx.get(f"{BASE}/profit-and-loss-statement-transactions").mock(
        return_value=httpx.Response(200, json={"profitAndLossStatementTransactions": []})
    )
    respx.get(f"{BASE}/balance-sheet-transactions").mock(
        return_value=httpx.Response(200, json={"balanceSheetTransactions": []})
    )
    respx.get(f"{BASE}/tax-summary-transactions").mock(
        return_value=httpx.Response(200, json={"taxSummaryTransactions": []})
    )

    out = await reconcile_period(client, None, None)
    await client.aclose()

    broken = out["accounts_payable"]["broken_references"]
    assert len(broken) == 1
    assert broken[0]["key"] == "pay-a"
    assert broken[0]["missing_invoice_key"] == "missing-inv"

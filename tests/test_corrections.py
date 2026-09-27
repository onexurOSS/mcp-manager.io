# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Two-phase corrections: propose/apply, reallocation, safe void, and the
Missing-invoice regression test (respx; no live Manager).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from manager_mcp.client import ManagerClient
from manager_mcp.corrections import (
    apply_correction,
    apply_purchase_invoice_reconstruction,
    propose_correction,
    propose_purchase_invoice_reconstruction,
    reallocate_payment_line,
    snapshot_and_void,
)
from manager_mcp.scopes import WritePolicy, WritesDeniedError

BASE = "http://example.test/api2"


@pytest.fixture(autouse=True)
def _audit_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_MCP_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))


def _client(scopes: frozenset[str], deletes: frozenset[str] = frozenset()) -> ManagerClient:
    return ManagerClient(BASE, "k", policy=WritePolicy(scopes, deletes))


# --------------------------------------------------------------------------
# propose_correction / apply_correction
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_correction_requires_scope() -> None:
    client = _client(frozenset({"banking"}))
    with pytest.raises(WritesDeniedError):
        await propose_correction(client, client.policy, "purchase_invoices", {"Supplier": "s"})
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_propose_correction_does_not_write(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(frozenset({"purchases"}))
    # No POST route mocked at all -- if propose_correction ever called POST,
    # respx would raise "not mocked!" and fail this test.
    fields = {"Supplier": "s", "Date": "2025-03-15", "Lines": []}
    out = await propose_correction(client, client.policy, "purchase_invoices", fields)
    await client.aclose()
    assert out["status"] == "proposed"
    assert out["operation"] == "create"
    assert "proposal_token" in out


@pytest.mark.asyncio
@respx.mock
async def test_apply_correction_rejects_mismatched_token() -> None:
    client = _client(frozenset({"purchases"}))
    with pytest.raises(ValueError, match="proposal_token does not match"):
        await apply_correction(
            client, client.policy, "wrong-token", "purchase_invoices", {"Supplier": "s"}
        )
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_propose_then_apply_create_round_trip() -> None:
    client = _client(frozenset({"purchases"}))
    fields = {
        "Supplier": "sup-1",
        "Date": "2025-03-15",
        "Lines": [{"Account": "expenses", "Amount": 10.0}],
    }
    proposal = await propose_correction(client, client.policy, "purchase_invoices", fields)
    respx.post(f"{BASE}/purchase-invoice-form").mock(
        return_value=httpx.Response(201, json={"Key": "new-inv", **fields})
    )
    out = await apply_correction(
        client, client.policy, proposal["proposal_token"], "purchase_invoices", fields
    )
    await client.aclose()
    assert out["status"] == "ok"
    assert out["key"] == "new-inv"
    assert out["before"] is None


# --------------------------------------------------------------------------
# reallocate_payment_line
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_reallocate_refuses_nonexistent_target_invoice() -> None:
    client = _client(frozenset({"banking"}))
    respx.get(f"{BASE}/purchase-invoice-form/ghost-inv").mock(
        return_value=httpx.Response(404, json={})
    )
    with pytest.raises(ValueError, match="does not exist"):
        await reallocate_payment_line(client, client.policy, "pay-b", 0, "ghost-inv")
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_reallocate_payment_line_success() -> None:
    client = _client(frozenset({"banking"}))
    respx.get(f"{BASE}/purchase-invoice-form/new-inv").mock(
        return_value=httpx.Response(200, json={"Key": "new-inv"})
    )
    before = {
        "Key": "pay-b",
        "PaidFrom": "bank-1",
        "Date": "2025-03-15",
        "Lines": [{"Amount": 412.50, "AccountsPayablePurchaseInvoice": "old-inv"}],
    }
    respx.get(f"{BASE}/payment-form/pay-b").mock(
        side_effect=[
            httpx.Response(200, json=before),
            httpx.Response(
                200,
                json={
                    **before,
                    "Lines": [{"Amount": 412.50, "AccountsPayablePurchaseInvoice": "new-inv"}],
                },
            ),
        ]
    )
    respx.put(f"{BASE}/payment-form/pay-b").mock(return_value=httpx.Response(200, json={}))
    out = await reallocate_payment_line(client, client.policy, "pay-b", 0, "new-inv")
    await client.aclose()
    assert out["status"] == "ok"
    assert out["reallocated_from"] == "old-inv"
    assert out["reallocated_to"] == "new-inv"


# --------------------------------------------------------------------------
# Missing-invoice regression test (general pattern, not hard-coded)
# --------------------------------------------------------------------------

_MISSING_INVOICE_PAYMENTS = {
    # Field name matches real production data (verified live): the AP
    # allocation line reads back as a plain `PurchaseInvoice` key, not
    # `AccountsPayablePurchaseInvoice` -- see corrections._ALLOCATION.
    "pay-a": {
        "Key": "pay-a",
        "PaidFrom": "bank-1",
        "Supplier": "example-supplier-key",
        "Date": "2025-03-15",
        "Description": "Repayment of Example Customer loan/current account",
        "Lines": [
            {
                "Amount": 412.50,
                "PurchaseInvoice": "missing-inv-key",
            }
        ],
    },
    "pay-b": {
        "Key": "pay-b",
        "PaidFrom": "bank-1",
        "Supplier": "example-supplier-key",
        "Date": "2025-03-15",
        "Description": "Repayment of Example Customer loan/current account",
        "Lines": [
            {
                "Amount": 2.50,
                "PurchaseInvoice": "missing-inv-key",
            }
        ],
    },
}


def _mock_missing_invoice_payments() -> None:
    for key, body in _MISSING_INVOICE_PAYMENTS.items():
        respx.get(f"{BASE}/payment-form/{key}").mock(return_value=httpx.Response(200, json=body))
    respx.get(f"{BASE}/purchase-invoice-form/missing-inv-key").mock(
        return_value=httpx.Response(404, json={})
    )


@pytest.mark.asyncio
@respx.mock
async def test_missing_invoice_reconstruction_unresolved_without_line_evidence() -> None:
    """The free-text 'Example Customer loan' description must never be used,
    and the £380.00/£35.00 split cannot be derived from the two payment
    totals alone -- the tool must say so rather than invent it.
    """
    client = _client(frozenset({"purchases", "banking"}))
    _mock_missing_invoice_payments()
    out = await propose_purchase_invoice_reconstruction(
        client, client.policy, ["pay-a", "pay-b"]
    )
    await client.aclose()
    assert out["status"] == "unresolved"
    assert out["party"] == "example-supplier-key"
    assert out["missing_invoice_key"] == "missing-inv-key"
    assert out["total_from_transactions"] == 415.00
    assert "cannot be derived" in out["reason"]
    assert "loan" not in out["reason"].casefold()  # never leaks the free-text framing as fact


@pytest.mark.asyncio
@respx.mock
async def test_missing_invoice_reconstruction_rejects_mismatched_evidence() -> None:
    client = _client(frozenset({"purchases", "banking"}))
    _mock_missing_invoice_payments()
    out = await propose_purchase_invoice_reconstruction(
        client,
        client.policy,
        ["pay-a", "pay-b"],
        lines=[{"Account": "computer-equipment", "Amount": 300.00}],  # doesn't reconcile
    )
    await client.aclose()
    assert out["status"] == "unresolved"
    assert out["lines_total"] == 300.00
    assert out["total_from_transactions"] == 415.00


@pytest.mark.asyncio
@respx.mock
async def test_missing_invoice_reconstruction_full_workflow() -> None:
    client = _client(frozenset({"purchases", "banking"}), frozenset())

    # Stateful payment store used for the whole test (GET reflects current
    # state, PUT replaces it) -- registered once, up front, so propose and
    # apply share the same live state. A static exact-match mock plus a
    # later regex mock would both be registered against the same URL and
    # respx matches routes in registration order, so mixing them here would
    # make GETs silently keep returning the pre-reallocation body forever.
    store = {k: dict(v) for k, v in _MISSING_INVOICE_PAYMENTS.items()}

    def _key_from_path(request: httpx.Request) -> str:
        return request.url.path.rsplit("/", 1)[-1]

    def _get_payment(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=store[_key_from_path(request)])

    def _put_payment(request: httpx.Request) -> httpx.Response:
        key = _key_from_path(request)
        body = json.loads(request.content)
        body["Key"] = key
        store[key] = body
        return httpx.Response(200, json=body)

    respx.get(url__regex=rf"{BASE}/payment-form/(pay-a|pay-b)$").mock(side_effect=_get_payment)
    respx.put(url__regex=rf"{BASE}/payment-form/(pay-a|pay-b)$").mock(side_effect=_put_payment)
    respx.get(f"{BASE}/purchase-invoice-form/missing-inv-key").mock(
        return_value=httpx.Response(404, json={})
    )

    supplied_lines = [
        {"Account": "computer-equipment", "Amount": 380.00},
        {"Account": "computer-equipment", "Amount": 35.00},
    ]
    proposal = await propose_purchase_invoice_reconstruction(
        client,
        client.policy,
        ["pay-a", "pay-b"],
        lines=supplied_lines,
        date="2025-02-10",
        reference="INV-EXAMPLE-0001",
    )
    assert proposal["status"] == "proposed"
    assert proposal["proposed_fields"]["Supplier"] == "example-supplier-key"
    assert proposal["proposed_fields"]["Reference"] == "INV-EXAMPLE-0001"

    new_invoice = {
        "Key": "new-example-inv",
        "Supplier": "example-supplier-key",
        "Date": "2025-02-10",
        "Reference": "INV-EXAMPLE-0001",
        "Lines": supplied_lines,
    }
    respx.post(f"{BASE}/purchase-invoice-form").mock(
        return_value=httpx.Response(201, json=new_invoice)
    )
    respx.get(f"{BASE}/purchase-invoice-form/new-example-inv").mock(
        return_value=httpx.Response(200, json=new_invoice)
    )
    respx.get(f"{BASE}/purchase-invoices", params={"skip": 0, "pageSize": 200}).mock(
        return_value=httpx.Response(
            200,
            json={
                "totalRecords": 1,
                "purchaseInvoices": [
                    {"key": "new-example-inv", "invoiceAmount": {"value": 415.00}}
                ],
            },
        )
    )

    respx.get(f"{BASE}/payments", params={"skip": 0, "pageSize": 200}).mock(
        return_value=httpx.Response(
            200, json={"totalRecords": 2, "payments": [{"key": "pay-a"}, {"key": "pay-b"}]}
        )
    )

    result = await apply_purchase_invoice_reconstruction(
        client,
        client.policy,
        proposal["proposal_token"],
        proposal["proposed_fields"],
        ["pay-a", "pay-b"],
    )
    await client.aclose()

    assert result["status"] == "ok"
    assert result["invoice"]["key"] == "new-example-inv"
    assert all(r["status"] == "ok" for r in result["reallocations"])
    # Writes always use the canonical write-time field, even though the
    # original data read back with the `PurchaseInvoice` alias -- and the
    # stale alias must not survive the reallocation (see _reallocate).
    assert store["pay-a"]["Lines"][0]["AccountsPayablePurchaseInvoice"] == "new-example-inv"
    assert "PurchaseInvoice" not in store["pay-a"]["Lines"][0]
    assert store["pay-b"]["Lines"][0]["AccountsPayablePurchaseInvoice"] == "new-example-inv"
    assert "PurchaseInvoice" not in store["pay-b"]["Lines"][0]
    verification = result["verification"]
    assert verification["invoice_total"] == 415.00
    assert verification["allocated_total"] == 415.00
    assert verification["fully_paid"] is True
    assert verification["outstanding"] == 0.0


# --------------------------------------------------------------------------
# snapshot_and_void
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_and_void_requires_confirmation() -> None:
    client = _client(frozenset(), frozenset({"sales"}))
    respx.get(f"{BASE}/sales-invoice-form/dup-1").mock(
        return_value=httpx.Response(200, json={"Key": "dup-1"})
    )
    respx.get(f"{BASE}/receipts").mock(
        return_value=httpx.Response(200, json={"totalRecords": 0, "receipts": []})
    )
    out = await snapshot_and_void(client, client.policy, "sales_invoices", "dup-1")
    await client.aclose()
    assert out["status"] == "confirmation_required"


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_and_void_blocked_when_still_referenced() -> None:
    client = _client(frozenset(), frozenset({"sales"}))
    respx.get(f"{BASE}/sales-invoice-form/inv-1").mock(
        return_value=httpx.Response(200, json={"Key": "inv-1"})
    )
    respx.get(f"{BASE}/receipts").mock(
        return_value=httpx.Response(200, json={"totalRecords": 1, "receipts": [{"key": "r1"}]})
    )
    respx.get(f"{BASE}/receipt-form/r1").mock(
        return_value=httpx.Response(
            200,
            json={
                "Key": "r1",
                "Lines": [{"AccountsReceivableSalesInvoice": "inv-1", "Amount": 10}],
            },
        )
    )
    out = await snapshot_and_void(
        client, client.policy, "sales_invoices", "inv-1", confirmed_duplicate_of="dup-check"
    )
    await client.aclose()
    assert out["status"] == "blocked"
    assert out["blocking_keys"] == ["r1"]


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_and_void_succeeds_and_logs(tmp_path: Path) -> None:
    from manager_mcp.audit_log import read_events

    client = _client(frozenset(), frozenset({"sales"}))
    respx.get(f"{BASE}/sales-invoice-form/dup-2").mock(
        return_value=httpx.Response(200, json={"Key": "dup-2", "Reference": "INV-1"})
    )
    respx.get(f"{BASE}/receipts").mock(
        return_value=httpx.Response(200, json={"totalRecords": 0, "receipts": []})
    )
    respx.delete(f"{BASE}/sales-invoice-form/dup-2").mock(return_value=httpx.Response(204))
    out = await snapshot_and_void(
        client, client.policy, "sales_invoices", "dup-2", confirmed_duplicate_of="dup-1"
    )
    await client.aclose()
    assert out["status"] == "ok"
    events = read_events(Path(__import__("os").environ["MANAGER_MCP_AUDIT_LOG_PATH"]))
    assert any(e["operation"] == "void" and e["key"] == "dup-2" for e in events)


@pytest.mark.asyncio
async def test_snapshot_and_void_requires_delete_scope() -> None:
    client = _client(frozenset(), frozenset())
    with pytest.raises(WritesDeniedError):
        await snapshot_and_void(client, client.policy, "sales_invoices", "dup-2")
    await client.aclose()

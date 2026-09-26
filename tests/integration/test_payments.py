"""Live payment task tools."""

from __future__ import annotations

import pytest

from manager_mcp.task_tools import (
    issue_purchase_invoice,
    issue_sales_invoice,
    record_customer_payment,
    record_supplier_payment,
    void_document,
)

pytestmark = pytest.mark.integration

_TODAY = "2026-07-29"


@pytest.mark.asyncio
async def test_record_customer_payment_happy_path(
    live_client,
    temp_customer,
    bank_account_key,
    income_account_key,
) -> None:
    invoice_out = await issue_sales_invoice(
        live_client,
        live_client.policy,
        {
            "Customer": temp_customer,
            "IssueDate": _TODAY,
            "Lines": [
                {
                    "Account": income_account_key,
                    "LineDescription": "Payment test",
                    "Qty": 1,
                    "SalesUnitPrice": 100.0,
                }
            ],
        },
    )
    invoice_key = invoice_out["keys"]["sales_invoice"]
    receipt_key = ""
    try:
        out = await record_customer_payment(
            live_client,
            live_client.policy,
            customer=temp_customer,
            bank_account=bank_account_key,
            date=_TODAY,
            amount=100.0,
            invoice_key=invoice_key,
        )
        assert out["status"] == "ok"
        receipt_key = out["keys"]["receipt"]
        assert receipt_key
        assert out["keys"]["invoice"] == invoice_key
        assert not any("unallocated" in w.casefold() for w in out["warnings"])
    finally:
        if receipt_key:
            await void_document(live_client, live_client.policy, "receipts", receipt_key)
        await void_document(live_client, live_client.policy, "sales_invoices", invoice_key)


@pytest.mark.asyncio
async def test_record_supplier_payment_happy_path(
    live_client,
    temp_supplier,
    bank_account_key,
    expense_account_key,
) -> None:
    invoice_out = await issue_purchase_invoice(
        live_client,
        live_client.policy,
        {
            "Supplier": temp_supplier,
            "IssueDate": _TODAY,
            "Lines": [
                {
                    "Account": expense_account_key,
                    "LineDescription": "Supplier payment test",
                    "Qty": 1,
                    "UnitPrice": 25.0,
                }
            ],
        },
    )
    invoice_key = invoice_out["keys"]["purchase_invoice"]
    payment_key = ""
    try:
        out = await record_supplier_payment(
            live_client,
            live_client.policy,
            supplier=temp_supplier,
            bank_account=bank_account_key,
            date=_TODAY,
            amount=25.0,
            invoice_key=invoice_key,
        )
        assert out["status"] == "ok"
        payment_key = out["keys"]["payment"]
        assert payment_key
        assert out["keys"]["invoice"] == invoice_key
    finally:
        if payment_key:
            await void_document(live_client, live_client.policy, "payments", payment_key)
        await void_document(live_client, live_client.policy, "purchase_invoices", invoice_key)

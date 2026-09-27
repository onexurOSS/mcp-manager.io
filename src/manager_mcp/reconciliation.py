"""Read-only PERIOD reconciliation report, composed from diagnostics.

Every figure here is derived from Manager's own transaction data only --
no external system is consulted anywhere in this
module. 'source_count' is Manager's own list-envelope `totalRecords`;
'current_count' is what this tool actually walked and reconciled. A
mismatch between the two signals a paging bug here, not a real-world
discrepancy, since both numbers come from Manager itself.

Where Manager API2 exposes only a raw transactions feed and not a
computed report total (profit_and_loss, balance_sheet, tax_summary --
confirmed limitation), this module surfaces the raw feed plus an explicit
notice instead of fabricating a computed figure from an unverified field
shape.

Each transaction-bearing resource's forms are fetched exactly once here
and shared across every diagnostic that needs them (duplicate detection,
unallocated/broken-reference checks, the GL summary) -- avoiding a
redundant per-record form fetch for every diagnostic that touches the
same resource.
"""

from __future__ import annotations

import sys as _sys
from typing import Any

from manager_mcp.client import ManagerClient
from manager_mcp.diagnostics import (
    _DATE_FIELD,
    _in_period,
    _transaction_total,
    find_broken_invoice_references,
    find_duplicate_transactions,
    find_suspense_candidate_accounts,
    find_unallocated_transactions,
    general_ledger_summary,
)
from manager_mcp.paging import fetch_all_forms
from manager_mcp.resources import resolve


def _period_params(from_date: str | None, to_date: str | None) -> dict[str, Any] | None:
    params: dict[str, Any] = {}
    if from_date:
        params["fromDate"] = from_date
    if to_date:
        params["toDate"] = to_date
    return params or None


async def _source_count(client: ManagerClient, resource: str) -> int | None:
    desc = resolve(resource)
    if desc is None:
        return None
    body = await client.get(desc.path, params={"skip": 0, "pageSize": 1})
    return body.get("totalRecords") if isinstance(body, dict) else None


async def _invoice_section(
    client: ManagerClient,
    resource: str,
    from_date: str | None,
    to_date: str | None,
    *,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    source_count = await _source_count(client, resource)
    date_field = _DATE_FIELD.get(resource, "Date")
    filtered = [i for i in items if _in_period(i.get(date_field), from_date, to_date)]
    dup = await find_duplicate_transactions(
        client, resource, from_date=from_date, to_date=to_date, items=filtered
    )
    total = sum(_transaction_total(resource, i) or 0.0 for i in filtered)
    return {
        "source_count": source_count,
        "current_count": len(filtered),
        "total": round(total, 2),
        "duplicate": dup["duplicate_count"],
        "unresolved": dup["unresolved_count"],
        "duplicate_groups": dup["duplicate_groups"],
    }


async def _banking_section(
    client: ManagerClient,
    resource: str,
    from_date: str | None,
    to_date: str | None,
    *,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    date_field = _DATE_FIELD.get(resource, "Date")
    filtered = [i for i in items if _in_period(i.get(date_field), from_date, to_date)]
    total = sum(_transaction_total(resource, i) or 0.0 for i in filtered)
    unallocated = await find_unallocated_transactions(
        client, resource, from_date=from_date, to_date=to_date, items=filtered
    )
    dup = await find_duplicate_transactions(
        client, resource, from_date=from_date, to_date=to_date, items=filtered
    )
    return {
        "count": len(filtered),
        "total": round(total, 2),
        "allocated": len(filtered) - len(unallocated),
        "unallocated": len(unallocated),
        "unallocated_keys": [i.get("Key") or i.get("key") for i in unallocated],
        "duplicate": dup["duplicate_count"],
    }


async def _raw_feed_section(
    client: ManagerClient,
    report: str,
    items_key: str,
    from_date: str | None,
    to_date: str | None,
    *,
    notice: str,
) -> dict[str, Any]:
    desc = resolve(report)
    if desc is None:
        return {"row_count": None, "raw": None, "notice": f"'{report}' is not a known report."}
    body = await client.get(desc.path, params=_period_params(from_date, to_date))
    rows = body.get(items_key) if isinstance(body, dict) else None
    return {
        "row_count": len(rows) if isinstance(rows, list) else None,
        "raw": body,
        "notice": notice,
    }


_TRANSACTIONS_FEED_NOTICE = (
    "Row-level transactions feed only. Manager API2 in this version does not "
    "expose computed report totals (the *-form resources return report "
    "configuration Keys, not rows/totals) -- do not treat this as an official "
    "computed report. Sum 'raw' rows once the field shape is verified live "
    "against this Manager instance, or read the totals in the Manager app."
)


async def reconcile_period(
    client: ManagerClient,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """Compose the PERIOD reconciliation report from Manager's own data.

    Every exception (duplicate group, unallocated transaction, broken
    invoice reference) carries the exact Manager Key(s) involved.
    """
    sales_invoices_forms = await fetch_all_forms(client, "sales_invoices")
    purchase_invoices_forms = await fetch_all_forms(client, "purchase_invoices")
    receipts_forms = await fetch_all_forms(client, "receipts")
    payments_forms = await fetch_all_forms(client, "payments")
    journal_forms = await fetch_all_forms(client, "journal_entries")
    transfer_forms = await fetch_all_forms(client, "inter_account_transfers")

    sales_invoices = await _invoice_section(
        client, "sales_invoices", from_date, to_date, items=sales_invoices_forms
    )
    purchase_invoices = await _invoice_section(
        client, "purchase_invoices", from_date, to_date, items=purchase_invoices_forms
    )
    customer_receipts = await _banking_section(
        client, "receipts", from_date, to_date, items=receipts_forms
    )
    supplier_payments = await _banking_section(
        client, "payments", from_date, to_date, items=payments_forms
    )

    ap_broken = await find_broken_invoice_references(
        client, "payments", from_date=from_date, to_date=to_date, items=payments_forms
    )
    ar_broken = await find_broken_invoice_references(
        client, "receipts", from_date=from_date, to_date=to_date, items=receipts_forms
    )

    bank = {
        "money_in": customer_receipts["total"],
        "money_out": supplier_payments["total"],
        "reconciled_difference": None,
        "notice": (
            "Manager exposes no read-verified reconciled/cleared status feed at "
            "the transaction level and bank-reconciliation-form is write-"
            "denylisted; money_in/money_out are receipt/payment totals for the "
            "period, not a bank-statement match. The bank_accounts list DOES "
            "expose per-account clearedBalance/actualBalance snapshots (see "
            "bank_balances) -- use bank_activity(<bank_account_key>, ...) for "
            "per-account transaction detail."
        ),
    }

    accounts_receivable = {
        "invoice_count": sales_invoices["current_count"],
        "invoice_total": sales_invoices["total"],
        "receipt_allocations": customer_receipts["allocated"],
        "unallocated_receipts": customer_receipts["unallocated"],
        "broken_references": ar_broken,
    }
    accounts_payable = {
        "invoice_count": purchase_invoices["current_count"],
        "invoice_total": purchase_invoices["total"],
        "payment_allocations": supplier_payments["allocated"],
        "unallocated_payments": supplier_payments["unallocated"],
        "broken_references": ap_broken,
    }

    suspense_candidates = await find_suspense_candidate_accounts(client)
    sources = {
        "receipts": receipts_forms,
        "payments": payments_forms,
        "journal_entries": journal_forms,
        "inter_account_transfers": transfer_forms,
        "sales_invoices": sales_invoices_forms,
        "purchase_invoices": purchase_invoices_forms,
    }
    gl = await general_ledger_summary(client, from_date=from_date, to_date=to_date, sources=sources)
    suspense = {
        "candidate_accounts": suspense_candidates,
        "candidate_balances": {
            str(acct.get("Key") or acct.get("key")): gl["per_account_net"].get(
                str(acct.get("Key") or acct.get("key"))
            )
            for acct in suspense_candidates
        },
        "notice": (
            "Candidates identified by structured chart-of-accounts Name only "
            "(never by transaction description). Confirm with the user before "
            "treating any of these as the suspense account."
        ),
    }

    pl = await _raw_feed_section(
        client,
        "profit_and_loss",
        "profitAndLossStatementTransactions",
        from_date,
        to_date,
        notice=_TRANSACTIONS_FEED_NOTICE,
    )
    balance_sheet = await _raw_feed_section(
        client,
        "balance_sheet",
        "balanceSheetTransactions",
        from_date,
        to_date,
        notice=_TRANSACTIONS_FEED_NOTICE,
    )
    vat = await _raw_feed_section(
        client,
        "tax_summary",
        "taxSummaryTransactions",
        None,
        None,
        notice=_TRANSACTIONS_FEED_NOTICE
        + " Output/input/net VAT must be derived from 'raw' once verified, not fabricated here.",
    )

    return {
        "period": {"from": from_date, "to": to_date},
        "sales_invoices": sales_invoices,
        "purchase_invoices": purchase_invoices,
        "customer_receipts": customer_receipts,
        "supplier_payments": supplier_payments,
        "bank": bank,
        "accounts_receivable": accounts_receivable,
        "accounts_payable": accounts_payable,
        "suspense": suspense,
        "profit_and_loss": pl,
        "vat": vat,
        "balance_sheet": balance_sheet,
        "general_ledger": gl,
        "limitations": [
            "P&L/Balance Sheet/Tax Summary sections are raw *-transactions feeds, "
            "not computed report totals -- Manager API2 does not expose the "
            "latter in this version. See each section's 'notice'.",
            "Bank money_in/money_out are period totals of receipts/payments, not "
            "a matched bank-statement reconciliation; bank-reconciliation-form "
            "is write-denylisted and its read shape is unverified beyond the "
            "per-account clearedBalance/actualBalance snapshot.",
            "Suspense-account identification is name-based (chart_of_accounts "
            "Name field) and requires human confirmation before acting on it.",
            "sales_invoices/purchase_invoices totals use Manager's own computed "
            "invoiceAmount (list view), not a from-scratch sum of Lines -- "
            "invoice Lines use Qty x UnitPrice + TaxCode with no verified "
            "tax-computation path here. general_ledger excludes invoice Lines "
            "entirely for the same reason (see its own 'notice').",
        ],
    }


def register_reconciliation_tools(mcp: Any, get_client: Any) -> None:
    """Register the read-only period reconciliation tool on the FastMCP instance."""
    _recon = _sys.modules[__name__]

    @mcp.tool(
        description=(
            "Read-only PERIOD reconciliation report composed entirely from "
            "Manager's own transaction data (no external system is "
            "consulted). Every exception carries exact Manager "
            "Keys. P&L/Balance Sheet/VAT sections surface raw transaction "
            "feeds with an explicit notice where Manager API2 does not expose "
            "computed report totals -- never a fabricated total."
        )
    )
    async def reconcile_period(
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        return await _recon.reconcile_period(get_client(), from_date, to_date)

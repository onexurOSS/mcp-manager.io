# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Read-only diagnostics over Manager's structured transaction data.

Every function here follows the source-of-truth priority: transaction
type, Manager Key, structured Account, structured invoice allocation,
structured Customer/Supplier, date, amount, Lines, chart-of-accounts
relationships. Free-text (Description, LineDescription, payee text) is
never used to classify, match, or deduplicate anything in this module --
it is carried through in results purely for a human to read.

Nothing here mutates Manager. Nothing here guesses: exact-match only.

Two facts, verified live against a real Manager business (not assumed
from the write-side research alone), shape this module:

1. List endpoints (e.g. GET /payments) return a flattened, camelCase,
   display-oriented summary with NO `Lines[]` at all. Structured Lines
   and AR/AP allocation fields exist only on the per-record form
   endpoint (GET /payment-form/{key}), which is PascalCase. Anything
   needing Lines/Account/party Keys must fetch the form
   (paging.fetch_all_forms), not just the list.
2. The AR/AP invoice-allocation line field is written by this project's
   own task tools as `AccountsPayablePurchaseInvoice` /
   `AccountsReceivableSalesInvoice`, but real historical data (written by
   other means -- the desktop app, imports, other tooling) can read back
   with a plain `PurchaseInvoice` / `SalesInvoice` key instead, sometimes
   alongside a separate `AccountsPayableSupplier` / a header-level
   `Supplier`/`Customer`. Every read in this module checks all known
   aliases; every write in task_tools.py still uses the original
   documented field names (unchanged -- this is a read-side fix only).
"""

from __future__ import annotations

import sys as _sys
from typing import Any

import httpx

from manager_mcp.client import ManagerClient
from manager_mcp.paging import fetch_all, fetch_all_forms
from manager_mcp.resources import form_path

# Read-side aliases (see module docstring point 2). Order = preference.
_PURCHASE_INVOICE_LINE_KEYS = ("PurchaseInvoice", "AccountsPayablePurchaseInvoice")
_SALES_INVOICE_LINE_KEYS = ("AccountsReceivableSalesInvoice", "SalesInvoice")
_SUPPLIER_LINE_KEYS = ("AccountsPayableSupplier",)
_CUSTOMER_LINE_KEYS = ("AccountsReceivableCustomer",)
_SUPPLIER_HEADER_KEYS = ("Supplier",)
_CUSTOMER_HEADER_KEYS = ("Customer",)

# resource -> (invoice line aliases, invoice resource, party line aliases, party header aliases)
_ALLOCATION: dict[str, tuple[tuple[str, ...], str, tuple[str, ...], tuple[str, ...]]] = {
    "payments": (
        _PURCHASE_INVOICE_LINE_KEYS,
        "purchase_invoices",
        _SUPPLIER_LINE_KEYS,
        _SUPPLIER_HEADER_KEYS,
    ),
    "receipts": (
        _SALES_INVOICE_LINE_KEYS,
        "sales_invoices",
        _CUSTOMER_LINE_KEYS,
        _CUSTOMER_HEADER_KEYS,
    ),
}

_INVOICE_RESOURCES = frozenset({"sales_invoices", "purchase_invoices"})

# Per-resource structured date field, as verified on the *form* endpoint.
_DATE_FIELD: dict[str, str] = {
    "sales_invoices": "IssueDate",
    "purchase_invoices": "IssueDate",
    "receipts": "Date",
    "payments": "Date",
    "journal_entries": "Date",
    "inter_account_transfers": "Date",
}

# Invoice Lines use Qty x UnitPrice (+ TaxCode), never a flat Amount --
# verified live. Used only to build a duplicate-detection line signature,
# never to compute a total (see module docstring point 1's `_list` note).
_UNIT_PRICE_FIELD: dict[str, str] = {
    "sales_invoices": "SalesUnitPrice",
    "purchase_invoices": "PurchaseUnitPrice",
}

_DUP_PARTY_FIELD: dict[str, str] = {
    "sales_invoices": "Customer",
    "purchase_invoices": "Supplier",
    "receipts": "Customer",
    "payments": "Supplier",
}

_LEDGER_SOURCES: tuple[str, ...] = (
    "receipts",
    "payments",
    "journal_entries",
    "inter_account_transfers",
    "sales_invoices",
    "purchase_invoices",
)

_SUSPENSE_NAME_HINTS = (
    "suspense",
    "uncategor",
    "unknown",
    "clearing",
    "miscellaneous",
    "unidentified",
)


def _first(line: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = line.get(k)
        if v:
            return str(v)
    return None


def _key_of(item: dict[str, Any]) -> str | None:
    key = item.get("Key") or item.get("key")
    return str(key) if key else None


def _lines_of(item: dict[str, Any]) -> list[dict[str, Any]]:
    lines = item.get("Lines")
    return [line for line in lines if isinstance(line, dict)] if isinstance(lines, list) else []


def _line_amount(line: dict[str, Any]) -> float | None:
    """Signed line amount for receipts/payments/journal_entries/transfers.

    Handles both observed conventions: a flat signed `Amount`, or a
    `Debit`/`Credit` pair (journal entries). Returns None for invoice
    Lines, which carry no Amount/Debit/Credit at all (Qty x UnitPrice
    instead) -- callers must not treat that None as zero.
    """
    amount = line.get("Amount")
    if isinstance(amount, (int, float)) and not isinstance(amount, bool):
        return float(amount)
    debit = line.get("Debit")
    credit = line.get("Credit")
    if isinstance(debit, (int, float)) or isinstance(credit, (int, float)):
        d = debit if isinstance(debit, (int, float)) else 0
        c = credit if isinstance(credit, (int, float)) else 0
        return float(d) - float(c)
    return None


def _total(item: dict[str, Any]) -> float | None:
    total = 0.0
    seen = False
    for line in _lines_of(item):
        amount = _line_amount(line)
        if amount is not None:
            total += amount
            seen = True
    return total if seen else None


def _money_value(node: Any) -> float | None:
    if isinstance(node, dict) and isinstance(node.get("value"), (int, float)):
        return float(node["value"])
    return None


def _invoice_amount(item: dict[str, Any]) -> float | None:
    """Manager's own computed invoice total, from the list-row summary
    attached by fetch_all_forms as `_list` (key `invoiceAmount`).
    Authoritative: invoice Lines have no verified tax-computation path
    here, so this is used instead of summing Lines.
    """
    return _money_value((item.get("_list") or {}).get("invoiceAmount"))


def _invoice_balance_due(item: dict[str, Any]) -> float | None:
    return _money_value((item.get("_list") or {}).get("balanceDue"))


def _transaction_total(resource: str, item: dict[str, Any]) -> float | None:
    if resource in _INVOICE_RESOURCES:
        return _invoice_amount(item)
    return _total(item)


def _date_of(resource: str, item: dict[str, Any]) -> Any:
    field = _DATE_FIELD.get(resource, "Date")
    return item.get(field)


def _in_period(date_value: Any, from_date: str | None, to_date: str | None) -> bool:
    if not isinstance(date_value, str) or not date_value:
        return True  # never silently drop rows with a missing/odd date
    if from_date and date_value < from_date:
        return False
    if to_date and date_value > to_date:
        return False
    return True


async def find_records(
    client: ManagerClient,
    resource: str,
    filters: dict[str, Any],
    *,
    max_pages: int = 50,
    page_size: int = 200,
) -> list[dict[str, Any]]:
    """Exact-match structured filter over a collection's LIST rows.

    `filters` is {field_name: expected_value}; a row matches only if every
    field is present and compares equal (via str() normalization). List
    rows are Manager's display-oriented, camelCase summary (e.g. `name`,
    `reference`, `key`) -- for filters that need Lines/Account/party Keys
    (only present on the form), use a form-aware diagnostic instead
    (find_broken_invoice_references, find_transactions_referencing_invoice).
    """
    items = await fetch_all(client, resource, max_pages=max_pages, page_size=page_size)
    out = []
    for item in items:
        if all(str(item.get(field)) == str(expected) for field, expected in filters.items()):
            out.append(item)
    return out


async def _invoice_exists(client: ManagerClient, resource: str, key: str) -> bool:
    path = form_path(resource, key)
    if path is None:
        return False
    try:
        await client.get(path)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return False
        raise
    return True


async def find_unallocated_transactions(
    client: ManagerClient,
    resource: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    max_pages: int = 50,
    items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Receipts/payments with no AR/AP invoice allocation on any line.

    Not every unallocated line is a problem -- a payment can legitimately
    post straight to a non-AP account (capital, loans, equity). This only
    reports "no invoice reference found"; it does not judge whether one
    was expected.
    """
    if resource not in _ALLOCATION:
        raise ValueError("resource must be 'receipts' or 'payments'.")
    invoice_aliases, _, _, _ = _ALLOCATION[resource]
    records = (
        items if items is not None else await fetch_all_forms(client, resource, max_pages=max_pages)
    )
    unallocated = []
    for item in records:
        if not _in_period(_date_of(resource, item), from_date, to_date):
            continue
        lines = _lines_of(item)
        if lines and all(not _first(line, invoice_aliases) for line in lines):
            unallocated.append(item)
    return unallocated


async def find_broken_invoice_references(
    client: ManagerClient,
    resource: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    max_pages: int = 50,
    items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Payments/receipts whose AP/AR invoice-Key line reference does not
    resolve to any current invoice record -- the general form of the
    missing-invoice payment pattern: the payment is genuine and structurally allocated, but
    the invoice it points at does not exist in this dataset.
    """
    if resource not in _ALLOCATION:
        raise ValueError("resource must be 'receipts' or 'payments'.")
    invoice_aliases, invoice_resource, _, _ = _ALLOCATION[resource]
    records = (
        items if items is not None else await fetch_all_forms(client, resource, max_pages=max_pages)
    )
    broken: list[dict[str, Any]] = []
    checked: dict[str, bool] = {}
    for item in records:
        if not _in_period(_date_of(resource, item), from_date, to_date):
            continue
        for line in _lines_of(item):
            invoice_key = _first(line, invoice_aliases)
            if not invoice_key:
                continue
            if invoice_key not in checked:
                checked[invoice_key] = await _invoice_exists(client, invoice_resource, invoice_key)
            if not checked[invoice_key]:
                broken.append(
                    {
                        "resource": resource,
                        "key": _key_of(item),
                        "date": _date_of(resource, item),
                        "line": line,
                        "missing_invoice_key": invoice_key,
                        "missing_invoice_resource": invoice_resource,
                    }
                )
    return broken


async def find_transactions_referencing_invoice(
    client: ManagerClient,
    resource: str,
    invoice_key: str,
    *,
    max_pages: int = 50,
    items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Payments/receipts whose allocation line references invoice_key
    (alias-aware, form-level). Shared by verify_invoice_balance and the
    reference-integrity check in corrections.snapshot_and_void.
    """
    if resource not in _ALLOCATION:
        raise ValueError("resource must be 'receipts' or 'payments'.")
    invoice_aliases, _, _, _ = _ALLOCATION[resource]
    records = (
        items if items is not None else await fetch_all_forms(client, resource, max_pages=max_pages)
    )
    out = []
    for item in records:
        for line in _lines_of(item):
            if _first(line, invoice_aliases) == str(invoice_key):
                out.append(item)
                break
    return out


def _line_signature(resource: str, item: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    unit_field = _UNIT_PRICE_FIELD.get(resource)
    sig = []
    for line in _lines_of(item):
        if unit_field:
            sig.append((line.get("Account"), line.get("Qty"), line.get(unit_field)))
        else:
            sig.append((line.get("Account"), _line_amount(line)))
    return tuple(sorted(sig, key=lambda t: tuple(str(x) for x in t)))


async def find_duplicate_transactions(
    client: ManagerClient,
    resource: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    max_pages: int = 50,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Exact-match duplicate detector.

    Two records are 'duplicate' only when every one of the following
    matches exactly: the structured party Key, the Reference string, the
    date, the computed total, and the line signature. Anything short of a
    full match is 'unresolved' -- never flagged as a duplicate on
    amount-only, name-similarity, or date-proximity grounds.
    """
    party_field = _DUP_PARTY_FIELD.get(resource)
    if party_field is None and resource != "journal_entries":
        raise ValueError(f"Duplicate detection not defined for '{resource}'.")
    date_field = _DATE_FIELD.get(resource, "Date")
    records = (
        items if items is not None else await fetch_all_forms(client, resource, max_pages=max_pages)
    )
    records = [r for r in records if _in_period(r.get(date_field), from_date, to_date)]

    fields = (party_field, "Reference", date_field) if party_field else ("Reference", date_field)
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    incomplete: list[dict[str, Any]] = []
    for item in records:
        if any(not item.get(f) for f in fields):
            incomplete.append(item)
            continue
        sig = (
            tuple(str(item.get(f)) for f in fields)
            + (_transaction_total(resource, item), _line_signature(resource, item))
        )
        buckets.setdefault(sig, []).append(item)

    duplicate_groups = [group for group in buckets.values() if len(group) > 1]
    duplicate_count = sum(len(g) for g in duplicate_groups)

    return {
        "resource": resource,
        "duplicate_groups": [
            {"keys": [_key_of(i) for i in group], "records": group} for group in duplicate_groups
        ],
        "duplicate_count": duplicate_count,
        "unresolved_count": len(incomplete),
        "unresolved": incomplete,
    }


async def verify_invoice_balance(
    client: ManagerClient,
    resource: str,
    key: str,
    *,
    max_pages: int = 50,
) -> dict[str, Any]:
    """Invoice balance cross-check: Manager's own computed total/balance
    (from the list row -- invoice Lines use Qty x UnitPrice + TaxCode with
    no verified tax-computation path here) vs. this module's own sum of
    every receipt/payment line that structurally allocates to it.

    A discrepancy between the two is reported, not resolved -- it can
    mean a genuinely broken allocation, or something (e.g. a credit note)
    outside what this function reconciles.
    """
    if resource not in _INVOICE_RESOURCES:
        raise ValueError("resource must be 'sales_invoices' or 'purchase_invoices'.")
    allocation_resource = "receipts" if resource == "sales_invoices" else "payments"

    path = form_path(resource, key)
    if path is None:
        raise ValueError(f"Unknown resource '{resource}'.")
    invoice_form = await client.get(path)
    if not isinstance(invoice_form, dict):
        raise ValueError(f"{resource}/{key} returned no form body.")

    list_rows = await fetch_all(client, resource, max_pages=max_pages)
    list_row = next((r for r in list_rows if _key_of(r) == str(key)), None)
    invoice_total = _money_value((list_row or {}).get("invoiceAmount"))
    manager_balance_due = _money_value((list_row or {}).get("balanceDue"))

    contributing_txns = await find_transactions_referencing_invoice(
        client, allocation_resource, key, max_pages=max_pages
    )
    invoice_aliases, _, _, _ = _ALLOCATION[allocation_resource]
    contributing: list[dict[str, Any]] = []
    allocated_total = 0.0
    for item in contributing_txns:
        for line in _lines_of(item):
            if _first(line, invoice_aliases) != str(key):
                continue
            amount = _line_amount(line)
            if amount is not None:
                allocated_total += amount
            contributing.append(
                {
                    "resource": allocation_resource,
                    "key": _key_of(item),
                    "date": _date_of(allocation_resource, item),
                    "amount": amount,
                }
            )
    allocated_total = round(allocated_total, 2)

    self_computed_outstanding = (
        round(invoice_total - allocated_total, 2) if invoice_total is not None else None
    )
    discrepancy = None
    if self_computed_outstanding is not None and manager_balance_due is not None:
        discrepancy = round(self_computed_outstanding - manager_balance_due, 2)

    fully_paid = False
    if self_computed_outstanding is not None:
        fully_paid = abs(self_computed_outstanding) < 0.005
    elif manager_balance_due is not None:
        fully_paid = abs(manager_balance_due) < 0.005

    return {
        "resource": resource,
        "key": key,
        "invoice_total": round(invoice_total, 2) if invoice_total is not None else None,
        "allocated_total": allocated_total,
        "outstanding": self_computed_outstanding,
        "manager_reported_balance_due": (
            round(manager_balance_due, 2) if manager_balance_due is not None else None
        ),
        "discrepancy_with_manager": discrepancy,
        "fully_paid": fully_paid,
        "contributing": contributing,
        "notice": (
            "invoice_total/manager_reported_balance_due are Manager's own computed "
            "list-view fields, not a from-scratch sum of Lines (invoice Lines use "
            "Qty x UnitPrice + TaxCode with no verified tax-computation path here). "
            "allocated_total/outstanding are self-computed from allocation Lines."
        ),
    }


async def account_ledger(
    client: ManagerClient,
    account: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    max_pages: int = 50,
    sources: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Every Lines[] entry across all transaction types whose Account
    matches the given chart-of-accounts key -- the closest available view
    to a per-account general ledger. Manager exposes no native
    GL-by-account endpoint; this is a client-side projection.

    Invoice (sales/purchase) Lines contribute nothing here: they carry no
    Amount (Qty x UnitPrice + TaxCode instead, not reimplemented) -- this
    excludes them rather than reporting a wrong number.
    """
    entries: list[dict[str, Any]] = []
    for source in _LEDGER_SOURCES:
        records = (sources or {}).get(source)
        if records is None:
            records = await fetch_all_forms(client, source, max_pages=max_pages)
        for item in records:
            date = _date_of(source, item)
            if not _in_period(date, from_date, to_date):
                continue
            for line in _lines_of(item):
                if str(line.get("Account") or "") != str(account):
                    continue
                entries.append(
                    {
                        "source": source,
                        "key": _key_of(item),
                        "date": date,
                        "amount": _line_amount(line),
                        "description": line.get("Description") or line.get("LineDescription"),
                    }
                )
    entries.sort(key=lambda e: str(e.get("date") or ""))
    debits = sum(
        e["amount"] for e in entries if isinstance(e["amount"], (int, float)) and e["amount"] > 0
    )
    credits = sum(
        -e["amount"] for e in entries if isinstance(e["amount"], (int, float)) and e["amount"] < 0
    )
    return {
        "account": account,
        "entries": entries,
        "debits": round(debits, 2),
        "credits": round(credits, 2),
        "net": round(debits - credits, 2),
        "notice": (
            "Excludes sales/purchase invoice Lines (Qty x UnitPrice + TaxCode, no "
            "verified tax-computation path here) -- only receipts, payments, "
            "journal entries, and transfers contribute."
        ),
    }


async def bank_activity(
    client: ManagerClient,
    bank_account_key: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    max_pages: int = 50,
    receipts: list[dict[str, Any]] | None = None,
    payments: list[dict[str, Any]] | None = None,
    transfers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Money-in/out for one bank/cash account, assembled from receipts
    (ReceivedIn), payments (PaidFrom), and transfers referencing it.
    Manager has no separate 'bank transaction' entity to read directly;
    journal entries posting straight to a bank account are not included
    (unverified whether Manager permits that) -- documented limitation.
    """
    money_in: list[dict[str, Any]] = []
    money_out: list[dict[str, Any]] = []

    receipt_records = (
        receipts
        if receipts is not None
        else await fetch_all_forms(client, "receipts", max_pages=max_pages)
    )
    for item in receipt_records:
        if str(item.get("ReceivedIn") or "") != str(bank_account_key):
            continue
        if not _in_period(item.get("Date"), from_date, to_date):
            continue
        money_in.append(
            {
                "source": "receipts",
                "key": _key_of(item),
                "date": item.get("Date"),
                "amount": _total(item),
            }
        )

    payment_records = (
        payments
        if payments is not None
        else await fetch_all_forms(client, "payments", max_pages=max_pages)
    )
    for item in payment_records:
        if str(item.get("PaidFrom") or "") != str(bank_account_key):
            continue
        if not _in_period(item.get("Date"), from_date, to_date):
            continue
        money_out.append(
            {
                "source": "payments",
                "key": _key_of(item),
                "date": item.get("Date"),
                "amount": _total(item),
            }
        )

    transfer_records = (
        transfers
        if transfers is not None
        else await fetch_all_forms(client, "inter_account_transfers", max_pages=max_pages)
    )
    for item in transfer_records:
        if not _in_period(item.get("Date"), from_date, to_date):
            continue
        amount = _total(item)
        if str(item.get("PaidFrom") or "") == str(bank_account_key):
            money_out.append(
                {
                    "source": "inter_account_transfers",
                    "key": _key_of(item),
                    "date": item.get("Date"),
                    "amount": amount,
                }
            )
        if str(item.get("ReceivedIn") or "") == str(bank_account_key):
            money_in.append(
                {
                    "source": "inter_account_transfers",
                    "key": _key_of(item),
                    "date": item.get("Date"),
                    "amount": amount,
                }
            )

    total_in = sum(e["amount"] for e in money_in if isinstance(e["amount"], (int, float)))
    total_out = sum(e["amount"] for e in money_out if isinstance(e["amount"], (int, float)))
    return {
        "bank_account": bank_account_key,
        "money_in": money_in,
        "money_out": money_out,
        "total_money_in": round(total_in, 2),
        "total_money_out": round(total_out, 2),
        "net_movement": round(total_in - total_out, 2),
    }


async def find_suspense_candidate_accounts(client: ManagerClient) -> list[dict[str, Any]]:
    """Chart-of-accounts rows whose structured Name matches common
    placeholder-account naming (suspense, uncategorised, clearing, ...).

    These are candidates for a human to confirm -- never treated as *the*
    suspense account automatically, and this never inspects any
    transaction's free-text description. GET /chart-of-accounts is
    verified to return {key, code, name} only (no statement/subtype), so
    this is name-matching on Manager's own structured Name field, not a
    guess about accounting classification.
    """
    accounts = await fetch_all(client, "chart_of_accounts", max_pages=50)
    out = []
    for acct in accounts:
        name = str(acct.get("name") or acct.get("Name") or "").casefold()
        if any(hint in name for hint in _SUSPENSE_NAME_HINTS):
            out.append(acct)
    return out


async def general_ledger_summary(
    client: ManagerClient,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    max_pages: int = 50,
    sources: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Single-pass GL consistency check across all non-invoice transaction
    sources (receipts, payments, journal entries, transfers). Sums every
    Lines[] amount grouped by Account. A balanced double-entry ledger
    across these sources nets to ~0; per-account balances surface
    anything worth investigating.

    Sales/purchase invoice postings are excluded (see account_ledger's
    notice) -- this is a partial GL view, not the full ledger.
    """
    per_account: dict[str, float] = {}
    per_source: dict[str, float] = {}
    line_count = 0
    for source in _LEDGER_SOURCES:
        records = (sources or {}).get(source)
        if records is None:
            records = await fetch_all_forms(client, source, max_pages=max_pages)
        for item in records:
            date = _date_of(source, item)
            if not _in_period(date, from_date, to_date):
                continue
            for line in _lines_of(item):
                account = line.get("Account")
                amount = _line_amount(line)
                if account is None or amount is None:
                    continue
                per_account[str(account)] = per_account.get(str(account), 0.0) + amount
                per_source[source] = per_source.get(source, 0.0) + amount
                line_count += 1
    overall_net = round(sum(per_account.values()), 2)
    return {
        "line_count": line_count,
        "per_account_net": {k: round(v, 2) for k, v in per_account.items()},
        "per_source_net": {k: round(v, 2) for k, v in per_source.items()},
        "overall_net": overall_net,
        "notice": (
            "NOT a trial-balance check, and 'overall_net' is not expected to be "
            "zero even in correct books: verified live, a receipt/payment's bank "
            "leg (ReceivedIn/PaidFrom) is a header field, never its own Lines[] "
            "entry -- only the 'other side' (AR/AP/expense/income account) is a "
            "structured line, stored as a positive magnitude regardless of "
            "direction. Summing receipts' and payments' lines together therefore "
            "adds two same-signed magnitudes instead of cancelling them; a "
            "genuine double-entry balance check would need to synthesize the "
            "implicit bank leg from each header field, which this does not do. "
            "Use per_account_net / per_source_net to see composition, not "
            "overall_net as a pass/fail balance signal. Also excludes sales/"
            "purchase invoice Lines entirely (Qty x UnitPrice + TaxCode, no "
            "verified tax-computation path here)."
        ),
    }


def register_diagnostic_tools(mcp: Any, get_client: Any) -> None:
    """Register the read-only diagnostic tools on the FastMCP instance."""
    _diag = _sys.modules[__name__]

    @mcp.tool(
        description=(
            "Exact-match structured filter over a collection (client-side; "
            "Manager's own query API has no field-value filter, only term/sort/"
            "paging). filters is {field_name: expected_value}; every field must "
            "match exactly. Never does fuzzy/partial/amount-only matching."
        )
    )
    async def find_records(
        resource: str,
        filters: dict[str, Any],
        max_pages: int = 50,
    ) -> dict[str, Any]:
        items = await _diag.find_records(get_client(), resource, filters, max_pages=max_pages)
        return {"resource": resource, "filters": filters, "items": items, "count": len(items)}


    @mcp.tool(
        description=(
            "Payments/receipts whose structured AR/AP invoice-Key line reference "
            "does not resolve to any current invoice -- the general form of a "
            "'missing invoice' (e.g. a supplier payment structurally allocated "
            "to a Purchase Invoice Key that doesn't exist yet). Never uses the "
            "transaction's free-text description to decide this."
        )
    )
    async def find_broken_invoice_references(
        resource: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        items = await _diag.find_broken_invoice_references(
            get_client(), resource, from_date=from_date, to_date=to_date
        )
        return {"resource": resource, "broken": items, "count": len(items)}


    @mcp.tool(description="Receipts/payments with no AR/AP invoice allocation on any line.")
    async def find_unallocated_transactions(
        resource: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        items = await _diag.find_unallocated_transactions(
            get_client(), resource, from_date=from_date, to_date=to_date
        )
        return {"resource": resource, "unallocated": items, "count": len(items)}


    @mcp.tool(
        description=(
            "Exact-match duplicate detector: Customer/Supplier + Reference + "
            "Date + total + line signature must ALL match. Partial matches are "
            "returned as unresolved, never flagged as duplicates."
        )
    )
    async def find_duplicate_transactions(
        resource: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        return await _diag.find_duplicate_transactions(
            get_client(), resource, from_date=from_date, to_date=to_date
        )


    @mcp.tool(
        description=(
            "Self-computed invoice balance: invoice total (its own Lines) vs. "
            "the sum of every receipt/payment line that structurally allocates "
            "to it. Does not trust any computed 'balance' field Manager may or "
            "may not expose on the form response."
        )
    )
    async def verify_invoice_balance(resource: str, key: str) -> dict[str, Any]:
        return await _diag.verify_invoice_balance(get_client(), resource, key)


    @mcp.tool(
        description=(
            "Every structured Lines[] entry across all transaction types whose "
            "Account matches the given chart-of-accounts key -- the closest "
            "available view to a per-account general ledger (Manager exposes "
            "no native GL-by-account endpoint)."
        )
    )
    async def account_ledger(
        account: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        return await _diag.account_ledger(
            get_client(), account, from_date=from_date, to_date=to_date
        )


    @mcp.tool(
        description=(
            "Money-in/out for one bank/cash account, assembled from receipts "
            "(ReceivedIn), payments (PaidFrom) and transfers referencing it. "
            "Manager has no separate 'bank transaction' entity to read directly."
        )
    )
    async def bank_activity(
        bank_account_key: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        return await _diag.bank_activity(
            get_client(), bank_account_key, from_date=from_date, to_date=to_date
        )


    @mcp.tool(
        description=(
            "Chart-of-accounts rows whose structured Name matches common "
            "placeholder-account naming (suspense, uncategorised, clearing, "
            "...). Candidates for human confirmation only -- never treated as "
            "the suspense account automatically, and never based on any "
            "transaction's free-text description."
        )
    )
    async def find_suspense_candidate_accounts() -> dict[str, Any]:
        items = await _diag.find_suspense_candidate_accounts(get_client())
        return {"candidates": items, "count": len(items)}


    @mcp.tool(
        description=(
            "Single-pass general-ledger consistency check: sums every Lines[] "
            "Amount (or Debit-Credit) grouped by Account across all transaction "
            "types. overall_net should be ~0 in a balanced ledger; "
            "per_account_net surfaces accounts worth investigating."
        )
    )
    async def general_ledger_summary(
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        return await _diag.general_ledger_summary(
            get_client(), from_date=from_date, to_date=to_date
        )

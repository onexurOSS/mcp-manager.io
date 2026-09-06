"""Curated Manager.io resource allowlist (collections + report shortcuts)."""

from __future__ import annotations

from dataclasses import dataclass, field

from manager_mcp.writable import WRITABLE
from manager_mcp.xalterra_read_collections import XALTERRA_READ_ONLY_COLLECTIONS


@dataclass(frozen=True)
class ResourceDescriptor:
    name: str
    kind: str  # "collection" | "report"
    path: str
    description: str
    supports_form: bool = False
    form_path_template: str | None = None  # e.g. "/customer-form/{key}"
    items_key: str | None = None  # envelope key in list GET responses
    date_params: tuple[str, ...] = field(default_factory=tuple)


def _collection(
    name: str,
    path: str,
    description: str,
    *,
    form_template: str | None,
    items_key: str,
) -> ResourceDescriptor:
    return ResourceDescriptor(
        name,
        "collection",
        path,
        description,
        supports_form=form_template is not None,
        form_path_template=form_template,
        items_key=items_key,
    )


# Core v1 collections + reports. Writable domains are merged below so agents
# can list/get what create_*/update_* may post (e.g. receipts).
_BASE_RESOURCES: dict[str, ResourceDescriptor] = {
    "customers": _collection(
        "customers",
        "/customers",
        "Customers collection (search, page, fetch by key)",
        form_template="/customer-form/{key}",
        items_key="customers",
    ),
    "suppliers": _collection(
        "suppliers",
        "/suppliers",
        "Suppliers collection (search, page, fetch by key)",
        form_template="/supplier-form/{key}",
        items_key="suppliers",
    ),
    "sales_invoices": _collection(
        "sales_invoices",
        "/sales-invoices",
        "Sales invoices collection",
        form_template="/sales-invoice-form/{key}",
        items_key="salesInvoices",
    ),
    "purchase_invoices": _collection(
        "purchase_invoices",
        "/purchase-invoices",
        "Purchase invoices collection",
        form_template="/purchase-invoice-form/{key}",
        items_key="purchaseInvoices",
    ),
    "chart_of_accounts": _collection(
        "chart_of_accounts",
        "/chart-of-accounts",
        "Chart of accounts collection (list/search only; no single form endpoint)",
        form_template=None,
        items_key="chartOfAccounts",
    ),
    "bank_accounts": _collection(
        "bank_accounts",
        "/bank-and-cash-accounts",
        "Bank/cash accounts collection for search and drill-in "
        "(distinct from bank_balances snapshot)",
        form_template="/bank-or-cash-account-form/{key}",
        items_key="bankAndCashAccounts",
    ),
    # Reports: Manager "*-form" report builders require POST (writes). v1 uses
    # GET-only equivalents validated live — customers/suppliers AR/AP fields,
    # bank-and-cash list balances, and *-transactions statement feeds.
    "aged_receivables": ResourceDescriptor(
        "aged_receivables",
        "report",
        "/customers",
        "Outstanding customer balances (from customers list; no POST aged-receivables-form)",
        items_key="customers",
    ),
    "aged_payables": ResourceDescriptor(
        "aged_payables",
        "report",
        "/suppliers",
        "Outstanding supplier balances (from suppliers list; no POST aged-payables-form)",
        items_key="suppliers",
    ),
    "bank_balances": ResourceDescriptor(
        "bank_balances",
        "report",
        "/bank-and-cash-accounts",
        "Bank/cash balances snapshot (distinct from bank_accounts collection drill-in)",
        items_key="bankAndCashAccounts",
    ),
    "trial_balance": ResourceDescriptor(
        "trial_balance",
        "report",
        "/trial-balance-transactions",
        "Trial balance snapshot (transactions feed)",
        items_key="trialBalanceTransactions",
        date_params=("fromDate", "toDate"),
    ),
    "profit_and_loss": ResourceDescriptor(
        "profit_and_loss",
        "report",
        "/profit-and-loss-statement-transactions",
        "Profit and loss snapshot (transactions feed)",
        items_key="profitAndLossStatementTransactions",
        date_params=("fromDate", "toDate"),
    ),
    "balance_sheet": ResourceDescriptor(
        "balance_sheet",
        "report",
        "/balance-sheet-transactions",
        "Balance sheet snapshot (transactions feed)",
        items_key="balanceSheetTransactions",
        date_params=("fromDate", "toDate"),
    ),
    "tax_summary": ResourceDescriptor(
        "tax_summary",
        "report",
        "/tax-summary-transactions",
        "Tax summary snapshot (transactions feed)",
        items_key="taxSummaryTransactions",
    ),
}


def _merge_writable_collections(
    base: dict[str, ResourceDescriptor],
) -> dict[str, ResourceDescriptor]:
    merged = dict(base)
    for writable in WRITABLE.values():
        if writable.name in merged:
            continue
        label = writable.name.replace("_", " ")
        merged[writable.name] = _collection(
            writable.name,
            writable.list_path,
            (
                f"{label} collection (search, page, fetch by key; "
                f"mutations via create_{writable.tool_stem} / "
                f"update_{writable.tool_stem} / delete_{writable.tool_stem} when scoped)"
            ),
            form_template=f"{writable.form_path}/{{key}}",
            items_key=writable.items_key,
        )
    return merged


def _merge_xalterra_read_only_collections(
    base: dict[str, ResourceDescriptor],
) -> dict[str, ResourceDescriptor]:
    merged = dict(base)
    for name, path, description, form_template, items_key in XALTERRA_READ_ONLY_COLLECTIONS:
        if name in merged:
            continue
        merged[name] = _collection(
            name, path, description, form_template=form_template, items_key=items_key
        )
    return merged


_RESOURCES: dict[str, ResourceDescriptor] = _merge_xalterra_read_only_collections(
    _merge_writable_collections(_BASE_RESOURCES)
)


def resolve(name: str) -> ResourceDescriptor | None:
    return _RESOURCES.get(name)


def all_resources() -> list[ResourceDescriptor]:
    return list(_RESOURCES.values())


def form_path(name: str, key: str) -> str | None:
    desc = resolve(name)
    if desc is None or not desc.supports_form or not desc.form_path_template:
        return None
    return desc.form_path_template.replace("{key}", key)


def extract_items(desc: ResourceDescriptor, body: object) -> list[object]:
    """Unwrap Manager list envelopes into a flat items list."""
    if body is None:
        return []
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        if desc.items_key and isinstance(body.get(desc.items_key), list):
            return body[desc.items_key]
        if isinstance(body.get("items"), list):
            return body["items"]
    return [body]

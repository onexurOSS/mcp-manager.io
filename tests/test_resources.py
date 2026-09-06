"""Allowlist resolve / form-path tests."""

from __future__ import annotations

from manager_mcp.resources import all_resources, extract_items, form_path, resolve

CORE_COLLECTIONS = {
    "customers",
    "suppliers",
    "sales_invoices",
    "purchase_invoices",
    "chart_of_accounts",
    "bank_accounts",
}
# Synced from writable.py so create_* results can be verified via get_record.
WRITABLE_COLLECTIONS = {
    "sales_quotes",
    "purchase_quotes",
    "sales_orders",
    "purchase_orders",
    "inventory_items",
    "non_inventory_items",
    "credit_notes",
    "delivery_notes",
    "debit_notes",
    "goods_receipts",
    "receipts",
    "payments",
    "inter_account_transfers",
    "employees",
    "payslips",
    "expense_claims",
    "journal_entries",
    "depreciation_entries",
    "amortization_entries",
}
# Xalterra addition (src/manager_mcp/xalterra_read_collections.py): read-only,
# never in WRITABLE. See tests/test_xalterra_read_collections.py for coverage.
XALTERRA_COLLECTIONS = {
    "fixed_assets",
    "tax_codes",
}
COLLECTIONS = CORE_COLLECTIONS | WRITABLE_COLLECTIONS | XALTERRA_COLLECTIONS
REPORTS = {
    "aged_receivables",
    "aged_payables",
    "bank_balances",
    "trial_balance",
    "profit_and_loss",
    "balance_sheet",
    "tax_summary",
}


def test_resolve_all_allowlisted() -> None:
    for name in COLLECTIONS | REPORTS:
        desc = resolve(name)
        assert desc is not None
        assert desc.name == name
        assert desc.path.startswith("/")


def test_resolve_miss() -> None:
    assert resolve("not_a_resource") is None


def test_form_path_collections() -> None:
    assert form_path("customers", "abc-guid") == "/customer-form/abc-guid"
    assert form_path("suppliers", "k") == "/supplier-form/k"
    assert form_path("sales_invoices", "x") == "/sales-invoice-form/x"
    assert form_path("purchase_invoices", "x") == "/purchase-invoice-form/x"
    assert form_path("bank_accounts", "k1") == "/bank-or-cash-account-form/k1"
    assert form_path("receipts", "r1") == "/receipt-form/r1"
    assert form_path("payments", "p1") == "/payment-form/p1"


def test_form_path_reports_and_coa_none() -> None:
    assert form_path("aged_receivables", "x") is None
    assert form_path("chart_of_accounts", "x") is None
    assert form_path("missing", "x") is None


def test_live_validated_paths() -> None:
    assert resolve("bank_accounts").path == "/bank-and-cash-accounts"  # type: ignore[union-attr]
    assert resolve("trial_balance").path == "/trial-balance-transactions"  # type: ignore[union-attr]
    assert resolve("trial_balance").date_params == ("fromDate", "toDate")  # type: ignore[union-attr]
    assert resolve("tax_summary").date_params == ()  # type: ignore[union-attr]


def test_all_resources_count() -> None:
    names = {r.name for r in all_resources()}
    assert names == COLLECTIONS | REPORTS


def test_bank_dual_descriptions() -> None:
    accounts = resolve("bank_accounts")
    balances = resolve("bank_balances")
    assert accounts is not None and balances is not None
    assert "bank_balances" in accounts.description
    assert "bank_accounts" in balances.description


def test_extract_items_envelope() -> None:
    desc = resolve("customers")
    assert desc is not None
    body = {"business": {}, "totalRecords": 2, "customers": [{"key": "1"}, {"key": "2"}]}
    assert len(extract_items(desc, body)) == 2

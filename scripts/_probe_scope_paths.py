"""Confirm OpenAPI list/form paths for all scoped resources (no mutations)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "specs" / "001-manager-readonly-mcp" / "_scope-paths-probe.json"

# resource_key -> expected (list_path, form_path, items_key_guess)
EXPECTED = {
    "sales_quotes": ("/sales-quotes", "/sales-quote-form", "salesQuotes"),
    "purchase_quotes": ("/purchase-quotes", "/purchase-quote-form", "purchaseQuotes"),
    "sales_orders": ("/sales-orders", "/sales-order-form", "salesOrders"),
    "purchase_orders": ("/purchase-orders", "/purchase-order-form", "purchaseOrders"),
    "customers": ("/customers", "/customer-form", "customers"),
    "suppliers": ("/suppliers", "/supplier-form", "suppliers"),
    "inventory_items": ("/inventory-items", "/inventory-item-form", "inventoryItems"),
    "non_inventory_items": (
        "/non-inventory-items",
        "/non-inventory-item-form",
        "nonInventoryItems",
    ),
    "sales_invoices": ("/sales-invoices", "/sales-invoice-form", "salesInvoices"),
    "credit_notes": ("/credit-notes", "/credit-note-form", "creditNotes"),
    "delivery_notes": ("/delivery-notes", "/delivery-note-form", "deliveryNotes"),
    "purchase_invoices": ("/purchase-invoices", "/purchase-invoice-form", "purchaseInvoices"),
    "debit_notes": ("/debit-notes", "/debit-note-form", "debitNotes"),
    "goods_receipts": ("/goods-receipts", "/goods-receipt-form", "goodsReceipts"),
    "receipts": ("/receipts", "/receipt-form", "receipts"),
    "payments": ("/payments", "/payment-form", "payments"),
    "inter_account_transfers": (
        "/inter-account-transfers",
        "/inter-account-transfer-form",
        "interAccountTransfers",
    ),
    "employees": ("/employees", "/employee-form", "employees"),
    "payslips": ("/payslips", "/payslip-form", "payslips"),
    "expense_claims": ("/expense-claims", "/expense-claim-form", "expenseClaims"),
    "journal_entries": ("/journal-entries", "/journal-entry-form", "journalEntries"),
    "depreciation_entries": (
        "/depreciation-entries",
        "/depreciation-entry-form",
        "depreciationEntries",
    ),
    "amortization_entries": (
        "/amortization-entries",
        "/amortization-entry-form",
        "amortizationEntries",
    ),
}


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> None:
    env = load_env(ROOT / ".env")
    base = env["MANAGER_API_URL"].rstrip("/")
    headers = {"X-API-KEY": env["MANAGER_API_KEY"], "Accept": "application/json"}
    with httpx.Client(timeout=60.0, headers=headers) as client:
        openapi = client.get(base).json()
        paths = openapi["paths"]
        report: dict[str, object] = {}
        for resource, (list_path, form_path, items_key) in EXPECTED.items():
            list_methods = sorted(
                m
                for m in (paths.get(list_path) or {})
                if m.lower() in {"get", "post", "put", "patch", "delete"}
            )
            form_methods = sorted(
                m
                for m in (paths.get(form_path) or {})
                if m.lower() in {"get", "post", "put", "patch", "delete"}
            )
            form_key = f"{form_path}/{{key}}"
            form_key_methods = sorted(
                m
                for m in (paths.get(form_key) or {})
                if m.lower() in {"get", "post", "put", "patch", "delete"}
            )
            list_status = client.get(base + list_path, params={"pageSize": 1}).status_code
            report[resource] = {
                "list_path": list_path,
                "list_methods": list_methods,
                "list_http": list_status,
                "form_path": form_path,
                "form_methods": form_methods,
                "form_key_methods": form_key_methods,
                "items_key": items_key,
                "ok": bool(list_methods)
                and "post" in form_methods
                and "put" in form_key_methods
                and "delete" in form_key_methods
                and list_status == 200,
            }
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    ok = sum(1 for v in report.values() if v["ok"])  # type: ignore[index]
    print(f"ok={ok}/{len(report)}")
    for name, row in report.items():
        flag = "OK" if row["ok"] else "MISS"  # type: ignore[index]
        print(
            f"{flag} {name}: list={row['list_methods']}/{row['list_http']} "  # type: ignore[index]
            f"form={row['form_methods']} key={row['form_key_methods']}"  # type: ignore[index]
        )


if __name__ == "__main__":
    main()

"""Writable resource descriptors (scoped mutations)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WritableResource:
    name: str
    scope: str
    form_path: str  # e.g. /sales-quote-form
    list_path: str
    items_key: str
    tool_stem: str  # create_{stem} / update_{stem} / delete_{stem}
    implemented: bool = False  # tools registered only when True
    create_notes: str = ""
    # When non-empty, create/update tools validate bodies (banking hardening).
    known_keys: frozenset[str] = field(default_factory=frozenset)
    known_line_keys: frozenset[str] = field(default_factory=frozenset)
    required_keys: frozenset[str] = field(default_factory=frozenset)


def _w(
    name: str,
    scope: str,
    form_path: str,
    list_path: str,
    items_key: str,
    *,
    tool_stem: str | None = None,
    implemented: bool = False,
    create_notes: str = "",
    known_keys: frozenset[str] | None = None,
    known_line_keys: frozenset[str] | None = None,
    required_keys: frozenset[str] | None = None,
) -> WritableResource:
    return WritableResource(
        name=name,
        scope=scope,
        form_path=form_path,
        list_path=list_path,
        items_key=items_key,
        tool_stem=tool_stem or name,
        implemented=implemented,
        create_notes=create_notes,
        known_keys=known_keys or frozenset(),
        known_line_keys=known_line_keys or frozenset(),
        required_keys=required_keys or frozenset(),
    )


_DEFAULT_NOTES = (
    "Body is Manager-native JSON (opaque dict). 201 create responses include Key. "
    "PUT is full-document replace — GET form → modify → PUT. "
    "Paths verified against live OpenAPI on TEST books."
)

_QUOTE_SALES_NOTES = (
    "POST body is Manager-native JSON (opaque dict). Empty {} creates a "
    "draft; typical fields: Customer, IssueDate, Description, Lines. "
    "201 response includes Key. PUT is full-document replace — GET then PUT."
)

_QUOTE_PURCHASE_NOTES = (
    "Typical fields: Supplier, Date, Description, Lines (not Customer/IssueDate). "
    "201 response includes Key. PUT is full-document replace."
)

_BANKING_WORKFLOW = (
    "MCP rejects empty bodies and unknown field names before POST. "
    "Workflow: list_resources → get_record template → create_* → verify warnings. "
    "Clone Lines from the template. PaidBy must be int. "
    "FX: Amount on AR lines is base currency; Manager clears USD AR using the "
    "invoice rate, not receipt ExchangeRate alone — set AR Amount in invoice-rate "
    "ZAR (or add an explicit FX line) so bank net matches."
)

_RECEIPT_KNOWN = frozenset(
    {
        "AmountsAreTaxExclusive",
        "CustomFields",
        "CustomFields2",
        "CustomThemeId",
        "Customer",
        "Date",
        "Description",
        "ExchangeRate",
        "HasLineNumber",
        "Key",
        "Lines",
        "PaidBy",
        "ProjectEnabled",
        "ReceivedIn",
        "Reference",
        "TaxCodeEnabled",
        "UniqueName",
        "id",
        "text",
    }
)
_RECEIPT_LINE_KNOWN = frozenset(
    {
        "Account",
        "AccountsReceivableCustomer",
        "AccountsReceivableSalesInvoice",
        "Amount",
        "Description",
        "LineDescription",
    }
)
_RECEIPT_REQUIRED = frozenset({"ReceivedIn", "Customer", "Date", "Lines"})

_PAYMENT_KNOWN = frozenset(
    {
        "AmountsAreTaxExclusive",
        "CustomFields",
        "CustomFields2",
        "CustomThemeId",
        "Date",
        "Description",
        "ExchangeRate",
        "HasLineNumber",
        "Key",
        "Lines",
        "PaidBy",
        "PaidFrom",
        "ProjectEnabled",
        "Reference",
        "Supplier",
        "TaxCodeEnabled",
        "UniqueName",
        "id",
        "text",
    }
)
_PAYMENT_LINE_KNOWN = frozenset(
    {
        "Account",
        "AccountsPayableSupplier",
        "AccountsPayablePurchaseInvoice",
        "Amount",
        "Description",
        "LineDescription",
    }
)
_PAYMENT_REQUIRED = frozenset({"PaidFrom", "Date", "Lines"})

_RECEIPT_NOTES = (
    "Known header keys include ReceivedIn, Customer, Date, PaidBy (int), "
    "ExchangeRate, Description, Reference, Lines. Line keys include Amount, "
    "AccountsReceivableCustomer, AccountsReceivableSalesInvoice, Account. "
    + _BANKING_WORKFLOW
)

_PAYMENT_NOTES = (
    "Known header keys include PaidFrom, Supplier, Date, PaidBy (int), "
    "ExchangeRate, Description, Reference, Lines. "
    + _BANKING_WORKFLOW
)

# Paths confirmed via live OpenAPI (Malva dev, 2026-07-28).
WRITABLE: dict[str, WritableResource] = {
    # quotes
    "sales_quotes": _w(
        "sales_quotes",
        "quotes",
        "/sales-quote-form",
        "/sales-quotes",
        "salesQuotes",
        tool_stem="sales_quote",
        implemented=True,
        create_notes=_QUOTE_SALES_NOTES,
    ),
    "purchase_quotes": _w(
        "purchase_quotes",
        "quotes",
        "/purchase-quote-form",
        "/purchase-quotes",
        "purchaseQuotes",
        tool_stem="purchase_quote",
        implemented=True,
        create_notes=_QUOTE_PURCHASE_NOTES,
    ),
    # orders
    "sales_orders": _w(
        "sales_orders",
        "orders",
        "/sales-order-form",
        "/sales-orders",
        "salesOrders",
        tool_stem="sales_order",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    "purchase_orders": _w(
        "purchase_orders",
        "orders",
        "/purchase-order-form",
        "/purchase-orders",
        "purchaseOrders",
        tool_stem="purchase_order",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    # parties
    "customers": _w(
        "customers",
        "parties",
        "/customer-form",
        "/customers",
        "customers",
        tool_stem="customer",
        implemented=True,
        create_notes=_DEFAULT_NOTES + " Name is commonly required for a useful customer.",
    ),
    "suppliers": _w(
        "suppliers",
        "parties",
        "/supplier-form",
        "/suppliers",
        "suppliers",
        tool_stem="supplier",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    # items
    "inventory_items": _w(
        "inventory_items",
        "items",
        "/inventory-item-form",
        "/inventory-items",
        "inventoryItems",
        tool_stem="inventory_item",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    "non_inventory_items": _w(
        "non_inventory_items",
        "items",
        "/non-inventory-item-form",
        "/non-inventory-items",
        "nonInventoryItems",
        tool_stem="non_inventory_item",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    # sales
    "sales_invoices": _w(
        "sales_invoices",
        "sales",
        "/sales-invoice-form",
        "/sales-invoices",
        "salesInvoices",
        tool_stem="sales_invoice",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    "credit_notes": _w(
        "credit_notes",
        "sales",
        "/credit-note-form",
        "/credit-notes",
        "creditNotes",
        tool_stem="credit_note",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    "delivery_notes": _w(
        "delivery_notes",
        "sales",
        "/delivery-note-form",
        "/delivery-notes",
        "deliveryNotes",
        tool_stem="delivery_note",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    # purchases
    "purchase_invoices": _w(
        "purchase_invoices",
        "purchases",
        "/purchase-invoice-form",
        "/purchase-invoices",
        "purchaseInvoices",
        tool_stem="purchase_invoice",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    "debit_notes": _w(
        "debit_notes",
        "purchases",
        "/debit-note-form",
        "/debit-notes",
        "debitNotes",
        tool_stem="debit_note",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    "goods_receipts": _w(
        "goods_receipts",
        "purchases",
        "/goods-receipt-form",
        "/goods-receipts",
        "goodsReceipts",
        tool_stem="goods_receipt",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    # banking
    "receipts": _w(
        "receipts",
        "banking",
        "/receipt-form",
        "/receipts",
        "receipts",
        tool_stem="receipt",
        implemented=True,
        create_notes=_RECEIPT_NOTES,
        known_keys=_RECEIPT_KNOWN,
        known_line_keys=_RECEIPT_LINE_KNOWN,
        required_keys=_RECEIPT_REQUIRED,
    ),
    "payments": _w(
        "payments",
        "banking",
        "/payment-form",
        "/payments",
        "payments",
        tool_stem="payment",
        implemented=True,
        create_notes=_PAYMENT_NOTES,
        known_keys=_PAYMENT_KNOWN,
        known_line_keys=_PAYMENT_LINE_KNOWN,
        required_keys=_PAYMENT_REQUIRED,
    ),
    "inter_account_transfers": _w(
        "inter_account_transfers",
        "banking",
        "/inter-account-transfer-form",
        "/inter-account-transfers",
        "interAccountTransfers",
        tool_stem="inter_account_transfer",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    "bank_accounts": _w(
        "bank_accounts",
        "banking",
        "/bank-or-cash-account-form",
        "/bank-and-cash-accounts",
        "bankAndCashAccounts",
        tool_stem="bank_account",
        implemented=True,
        create_notes=(
            "Create a Bank/Cash account (e.g. Customer deposits). "
            "Required: Name. Clone get_record on bank_accounts when unsure. "
            "Used as ReceivedIn for deposit receipts."
        ),
        known_keys=frozenset(
            {
                "Name",
                "Currency",
                "Key",
                "id",
                "text",
                "UniqueName",
                "CustomFields",
                "CustomFields2",
            }
        ),
        required_keys=frozenset({"Name"}),
    ),
    # payroll
    "employees": _w(
        "employees",
        "payroll",
        "/employee-form",
        "/employees",
        "employees",
        tool_stem="employee",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    "payslips": _w(
        "payslips",
        "payroll",
        "/payslip-form",
        "/payslips",
        "payslips",
        tool_stem="payslip",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    "expense_claims": _w(
        "expense_claims",
        "payroll",
        "/expense-claim-form",
        "/expense-claims",
        "expenseClaims",
        tool_stem="expense_claim",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    # ledger
    "journal_entries": _w(
        "journal_entries",
        "ledger",
        "/journal-entry-form",
        "/journal-entries",
        "journalEntries",
        tool_stem="journal_entry",
        implemented=True,
        create_notes=_DEFAULT_NOTES + " Prefer TEST books; ledger mutations are high impact.",
    ),
    "depreciation_entries": _w(
        "depreciation_entries",
        "ledger",
        "/depreciation-entry-form",
        "/depreciation-entries",
        "depreciationEntries",
        tool_stem="depreciation_entry",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
    "amortization_entries": _w(
        "amortization_entries",
        "ledger",
        "/amortization-entry-form",
        "/amortization-entries",
        "amortizationEntries",
        tool_stem="amortization_entry",
        implemented=True,
        create_notes=_DEFAULT_NOTES,
    ),
}


def implemented_for_scope(scope: str) -> list[WritableResource]:
    return [w for w in WRITABLE.values() if w.scope == scope and w.implemented]

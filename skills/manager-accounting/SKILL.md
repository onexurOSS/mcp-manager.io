---
name: manager-accounting
description: >-
  Use when the user asks about Manager.io books, balances, customers who owe
  money, payables, bank, deposits, customer deposits, deposit invoices,
  record a payment, issue an invoice, post a journal, trial balance, P&L,
  balance sheet, tax, or scoped task tools and create/update/delete.
  Always call list_resources first.
---

# Manager.io accounting

Pairs with the **manager-mcp** MCP server.

## Discovery first

1. Call `list_resources`.
2. Trust its `read_only`, `write_scopes`, `delete_scopes`, `effective_write_scopes`, and `boundary`.
3. Use only collections named in that response for `list_records` / `get_record`.

## Boundary

- Empty scopes → read-only tool set (10 tools).
- Mutations need `MANAGER_MCP_WRITE_SCOPES` (create/update) and/or
  `MANAGER_MCP_DELETE_SCOPES` (delete). Delete is never implied by write.
- Recommended write scopes: `banking,sales,parties` (not all nine domains).
- Never attempt access-token, chart-of-accounts / control-account forms,
  tax/currency minting, starting balances, or other denylisted paths.
- `raw` in WRITE_SCOPES restores the full CRUD set (advanced escape hatch).

## Config

- `MANAGER_API_URL` - opaque base URL (include `/api2` when required)
- `MANAGER_API_KEY` - `X-API-KEY`; never echo
- Scope CSVs must match between local `.env` and the MCP host `env` block
- If a tool says Manager is not reachable: tell the user to open Manager
  (API enabled) and retry. Do not treat it as an MCP server crash.

## Precondition responses

When a tool returns `status: precondition_failed`:

1. Relay each item's `how_to_create` text to the user **verbatim**.
2. Stop. Do not invent a workaround or skip setup steps.

## Task tools (prefer over CRUD)

Registered when required scopes are in `effective_write_scopes`:

| Tool | Scopes | Bookkeeper sentence |
|------|--------|---------------------|
| `issue_sales_invoice` | sales | Issue a sales invoice |
| `issue_purchase_invoice` | purchases | Issue a purchase invoice |
| `issue_quote` | quotes | Issue a quote |
| `convert_quote_to_invoice` | quotes + sales | Turn this quote into an invoice |
| `record_customer_payment` | banking | Record a customer payment against an invoice |
| `record_supplier_payment` | banking | Pay a supplier invoice |
| `record_expense` | payroll and/or purchases | Record an expense |
| `transfer_between_accounts` | banking | Move money between accounts |
| `post_journal_entry` | ledger | Post a journal entry |
| `void_document` | matching delete scope | Void this document |
| `record_customer_deposit` | banking | Record a customer deposit |
| `issue_deposit_invoice` | quotes | Issue a deposit invoice document |
| `apply_deposit_to_invoice` | ledger | Apply held deposit to an invoice |

`create_customer` and `create_supplier` remain as single-resource CRUD tools (parties scope).

Per-resource CRUD is **deprecated in 0.2.0** (removed in 0.3.0). Use task tools unless `raw` scope is set.

## Verify after write

For any mutation:

1. Prefer `get_record` on a **similar** existing row as a body template.
2. Mutate.
3. `get_record` the returned `Key` before treating the change as done.
4. If wrong and delete scope is enabled, `void_document` or `delete_*` and retry.

## Customer deposit workflow

**A deposit is not revenue.** Do not book it to an income account. Confirm tax/VAT treatment with the user's accountant.

Order of operations:

1. **`record_customer_deposit`** (or ensure deposit bank account exists first; see preconditions).
2. **`issue_deposit_invoice`** when the customer needs a document for the deposit (optional; still a quote in Manager).
3. Raise the final sales invoice (`issue_sales_invoice` or user-created).
4. **`apply_deposit_to_invoice`** via journal entry (clone `get_record` on `journal_entries`).

Required scopes: `banking` (deposit receipt), `quotes` (deposit invoice doc), `ledger` (apply to invoice), `sales` (final invoice via MCP).

### Deposit account (Option A: guide only)

`record_customer_deposit` checks for a bank/cash account whose name contains "deposit".
If missing, it returns `precondition_failed` with UI steps. Relay those steps verbatim.
Do not book the deposit to a revenue account as a workaround.

## Banking cheat-sheet (`banking` scope)

- MCP **rejects** empty `{}`, unknown names (`BankAccount`, etc.), and non-int
  `PaidBy` before POST. Clone a `get_record` template.
- Receipts: `ReceivedIn`, `Customer`, `Date`, `PaidBy` (int), `ExchangeRate`,
  `Lines` with `Amount`, `AccountsReceivableCustomer`,
  `AccountsReceivableSalesInvoice` (and `Account` for fees/FX).
- Payments: `PaidFrom`, `Supplier`, `Date`, `Lines` (AP analogues).
- FX: AR `Amount` is base currency; Manager uses the **invoice** rate to clear
  USD. Set AR in invoice-rate ZAR (or add an FX line), not bank-net alone.
- After create, check tool `warnings` (persistence diff).

## Read tools

- `list_resources`, `list_records`, `get_record`
- Reports: `aged_receivables`, `aged_payables`, `bank_balances`,
  `trial_balance`, `profit_and_loss`, `balance_sheet`, `tax_summary`

`bank_balances` (snapshot) and `bank_accounts` (collection) are both intentional.

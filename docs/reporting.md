# Reporting and authority guide

Read this before quoting any figure from this MCP. Validated against Manager
26.8.4.3664 (653 API paths). Every reporting call is an HTTP GET.

## What Manager's API exposes

- Finished, calculated reports (Trial Balance, Aged Receivables, Balance Sheet
  and so on) are NOT returned by the API. The `*-view` routes are
  application-only and answer HTTP 401 to an API key. This MCP never calls or
  imitates them.
- Stored report settings can be read with `GET /<type>-form/<key>` (no
  calculated rows). There is no list endpoint, so the key must be known.
- Raw feeds: `*-transactions` (19 endpoints). Only 8 GET endpoints accept
  `fromDate`/`toDate`. No endpoint accepts an as-at date.
- `GET /transactions` is an account-level double-entry ledger (one row per ledger leg). Each row names its account, type,
  counterparty, tax code, debit, credit and signed amount. It ignores date parameters, so dates are filtered
  by the MCP after a complete fetch.
- Current state: customers and suppliers (balances today), bank and cash
  accounts, fixed assets, tax codes, and about 150 other collections.
- Pagination: `skip` and `pageSize`. Manager declares no maximum. 3000 is
  verified accepted on `/transactions`.

## Data classes (never confuse these)

| Class | Meaning | Tools |
|---|---|---|
| `current_state` | balances as they are today, never historical | `aged_receivables`, `aged_payables`, `bank_balances`, `list_records`, `get_record` |
| `raw_transaction_feed` | rows from a `*-transactions` feed, not a report | `trial_balance`, `profit_and_loss`, `balance_sheet`, `tax_summary` |
| `historical_transaction_data` | filtered ledger rows with a real account per row | `ledger_transactions`, `account_ledger`, `bank_activity` |
| `reconstructed` | calculated by this MCP, always `authoritative: false` | `reconstructed_trial_balance`, `reconstructed_profit_and_loss`, `reconstructed_aged_receivables`, `reconstructed_aged_payables` |
| `report_settings` | stored definition, no rows | `get_report_definition` |
| `application_only` | exists only inside Manager | none (not available) |

## Reporting tools (all read-only)

- `manager_report_catalogue`: what each Manager report is, how it can be read,
  which dates it accepts, and which tool covers it. Also classifies the older
  tools.
- `get_report_definition(report_type, key)`: settings only.
- `ledger_transactions(from_date, to_date, account, transaction_type, customer,
  supplier, bank_account, reference, tax_only, skip, limit)`: complete fetch,
  client side filters, pages with `next_skip`.
- `reconstructed_trial_balance(as_at, period_start)`, `reconstructed_profit_and_loss(from_date, to_date)`,
  `reconstructed_aged_receivables(as_at)`, `reconstructed_aged_payables(as_at)`.

Every reconstruction returns `authoritative: false`,
`official_manager_report: false`, the source, the calculation method, the
as-at date and ledger completeness. A match with a control total does not make
it official.

## Rules the tools enforce

- `aged_receivables`, `aged_payables`, `bank_balances` and `tax_summary`
  REJECT `from_date`/`to_date`. They never return today's data for a past date.
- Dates must be `YYYY-MM-DD`; other formats are refused.
- Results are never silently truncated. Paged results expose totals and
  `next_skip`; feeds report `complete` and `completeness`.

## Reconstruction method and limits

- Trial balance: balance sheet accounts are cumulative sums of signed amounts to
  the as-at date. P&L accounts cover `period_start` (default 1 January of the
  as-at year, an assumption) to as-at. Earlier P&L is shown as one prior-periods
  line so debits equal credits. Whether Manager's own Retained earnings rows
  already include closing entries is unverified.
- Aged balances: per customer or supplier from the Accounts receivable or
  payable rows dated on or before as-at, applied oldest first, aged by
  transaction date. Manager's report uses due dates and real allocations, so
  bucket splits can differ. Party totals are checked against the control
  account total only.
- Not available from the API: Manager's group and subtotal layout, invoice
  due dates and allocations as at a past date, VAT returns, and finished
  report output.

## Read and write separation

Reporting tools use `client.get` only. Write tools (`create_*`, `update_*`,
`apply_*`, `record_*`, `post_*`, `reallocate_*`) are unchanged and remain gated
by `MANAGER_MCP_WRITE_SCOPES`. `scripts/verify_reporting_controls.py` runs the
tools live and fails if any request is not a GET.

## Capability matrix

No Manager API description or generated matrix is shipped with this project. To see, per
endpoint and method, which dates, fields, paging, sorting and filters exist and which MCP tool
covers it, fetch the description from your own instance (`GET /api2`) and run
`scripts/build_capability_matrix.py` against it. Runtime never loads such a file: tools always
call the live URL.

# Research: Scoped writes — banking (receipts / payments)

**Instance**: Malva dev. Top-level + line keys verified live 2026-07-28
(template receipt #378 / posted #381 clearing invoice #525).

## Paths

| Resource | List | Form |
|----------|------|------|
| receipts | `GET /receipts` (`receipts`) | `POST /receipt-form`; `GET/PUT/DELETE /receipt-form/{key}` |
| payments | `GET /payments` (`payments`) | `POST /payment-form`; `GET/PUT/DELETE /payment-form/{key}` |

No PATCH on forms.

## Receipt header (VERIFIED)

`ReceivedIn` (bank key), `Customer`, `PaidBy` (**int**, e.g. `1`), `ExchangeRate`,
`Date`, `Description`, `Reference`, `AmountsAreTaxExclusive`, `Lines`, plus
`Key` / `id` / `text` / theme / custom-field flags on GET clones.

**Not valid:** `BankAccount`, `BankOrCashAccount`, string `PaidBy` — wrong names
are silently dropped or can 500 upstream. MCP rejects unknowns / bad types
before POST.

## Receipt `Lines[]` (VERIFIED)

| Key | Role |
|-----|------|
| `Amount` | Base-currency amount (ZAR on Malva books) |
| `AccountsReceivableCustomer` | Customer key for AR allocation |
| `AccountsReceivableSalesInvoice` | Invoice key being cleared |
| `Account` | P&amp;L / bank-charges account (fees, FX) |

## FX behavior (VERIFIED on invoice #525)

- Manager clears USD AR using the **invoice** exchange rate, not the receipt
  `ExchangeRate` alone.
- Setting AR `Amount` to bank net ZAR under-clears USD. Use invoice-rate ZAR
  for the AR line (e.g. 35,826.042) and an explicit FX line (negative) so bank
  `ReceivedIn` net matches cash (e.g. 31,012.95).

## Payments

Symmetric: `PaidFrom` instead of `ReceivedIn`; `Supplier` / AP line keys
(`AccountsPayableSupplier`, `AccountsPayablePurchaseInvoice`). MCP requires
`PaidFrom`, `Date`, `Lines` on create.

## MCP hardening

1. Reject empty body and unknown keys / bad `PaidBy` type before POST.
2. After create/update, GET form and return `warnings` if fields dropped.
3. HTTP ≥500 → `ManagerApiError` with retry guidance (template + fix types).

## Agent workflow

1. `list_resources` → `banking` in scopes.
2. `get_record` similar receipt as template.
3. `create_receipt` with known keys only.
4. Read `warnings` / `verified` in the tool result.

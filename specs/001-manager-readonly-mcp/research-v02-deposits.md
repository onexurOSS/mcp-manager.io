# Research: v0.2 deposits and task tools

**Source**: vendored `api2.json`, `research-writes-banking.md` (Malva dev, 2026-07-28).
Live re-validation on TEST books recommended before production use.

## 1. Customer deposit representation

- `/customers` supports `sortBy=AvailableCredit` and `PendingDeposits` in the vendored spec.
- Unallocated customer receipts appear to become **available credit** natively; a separate deposit bank account is optional for users who want a distinct balance-sheet liability line.
- **Assumption for v0.2**: `record_customer_deposit` still checks for a named deposit bank/cash account (Option A: guide only if missing). Simpler credit-only path may be added in a later release after live confirmation.

## 2. Deposit account type

- Deposit holding account is a **Bank/Cash account** via `/bank-or-cash-account-form` (not denylisted).
- No `/special-accounts` paths in the vendored provenance subset.

## 3. Receipt allocation payload (VERIFIED on Malva dev)

Receipt `Lines[]` for invoice allocation:

| Key | Role |
|-----|------|
| `Amount` | Base-currency amount |
| `AccountsReceivableCustomer` | Customer key |
| `AccountsReceivableSalesInvoice` | Invoice key being cleared |

## 4. Partial failure policy

Multi-step task tools return `status: partial` with created `keys`, `warnings`, and `next_steps` when a later step fails. No silent rollback.

## 5. Tax

Deposits may attract VAT depending on jurisdiction. Task tools accept optional tax-related fields only when cloned from templates; no default tax assumption.

## 6. apply_deposit_to_invoice

Per validated agent workflow: journal entry moves balance from deposit bank account to invoice/AR. Clone an existing journal via `get_record` when possible.

## 7. void_document

Manager keys are per document type. `void_document` requires `resource` (writable resource name) plus `key`.

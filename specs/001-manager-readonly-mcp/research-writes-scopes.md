# Research: Scoped writes — remaining domains (Increments 2+)

**Instance**: Malva dev (TEST/dev books). OpenAPI path probe 2026-07-28.
**Quotes deep RE**: see [research-writes-quotes.md](./research-writes-quotes.md).

## Path matrix (all VERIFIED)

Every scoped resource below has live OpenAPI:

| Scope | Resource | List GET | Form POST | Form/{key} GET,PUT,DELETE |
|-------|----------|----------|-----------|---------------------------|
| orders | sales_orders / purchase_orders | `/sales-orders`, `/purchase-orders` | `*-order-form` | yes |
| parties | customers / suppliers | `/customers`, `/suppliers` | `*-form` | yes |
| items | inventory_items / non_inventory_items | `/inventory-items`, `/non-inventory-items` | `*-item-form` | yes |
| sales | sales_invoices / credit_notes / delivery_notes | matching plurals | `*-form` | yes |
| purchases | purchase_invoices / debit_notes / goods_receipts | matching plurals | `*-form` | yes |
| banking | receipts / payments / inter_account_transfers | matching plurals | `*-form` | yes |
| payroll | employees / payslips / expense_claims | matching plurals | `*-form` | yes |
| ledger | journal_entries / depreciation_entries / amortization_entries | matching plurals | `*-form` | yes |

List HTTP GET with `pageSize=1` returned **200** for all 23 resources (including quotes).

## Shared write semantics (inferred + quotes-verified)

- **No PATCH** on these form paths in OpenAPI (same as quotes).
- Create → **201** body with `Key` (quotes verified; others assumed same Manager pattern).
- DELETE → **204**, then GET **404** (quotes verified).
- PUT → treat as **full replace** (quotes verified).
- Bodies are opaque `dict`s; OpenAPI schemas empty — agents should GET an example form or start from `{}` / known fields.
- Client denylist still blocks access tokens, tax/currency, starting balances, COA account forms, email templates, etc.

## Tool naming

Verb + singular stem: `create_sales_order`, `update_customer`, `delete_journal_entry`, …

Registration only when the resource’s scope is in `MANAGER_MCP_WRITE_SCOPES` (create/update) or `MANAGER_MCP_DELETE_SCOPES` (delete).

## Caution

Ledger / sales invoices / banking mutate money and history. Prefer TEST businesses. Scopes are a client gate, not Manager RBAC.

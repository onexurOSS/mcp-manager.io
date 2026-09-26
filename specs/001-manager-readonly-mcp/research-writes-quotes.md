# Research: Scoped writes — quotes (Increment 1)

**Instance**: Malva dev (TEST/dev books). Probed 2026-07-28 via live `/api2`.
**Status**: Answers below are **VERIFIED** on that instance unless noted.

## OpenAPI paths (quotes)

| Path | Methods |
|------|---------|
| `/sales-quotes` | GET |
| `/sales-quote-form` | POST |
| `/sales-quote-form/{key}` | GET, PUT, DELETE |
| `/purchase-quotes` | GET |
| `/purchase-quote-form` | POST |
| `/purchase-quote-form/{key}` | GET, PUT, DELETE |

Also present (out of quotes write scope / denylist-adjacent): email templates, footers, lines views, recurring sales quotes, pending sales quotes. **No PATCH** on quote forms in OpenAPI.

Envelope keys: list `salesQuotes` / `purchaseQuotes`; form JSON uses PascalCase fields (`Customer`, `IssueDate`, `Lines`, …).

## Answers to plan open questions

1. **Exact list/form paths** — **VERIFIED**: `/sales-quotes`, `/purchase-quotes`; forms `/sales-quote-form`, `/purchase-quote-form` (+ `/{key}`). Matches customer singular `*-form` pattern.
2. **Minimal JSON body** — **VERIFIED**: `POST /sales-quote-form` with `{}` returns **201** and a new `Key`. Useful create still wants `Customer`, `IssueDate`, `Description`, optional `Lines`. Purchase: `Supplier` + `Date` + `Description` succeeded (201). OpenAPI `components.schemas` remains empty.
3. **PUT semantics** — **VERIFIED**: full-document replace. Omitting `Description` on PUT cleared it (`null`/absent after). Callers should GET → modify → PUT.
4. **PATCH** — **VERIFIED unsupported for agents**: OpenAPI has no PATCH; live `PATCH` returned **401 Unauthorized**. Tools will not expose PATCH.
5. **DELETE** — **VERIFIED**: **204** empty body; subsequent GET → **404 Not found**. Hard delete (not soft) on this instance.
6. **Reversibility** — **VERIFIED**: delete is permanent for practical purposes. `/deleted-sales-quotes`, `/recycle-bin`, `/trash` → 404. Manual smoke: create → get → update → delete only on TEST books.
7. **Lock date / closed period** — **UNVERIFIED** (not exercised). Expect Manager-side 4xx with error string; surface httpx error to agent.
8. **Authz vs our scopes** — **PARTIALLY VERIFIED**: API key could create/delete quotes here. Server-side 403 still possible on other keys/editions; our scopes are an additional client gate, not a substitute for Manager permissions.
9. **Create response** — **VERIFIED**: **201** JSON body includes `Key` (and `id`); **no** `Location` header.
10. **Purchase vs sales field names** — **VERIFIED asymmetric**: sales uses `Customer` + `IssueDate` (+ `ExpiryDate`, billing, footers…); purchase uses `Supplier` + `Date`. Both use `Description`, `Lines`, `Key`.
11. **Denylist completeness** — **PARTIALLY VERIFIED**: quote email-template / theme-ish paths exist (`email-template-for-*-quote-form`); substring denylist `email-template` covers them. COA/tax patterns unchanged. Edition-specific mint paths may still appear later.
12. **No GET-only write workaround** — **VERIFIED**: mutations use `POST`/`PUT`/`DELETE` on `*-form`; list GETs remain read-only. Report `*-form` POST paths stay out of quotes scope / denylist as applicable.

## Tool contract (implementation)

When `quotes` ∈ `MANAGER_MCP_WRITE_SCOPES`:

- `create_sales_quote` / `create_purchase_quote` — `POST` form path; body `dict` (opaque); return Manager JSON (includes `Key`).
- `update_sales_quote` / `update_purchase_quote` — `PUT` form`/{key}`; body full document.

When `quotes` ∈ `MANAGER_MCP_DELETE_SCOPES`:

- `delete_sales_quote` / `delete_purchase_quote` — `DELETE` form`/{key}`.

## Sanitized fixtures note

CI tests use respx with synthetic GUIDs and minimal bodies (`{}` / `{Description: ...}`). Do not commit live customer addresses or real business payloads from probe dumps.

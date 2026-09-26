# Research: Manager.io Read-Only MCP Server

## 1. MCP server framework

**Decision**: FastMCP >=2.0 with console script entry `manager-mcp`.

**Rationale**: User-specified stack; FastMCP 2.x is the current Python MCP
server ergonomics for tool registration and stdio hosting. Lazy client
construction so `import manager_mcp.server` needs no env.

**Alternatives considered**: Raw MCP SDK only (more boilerplate); FastAPI+SSE
transport (out of scope for v1 stdio packaging).

## 2. HTTP client & read-only enforcement

**Decision**: Async `httpx.AsyncClient` wrapper in `client.py` that exposes
**GET only** (no `post`/`put`/`patch`/`delete` methods). Auth via `X-API-KEY`
from `MANAGER_API_KEY`. Base URL from `MANAGER_API_URL` (must include `/api2`
when required by the instance).

**Rationale**: Confirmed against live desktop Manager: apiKey header scheme;
flat paths under `/api2`. Structural absence of write methods beats
“discipline” alone. Spec also requires write-flag hard-fail + tool-set
regression tests.

**Alternatives considered**: Shared httpx with method allowlist at call site
(easier to regress); sync requests (blocks event loop under FastMCP async).

## 3. Query parameter allowlist

**Decision**: Client filters outbound query keys to:
`term`, `sortBy`, `sortByDesc`, `skip`, `pageSize`, `fields`, plus **only**
date/period keys that a specific report shortcut’s live path documents in
OpenAPI (discovered at implement time from vendored/`GET /api2` snapshot).
Unknown keys dropped or rejected before send.

**Rationale**: Matches confirmed collection query surface. Spec clarification:
date/period only where the live endpoint exposes the param; otherwise
current/default and disclose. Collection tools do not invent client-side
period filtering.

**Alternatives considered**: Pass-through all query params (unsafe / write-adjacent
risk); hard-code every Manager enum (brittle across editions).

## 4. Resource allowlist & report shortcuts

**Decision**: `resources.py` maps short names → relative paths, split into:

**Collections** (list + `-form/{key}` get):

| Short name | Path (confirmed pattern) | Form path |
|------------|--------------------------|-----------|
| `customers` | `/customers` | `/customers-form/{key}` |
| `suppliers` | `/suppliers` | `/suppliers-form/{key}` |
| `sales_invoices` | path from OpenAPI (e.g. `/sales-invoices`) | `…-form/{key}` |
| `purchase_invoices` | path from OpenAPI | `…-form/{key}` |
| `chart_of_accounts` | path from OpenAPI | `…-form/{key}` |
| `bank_accounts` | path from OpenAPI | `…-form/{key}` |

Exact path strings for non-`customers` rows MUST be taken from the vendored
`api2.json` / live OpenAPI at implement time (desktop edition confirmed flat).

**Report shortcuts** (named tools → GET path, no saved-report GUID):

| Tool | Role |
|------|------|
| `aged_receivables` | Outstanding / aging customers |
| `aged_payables` | Aging suppliers |
| `bank_balances` | Snapshot balances (dual with `bank_accounts` collection) |
| `trial_balance` | Trial balance |
| `profit_and_loss` | P&L |
| `balance_sheet` | Balance sheet |
| `tax_summary` | Tax summary |

`resolve(name) -> path | None`. Unknown → tool error pointing at `list_resources`.

**Rationale**: Spec curated collections + financial snapshots; constitution ~10
tools. Empty `components.schemas` and untyped bodies → do not generate tools
from full 650-path spec.

**Alternatives considered**: OpenAPI codegen (rejected by spec/constitution);
saved-report GUID tools (explicitly out of scope).

## 5. Tool surface

**Decision**:

| Tool | Purpose |
|------|---------|
| `list_resources` | Discovery + read-only boundary text |
| `list_records` | Collection page: resource, term, sort, skip, page_size |
| `get_record` | `/{resource}-form/{key}` |
| 7 report shortcuts | Named GETs above |

Truncation: when `pageSize` results returned and/or Manager indicates more,
response metadata MUST tell the agent results may be incomplete / another
`skip` page is available.

**Rationale**: Covers US1–US4 without API sprawl. Bank dual exposure is
intentional (snapshot vs drill-in).

## 6. Config & scoped writes

**Decision**:

| Variable | Role |
|----------|------|
| `MANAGER_API_URL` | Opaque API root (include `/api2`) |
| `MANAGER_API_KEY` | Token → `X-API-KEY` (prefer secret manager) |
| `MANAGER_MCP_WRITE_SCOPES` | CSV of enumerated domains → POST/PUT (and PATCH if used) |
| `MANAGER_MCP_DELETE_SCOPES` | CSV of enumerated domains → DELETE only (never implied by write) |
| `MANAGER_MCP_ALLOW_WRITES` / near-misses | Legacy → hard-fail; point to scope vars |

Missing URL/key → clear config error at first client use (lazy init). Empty
scopes → read-only tool set. Client denylist is absolute. Unknown scope
tokens / wildcards hard-fail at startup.

**Rationale**: Spec FR-005 (scoped writes). Canonical names locked for
docs/code/tests.

## 7. Packaging, skill, CI

**Decision**: hatchling; entry point `manager-mcp = manager_mcp.server:main`
(or FastMCP-recommended equivalent). Skill at
`skills/manager-accounting/SKILL.md`. CI: GitHub Actions matrix 3.10 & 3.12,
`ruff check` + `pytest`. Vendored `src/manager_mcp/spec/api2.json` is
**provenance only** — runtime reads live Manager.

**Rationale**: User layout; constitution engineering baseline.

## 8. Multi-business caveat

**Decision**: Document only. v1 assumes opaque URL uniquely identifies one
books set. Flat `/api2` confirmed for single-business desktop; multi-business
disambiguation unverified — do not claim support (out of scope).

**Rationale**: Spec clarification Q4 open item for plan/README.

## 9. Documentation sources

**Context7**: Monthly quota exceeded during planning — FastMCP API details to
be verified from package docs at implement time (`fastmcp` PyPI / project
README). Manager API facts from user confirmation against live desktop
instance (authoritative for this plan).

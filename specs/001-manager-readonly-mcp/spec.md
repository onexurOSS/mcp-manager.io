# Feature Specification: Manager.io Read-Only MCP Server

**Feature Branch**: `001-manager-readonly-mcp`

**Created**: 2026-07-28

**Status**: Draft

**Input**: User description: "Build a read-only Model Context Protocol (MCP) server that lets an AI agent query a self-hosted Manager.io accounting instance, plus a companion Agent Skill so the capability is installable from a skills registry. Strictly read-only; discoverable surface; paginated/searchable results with truncation signals; instance URL + access token via environment; Agent Skill stating read-only boundary. Out of scope: writes, saved-report GUID lookups, multi-instance."

## Clarifications

### Session 2026-07-28

- Q: For financial snapshots (P&L, balance sheet, tax, aging), can the agent specify a date/period? → A: Optional date/period only where the live endpoint exposes a date/period query param; if unsupported, that view is current/default-only in v1 and the agent must say so.
- Q: Which collections support search, paging, and fetch-by-key in v1? → A: Sales invoices, purchase invoices, chart of accounts, customers, suppliers, and bank accounts. Bank/cash is intentionally dual-exposed: snapshot balances tool and searchable/drill-in collection (not redundancy).
- Q: What counts as a write-enable attempt that must fail loudly? → A: (a) legacy boolean keys `MANAGER_MCP_ALLOW_WRITES` / `ALLOW_WRITES` / `MANAGER_MCP_WRITES` hard-fail if set (point operators to scope vars); (b) unknown/wildcard tokens in `MANAGER_MCP_WRITE_SCOPES` / `MANAGER_MCP_DELETE_SCOPES` hard-fail; (c) empty scopes → registered tools must have no create/update/delete verbs (regression-tested); (d) when scopes are set, only verb-named tools for implemented scoped resources, with client denylist absolute.
- Q: Besides access token, how does the user identify which Manager books to read? → A: `MANAGER_API_URL` + `MANAGER_API_KEY` only (URL may include business/`api2` path). Confirmed flat path layout (`/customers`, not `/{business}/customers`); a single opaque `MANAGER_API_URL` (including `/api2` when required) scopes which books are read and absorbs other editions’ path shapes. Separate business ID (B) would hard-code unsupported path structure; token-only discovery (C) needs an unconfirmed endpoint. Open for plan/README: multi-business on one instance may not be disambiguated by flat `/api2` — validate before claiming support; already out of scope for v1 (documentation caveat, not a blocker).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Outstanding Customer Balances (Priority: P1)

A business owner asks their AI agent which customers have outstanding balances. The agent uses the configured Manager.io connection to retrieve receivables-oriented data and returns a clear summary (who owes, amounts) without any ability to change the books.

**Why this priority**: This is the primary trust-and-value scenario — answer money-owed questions safely.

**Independent Test**: With a configured instance containing customers with unpaid sales invoices, ask the agent for outstanding customer balances and verify the answer matches the books and that no mutating capability is available.

**Acceptance Scenarios**:

1. **Given** a configured Manager.io instance with at least one customer owing money, **When** the user asks which customers have outstanding balances, **Then** the agent returns those customers and amounts as present in the Manager read response for that view (same keys/amounts; offline: equal to the mocked GET body).
2. **Given** a configured instance with no outstanding receivables, **When** the user asks the same question, **Then** the agent reports that no customers currently owe money (or an equivalent empty result), not an error.
3. **Given** any configured instance, **When** the user or agent inspects available capabilities, **Then** no create, edit, post, or delete capability is present.

---

### User Story 2 - Core Financial Snapshots (Priority: P1)

The same user asks for aged payables, bank/cash balances, trial balance, profit & loss, balance sheet, or tax summary. The agent retrieves the corresponding read-only financial view and answers in plain language.

**Why this priority**: Completes the “ask about my books” value set without needing writes or saved-report setup.

**Independent Test**: For each named view, request it via the agent against a configured instance and confirm figures match the Manager GET body for that path (same keys/amounts; offline: mocked equality) and that no mutation path exists.

**Acceptance Scenarios**:

1. **Given** a configured instance with supplier obligations, **When** the user asks about aged payables (who is owed / aging), **Then** the agent returns amounts as present in the Manager read response for that view (offline: equal to the mocked GET body).
2. **Given** a configured instance with bank or cash accounts, **When** the user asks for bank/cash balances, **Then** the agent returns balances as present in the Manager read response for that view (offline: equal to the mocked GET body).
3. **Given** a configured instance with posted activity, **When** the user asks for trial balance, profit & loss, balance sheet, or tax summary, **Then** the agent returns the corresponding read-only snapshot (or a clear error if that view is unavailable on the instance), without requiring a pre-saved report identifier from the user.
4. **Given** a snapshot view whose live endpoint accepts a date or period parameter, **When** the user asks for that view for a specific date or period, **Then** the agent returns data for that date/period.
5. **Given** a snapshot view whose live endpoint does not accept a date or period parameter, **When** the user asks for a historical or custom period, **Then** the agent returns the current/default view and clearly states that date/period selection is not available for that view in v1.

---

### User Story 3 - Discover What Can Be Read (Priority: P2)

An agent (or user via the agent) needs to know what accounting data can be queried before diving into specifics. Discovery lists the supported read capabilities and their purpose, including the hard read-only boundary.

**Why this priority**: Prevents blind tool use and makes the curated surface explicit.

**Independent Test**: Ask what Manager/bookkeeping data can be read; verify the response enumerates the supported read capabilities and states that writes are impossible.

**Acceptance Scenarios**:

1. **Given** a running server with valid configuration, **When** discovery is requested, **Then** the response lists the supported read capabilities with short descriptions.
2. **Given** the same setup, **When** discovery is requested, **Then** the response explicitly states that create, edit, post, and delete are not available.

---

### User Story 4 - Search, Page, and Drill Into Records (Priority: P2)

The user (via agent) searches and pages through the curated collections — sales invoices, purchase invoices, chart of accounts, customers, suppliers, and bank accounts — and opens a single record by its key when they need detail. Bank/cash accounts are also available as a balances snapshot (User Story 2); both paths are intentional: snapshot for “what are my balances?”, collection for “find account X and show its detail.”

**Why this priority**: Enables investigation beyond summaries without exposing the full mutable API.

**Independent Test**: Search a collection with a filter, page to a second page when results exceed the page size, open one record by key, and confirm truncation is signaled when results are incomplete. Separately, confirm bank accounts work both as a balances snapshot and as a searchable collection.

**Acceptance Scenarios**:

1. **Given** a collection with many records, **When** the agent requests a page of results, **Then** it receives a bounded page and a clear indication whether more results exist or the result was truncated.
2. **Given** a searchable collection, **When** the agent supplies a search term, **Then** returned items are limited to matches (or an empty page if none).
3. **Given** a known record key, **When** the agent requests that record, **Then** it receives that record’s read-only details, or a clear not-found error if the key does not exist.
4. **Given** bank/cash accounts on the instance, **When** the agent uses the balances snapshot versus searching/drilling into the bank accounts collection, **Then** both succeed; reviewers MUST treat this dual exposure as deliberate (different jobs), not as redundant tooling.

---

### User Story 5 - Installable Agent Skill (Priority: P3)

A user installs a companion Agent Skill from a skills registry. The skill’s description activates on Manager.io / bookkeeping questions and reminds the agent of the read-only boundary and how to use the MCP server.

**Why this priority**: Improves discoverability and correct agent behavior; the MCP server alone already delivers core value.

**Independent Test**: Install the skill from the project’s skill package; confirm its description mentions Manager.io/bookkeeping triggers and the read-only constraint.

**Acceptance Scenarios**:

1. **Given** the skill package in the repository, **When** a user installs it via their skills registry workflow, **Then** the skill is available to the agent with a description that triggers on Manager.io and bookkeeping questions.
2. **Given** the installed skill, **When** an agent reads it, **Then** it states that all Manager operations through this integration are read-only and must not mutate books.

---

### Edge Cases

- Invalid or missing instance URL / access token: fail at startup or first use with a clear configuration error; do not partially run with silent defaults.
- Unauthorized or expired token: return a clear authentication/authorization error to the agent; do not leak the token value.
- Unreachable instance: return a clear connectivity error.
- Empty books / empty collection pages: return empty results, not a failure.
- Unknown collection or unsupported read capability: return a clear error listing what is supported (or pointing to discovery).
- Record key not found: clear not-found error.
- Oversized result sets: enforce pagination; mark truncated/incomplete results so the agent knows to request another page or narrow the search.
- Legacy boolean write envs (`MANAGER_MCP_ALLOW_WRITES` / near-misses): refuse to start with an explicit error pointing to scope vars.
- Unknown or wildcard scope tokens: refuse to start listing valid scopes.
- Empty scopes: registered tool set MUST contain no create/update/delete verbs (regression-tested).
- Non-empty scopes: only implemented verb-named tools for those scopes; denylist paths remain denied.
- Saved-report identifiers: not required and not supported in v1; requests that depend on a pre-existing saved-report GUID are rejected or out of scope with a clear message.
- Date/period requested on a view that has no date/period query param on the live endpoint: serve current/default data and tell the agent that period selection is unsupported for that view (do not invent filtered data).

## Requirements *(mandatory)*

### Constitution Constraints *(mandatory for this project)*

Specs MUST respect `.specify/memory/constitution.md`:

- Tools/client methods are read-only unless an explicit env-flagged write path
  is in scope (off by default)
- New tools are hand-curated (no wholesale upstream API exposure)
- Secrets come from environment variables only
- Acceptance tests for network I/O MUST be offline-mockable (respx); no live
  external service as a gate
- Docs in scope explain intent/tradeoffs (no filler)

**Feature-specific hardening**: Default remains read-only. Scoped writes use
`MANAGER_MCP_WRITE_SCOPES` / `MANAGER_MCP_DELETE_SCOPES` (enumerated domains only).
Legacy boolean write flags hard-fail (FR-005). Denylist paths are never writable.

### Functional Requirements

- **FR-001**: The system MUST expose a Model Context Protocol server that an MCP client can start and use to query one Manager.io instance.
- **FR-002**: The system MUST obtain `MANAGER_API_URL` (opaque instance base URL) and `MANAGER_API_KEY` (access token) only from environment configuration; credentials MUST NOT be hard-coded or written into logs or tool responses. The URL already scopes which books are read (including any `/api2` suffix or business path segment the deployment requires); v1 MUST NOT require a separate business-identifier setting.
- **FR-003**: The system MUST reject startup or configuration when required environment values (base URL and access token) are missing or invalid, with an actionable error message.
- **FR-004**: When `MANAGER_MCP_WRITE_SCOPES` and `MANAGER_MCP_DELETE_SCOPES` are both empty/unset, the system MUST NOT expose any capability that creates, edits, posts, or deletes data in Manager.io.
- **FR-005**: Write configuration MUST fail closed and be explicit: (a) legacy env keys `MANAGER_MCP_ALLOW_WRITES`, `ALLOW_WRITES`, and `MANAGER_MCP_WRITES` MUST hard-fail at startup if set to any non-empty value, with a message pointing operators to `MANAGER_MCP_WRITE_SCOPES` / `MANAGER_MCP_DELETE_SCOPES`; (b) unknown names, wildcards (`*`, `all`), or empty tokens in those scope CSVs MUST hard-fail at startup listing valid scopes; (c) when both scope vars are empty/unset, automated checks MUST confirm the registered tool set contains no `create_`/`update_`/`delete_` (or equivalent mutating) tools; (d) when scopes are non-empty, only verb-named tools for implemented resources in those scopes MAY be registered, and a client-layer denylist MUST still block never-writable paths.
- **FR-006**: The system MUST provide a discovery capability that lists supported read operations and states the read-only boundary.
- **FR-007**: The system MUST support read access for outstanding customer balances / receivables-oriented answers.
- **FR-008**: The system MUST support read access for aged payables, bank/cash balances, trial balance, profit & loss, balance sheet, and tax summary without requiring the user to supply a saved-report GUID.
- **FR-017**: For financial snapshot views, the system MUST accept optional date/period inputs only when the live Manager endpoint exposes a corresponding query parameter; when it does not, the view MUST be current/default-only and the response MUST state that date/period selection is unavailable for that view.
- **FR-009**: The system MUST support searching and paging through exactly these curated collections in v1: sales invoices, purchase invoices, chart of accounts, customers, suppliers, and bank accounts.
- **FR-010**: The system MUST support fetching a single record by its key for those curated collections.
- **FR-018**: Bank/cash MUST be exposed in two complementary ways: (1) a snapshot capability for balances (“what are my balances?”) and (2) the bank accounts curated collection for search and drill-in (“find the account named X and show its detail”). This dual exposure is intentional and MUST NOT be treated as redundant duplication.
- **FR-011**: Paginated responses MUST include `truncated` and/or `has_more` (boolean; names as in `contracts/mcp-tools.md`, or documented equivalents) so the agent can tell whether results are complete or another page may exist.
- **FR-012**: Searchable collection reads MUST accept a search input and return a page of matching results (or an empty page).
- **FR-013**: Errors for auth failure, not found, unsupported operation, and connectivity MUST be clear and MUST NOT include secret values.
- **FR-014**: The system MUST ship a companion Agent Skill (`SKILL.md`) whose description triggers on Manager.io / bookkeeping questions and states the read-only boundary.
- **FR-015**: The curated read surface MUST be hand-picked for agent jobs; the system MUST NOT auto-expose the full Manager API surface (~650 paths).
- **FR-016**: v1 MUST support exactly one configured instance (no multi-instance routing).

### Key Entities

- **Manager Instance Connection**: Opaque base URL plus access token identifying one reachable Manager.io API root (flat `/api2`-style paths on the confirmed instance; other path shapes absorbed by the URL).
- **Read Capability**: A named, discoverable read operation (summary view, collection search/page, or record fetch) with a human-readable description and read-only guarantee.
- **Collection Page**: A bounded list of records from a curated collection, with search context and truncation / continuation metadata.
- **Record**: A single accounting object addressed by a stable key within a collection.
- **Financial Snapshot**: A read-only aggregate view (e.g. trial balance, P&L, balance sheet, tax summary, aged receivables/payables, bank/cash balances).
- **Agent Skill**: Installable instruction package that tells an agent when to use this integration and that mutation is forbidden.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can configure the server with only an instance URL and access token and, from their MCP client, complete User Story 1 (outstanding customer balances) without any mutating capability available in the tool list.
- **SC-002**: For each of aged payables, bank/cash balances, trial balance, profit & loss, balance sheet, and tax summary, a user can obtain an answer whose figures match the Manager GET body for that view (same keys/amounts; offline: mocked equality) in a single agent session, or receive a clear unsupported/unavailable message — never a silent wrong success.
- **SC-008**: When a user requests a period-specific snapshot for a view that lacks live date/period query support, the response still returns current/default data and explicitly discloses that period selection is unavailable (never silently implies the period was applied).
- **SC-003**: Discovery returns a complete list of supported read capabilities in one request, and 100% of listed capabilities are non-mutating.
- **SC-004**: When a collection has more matches than one page, the agent is informed that results are truncated or that another page is available in 100% of such responses.
- **SC-005**: Setting a legacy write env var (`MANAGER_MCP_ALLOW_WRITES` or near-miss) causes startup to fail in 100% of cases; unknown scope names fail startup; with both scope vars empty, automated verification confirms zero `create_`/`update_`/`delete_` tools.
- **SC-006**: The companion Agent Skill is installable from the project’s skill packaging and its description visibly states Manager.io/bookkeeping relevance and the read-only boundary.
- **SC-007**: A reviewer can confirm the exposed capability count stays small and curated: discovery (`list_resources`) + `list_records` + `get_record` + seven report shortcuts ≈ 10 tools (`contracts/mcp-tools.md`), not a wholesale mirror of the Manager API; bank balances snapshot plus bank accounts collection counts as two intentional capabilities, not a defect.

## Assumptions

- The user runs a self-hosted Manager.io instance reachable from the machine hosting the MCP server, with API access enabled and a valid access token.
- One MCP server process maps to one base URL (multi-instance routing is out of scope). Single-instance / single-books is assumed for the confirmed flat `/api2` layout; whether one Manager host with several businesses is disambiguated by that URL alone is unverified and MUST be validated against a live multi-business setup before v1 documentation claims multi-business support (caveat for `plan.md` / README — not a v1 feature).
- “Tax summary”, “trial balance”, “profit & loss”, and “balance sheet” refer to Manager’s standard built-in read views that do not require a user-created saved-report GUID.
- Which snapshot views accept date/period is determined by the live Manager endpoint’s query parameters at integration time; v1 does not add client-side period filtering when the endpoint lacks those params.
- The curated collections for v1 are exactly: sales invoices, purchase invoices, chart of accounts, customers, suppliers, and bank accounts — not the entire Manager object catalog.
- Bank/cash dual exposure (balances snapshot + bank accounts collection) is a product decision for two agent jobs, not accidental overlap.
- Pagination defaults (page size) may be chosen by the implementer as long as truncation/continuation is always signaled.
- The Agent Skill is distributed alongside the project for installation via the user’s existing skills registry workflow; publishing to a public registry is optional and not required for v1 success.
- Offline verification of network behavior (mocked remote responses) is acceptable for automated tests; live Manager access is only required for manual acceptance of the user scenarios.

## Out of Scope (v1)

- Any create, update, post, or delete operation
- Opt-in write mode (even behind a flag)
- Lookups that require a pre-existing saved-report GUID
- Multi-instance configuration or switching
- Multi-business selection/routing when a host’s API root does not uniquely identify one set of books
- Auto-generating tools for every Manager API path

---
description: "Task list for Manager.io read-only MCP server"
---

# Tasks: Manager.io Read-Only MCP Server

**Input**: Design documents from `/specs/001-manager-readonly-mcp/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required alongside each unit (user request + constitution). Network I/O
via respx only — no live Manager in CI.

**Organization**: Scaffold → foundational units (client/resources/server) with
co-located tests → user stories → polish (skill already US5; README/CI polish).

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Parallelizable (different files, no incomplete blockers)
- **[USn]**: User story label (story phases only)

## Path Conventions

- Package: `src/manager_mcp/`
- Tests: `tests/`
- Skill: `skills/manager-accounting/`

---

## Phase 1: Setup (Project Scaffold)

**Purpose**: Installable package skeleton so later units have a home

- [x] T001 Create package layout `src/manager_mcp/__init__.py`, `src/manager_mcp/spec/`, and `tests/`
- [x] T002 [P] Add `pyproject.toml` (hatchling, Python >=3.10, deps fastmcp>=2/httpx, optional `[dev]` pytest/pytest-asyncio/respx/ruff, console script `manager-mcp`)
- [x] T003 [P] Add MIT `LICENSE`
- [x] T004 [P] Add `.env.example` documenting `MANAGER_API_URL`, `MANAGER_API_KEY`, and write-flag hard-stop names (`MANAGER_MCP_ALLOW_WRITES`, `ALLOW_WRITES`, `MANAGER_MCP_WRITES`)
- [x] T005 [P] Add `.gitignore` for Python/venv/`.env`/caches
- [x] T006 [P] Add `src/manager_mcp/spec/README.md` explaining vendored OpenAPI is provenance-only (runtime reads live); leave placeholder for `api2.json` or empty note until vendored

**Checkpoint**: `pip install -e ".[dev]"` succeeds (even with stub modules)

---

## Phase 2: Foundational (Client, Resources, Server Guard)

**Purpose**: Shared infrastructure — blocks all user stories

**⚠️ CRITICAL**: No user-story tool work until this phase completes

### Client (GET-only + config + param filter)

- [x] T007 Implement async GET-only httpx client in `src/manager_mcp/client.py` (`MANAGER_API_URL`/`MANAGER_API_KEY`, `X-API-KEY`, query allowlist `term|sortBy|sortByDesc|skip|pageSize|fields` + report date keys when configured; clear error if env missing; no post/put/patch/delete methods)
- [x] T008 [P] Add `tests/test_client.py` with respx: missing config error, param cleaning (drops unknown keys), `X-API-KEY` header asserted, HTTP error propagation, empty-body handling

### Resource allowlist

- [x] T009 Implement allowlist + `resolve()` in `src/manager_mcp/resources.py` (six collections + seven report shortcuts; collection form path `{path}-form/{key}`; paths from plan/research / OpenAPI)
- [x] T010 [P] Add `tests/test_resources.py`: resolve hits for all allowlisted names, miss returns `None`, form-path helper for collections

### Write-guard + FastMCP shell

- [x] T011 Implement write-flag hard-fail + lazy client + FastMCP app shell in `src/manager_mcp/server.py` (FR-001 MCP server via console script; write env `1|true|yes|on` or any other non-empty → raise per plan; import needs no config; entrypoint for `manager-mcp`)
- [x] T012 [P] Add `tests/test_readonly_guard.py` (FR-004/FR-005): each write-related env name with truthy/non-empty → hard-fail; unset → guard allows init; registered tool names contain no create/update/delete verbs (even if only stub tools yet)

**Checkpoint**: Foundation ready — story tools can land on the shell

---

## Phase 3: User Story 1 — Outstanding Customer Balances (Priority: P1) 🎯 MVP

**Goal**: Agent can answer who owes money via `aged_receivables` (and customers collection when needed)

**Independent Test**: Mocked `aged_receivables` returns receivables data; no mutating tools; optional `list_records` on `customers`

### Tests

- [x] T013 [P] [US1] Add failing/respx tests for `aged_receivables` tool in `tests/test_server_tools.py` (success path + auth error; period unsupported notice if no date params)

### Implementation

- [x] T014 [US1] Register `aged_receivables` tool in `src/manager_mcp/server.py` (GET via client + resources resolve; period params only if descriptor `date_params` set)
- [x] T015 [P] [US1] Ensure `customers` collection is resolvable for follow-up list/get (resources already; wire only if missing) in `src/manager_mcp/resources.py`

**Checkpoint**: US1 MVP — outstanding balances path works offline under respx

---

## Phase 4: User Story 2 — Core Financial Snapshots (Priority: P1)

**Goal**: Named report shortcuts for payables, bank balances, trial balance, P&L, balance sheet, tax summary

**Independent Test**: Each report tool returns mocked body; `bank_balances` distinct from `bank_accounts` collection job

### Tests

- [x] T016 [P] [US2] Extend `tests/test_server_tools.py` for `aged_payables`, `bank_balances`, `trial_balance`, `profit_and_loss`, `balance_sheet`, `tax_summary` (respx; period disclosure when unsupported)

### Implementation

- [x] T017 [US2] Register remaining report shortcut tools in `src/manager_mcp/server.py`
- [x] T018 [P] [US2] Document dual bank exposure in tool descriptions (`bank_balances` vs `bank_accounts`) in `src/manager_mcp/server.py` / `resources.py`

**Checkpoint**: US2 snapshots available

---

## Phase 5: User Story 3 — Discover What Can Be Read (Priority: P2)

**Goal**: `list_resources` enumerates capabilities + read-only boundary

**Independent Test**: Tool returns curated names/kinds and explicit no-write boundary

### Tests

- [x] T019 [P] [US3] Add tests for `list_resources` in `tests/test_server_tools.py` (lists collections+reports; `read_only` true; boundary text present)

### Implementation

- [x] T020 [US3] Implement `list_resources` in `src/manager_mcp/server.py` per `contracts/mcp-tools.md`

**Checkpoint**: Discovery complete

---

## Phase 6: User Story 4 — Search, Page, and Drill-In (Priority: P2)

**Goal**: `list_records` / `get_record` over six curated collections with truncation metadata

**Independent Test**: Page with `has_more`/`truncated`; search term; get by GUID; bank_accounts collection works alongside `bank_balances`

### Tests

- [x] T021 [P] [US4] Add respx tests for `list_records` in `tests/test_server_tools.py` (FR-011: assert `truncated` and/or `has_more`; term forwarded; unknown resource error)
- [x] T022 [P] [US4] Add respx tests for `get_record` (`-form/{key}` path, 404 handling) in `tests/test_server_tools.py`

### Implementation

- [x] T023 [US4] Implement `list_records` in `src/manager_mcp/server.py` (resource/term/sort/skip/page_size; FR-011 `truncated` and/or `has_more` in response)
- [x] T024 [US4] Implement `get_record` in `src/manager_mcp/server.py` using collection `-form/{key}` sibling endpoint

**Checkpoint**: US4 investigation path complete

---

## Phase 7: User Story 5 — Installable Agent Skill (Priority: P3)

**Goal**: Companion skill triggers on Manager.io / bookkeeping and states read-only

**Independent Test**: `SKILL.md` description contains triggers + read-only boundary

### Implementation

- [x] T025 [US5] Author `skills/manager-accounting/SKILL.md` (description triggers; read-only boundary; points at MCP tools / env config)

**Checkpoint**: Skill package ready for registry install workflows

---

## Phase 8: Polish & Cross-Cutting

**Purpose**: Docs, CI, provenance artifact

- [x] T026 [P] Write `README.md` (config env vars, MCP client config JSON example, tool table including intentional bank dual path, roadmap noting opt-in writes are v2+ and currently hard-fail, multi-business caveat)
- [x] T027 [P] Add `.github/workflows/ci.yml` matrix Python 3.10 and 3.12 running `ruff check` + `pytest`
- [x] T028 [P] Vendor or stub `src/manager_mcp/spec/api2.json` provenance snapshot + confirm `spec/README.md` accuracy
- [x] T029 Confirm console script `manager-mcp` launches FastMCP entry from `pyproject.toml` / `src/manager_mcp/server.py` (FR-001: MCP client can start the server)
- [x] T030 Run full offline suite (`ruff check` + `pytest`) and fix gaps vs `quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Start immediately; T002–T006 parallel after T001
- **Foundational (Phase 2)**: After Setup — **blocks** all stories
  - T007 → T008; T009 → T010; T011 needs T007+T009; T012 parallel after T011 stubs
- **US1 (Phase 3)**: After Phase 2 — MVP
- **US2 (Phase 4)**: After Phase 2 (can follow or parallel US1 once T011 shell exists; prefer after US1 for smaller PRs)
- **US3 (Phase 5)**: After Phase 2
- **US4 (Phase 6)**: After Phase 2 (needs collections in resources)
- **US5 (Phase 7)**: After contracts stable (can parallel late Phase 4–6)
- **Polish (Phase 8)**: After desired stories; CI can start once tests exist (T027 after T008+)

### User Story Independence

| Story | Can demo alone after Phase 2 + story tasks? |
|-------|-----------------------------------------------|
| US1 | Yes (`aged_receivables`) |
| US2 | Yes (report tools) |
| US3 | Yes (`list_resources`) |
| US4 | Yes (`list_records`/`get_record`) |
| US5 | Yes (skill file only) |

### Parallel Opportunities

```text
Phase 1: T002, T003, T004, T005, T006 in parallel after T001
Phase 2: T008 || T010 after their impls; client (T007-T008) || resources (T009-T010) before T011
Phase 4–6: US2/US3/US4 can proceed in parallel after Phase 2 if staffed
Phase 8: T026 || T027 || T028
```

---

## Parallel Example: Foundational

```bash
# After T001–T006:
Task: "Implement client.py"          # T007
Task: "Implement resources.py"       # T009  (parallel with T007)

# Then:
Task: "tests/test_client.py"         # T008
Task: "tests/test_resources.py"      # T010  (parallel)

# Then server shell + guard:
Task: "server.py write-guard shell"  # T011
Task: "tests/test_readonly_guard.py" # T012
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup
2. Phase 2 Foundational (client + resources + guard)
3. Phase 3 US1 (`aged_receivables`)
4. **STOP**: validate MVP with respx + optional live smoke
5. Continue US2–US5 + polish

### Incremental Delivery

1. Setup + Foundational → safe GET client & allowlist
2. US1 → receivables answers
3. US2 → full snapshot set
4. US3 → discovery
5. US4 → search/page/drill-in
6. US5 → skill
7. Polish → README + CI

---

## Notes

- Exact collection/report path strings: pull from Manager OpenAPI when vendoring `api2.json` (T028); until then use research.md placeholders consistently
- Do not add write tools or pass-through of non-allowlisted query keys
- Commit after each task or logical group (Conventional Commits)

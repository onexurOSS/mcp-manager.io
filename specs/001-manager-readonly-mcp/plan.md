# Implementation Plan: Manager.io Read-Only MCP Server

**Branch**: `001-manager-readonly-mcp` | **Date**: 2026-07-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-manager-readonly-mcp/spec.md`

## Summary

Ship a small Python MCP server (`manager-mcp`) that lets agents query a
self-hosted Manager.io instance **read-only**, plus a companion Agent Skill.
Architecture: GET-only `httpx` client, curated resource allowlist, FastMCP tools
(~10) for discovery, collection list/search/page, record fetch, and named report
shortcuts — never auto-generated from the ~650-path OpenAPI surface.

## Technical Context

**Language/Version**: Python 3.10 and 3.12 (CI matrix); package requires `>=3.10`

**Primary Dependencies**: FastMCP (>=2.0), httpx (async), hatchling (build)

**Storage**: N/A (no local persistence; live Manager instance is the data source)

**Testing**: pytest, pytest-asyncio, respx (mocked HTTP; no live Manager in CI)

**Target Platform**: Local/dev MCP host (stdio via console script `manager-mcp`);
Windows/macOS/Linux where Python and network to Manager are available

**Project Type**: Installable Python package (uvx/pip) + Agent Skill package

**Performance Goals**: Interactive agent latency — single-page GETs; no throughput
SLO beyond “one tool call ≈ one Manager GET”

**Constraints**: Strict read-only (no POST/PUT/PATCH/DELETE on client); curated
tools only; secrets via env; offline CI; MIT; Conventional Commits; empty
Manager `components.schemas` means no generated write schemas

**Scale/Scope**: One opaque `MANAGER_API_URL` + `MANAGER_API_KEY`; ~10 tools;
six curated collections + seven report shortcuts; companion `SKILL.md`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Pre-research | Post-design |
|------|--------------|-------------|
| Read-only default | PASS — GET-only client; no write methods; v1 harder than constitution (write flag = hard-fail, not opt-in) | PASS |
| Curated tools | PASS — hand-picked ~10 tools; no OpenAPI codegen | PASS |
| Secrets via env | PASS — `MANAGER_API_URL`, `MANAGER_API_KEY`; never log key | PASS |
| Offline tests (respx) | PASS — all HTTP via respx in CI | PASS |
| Engineering baseline | PASS — MIT, Conventional Commits, CI 3.10+3.12 ruff+pytest | PASS |
| No-slop docs | PASS — README/docstrings intent+tradeoffs; vendored spec README explains snapshot role | PASS |

**Write-scope policy (settled names)**: `MANAGER_MCP_WRITE_SCOPES` and
`MANAGER_MCP_DELETE_SCOPES` (comma-separated enumerated domains; no wildcards).
Delete never implied by write. Legacy `MANAGER_MCP_ALLOW_WRITES` /
`ALLOW_WRITES` / `MANAGER_MCP_WRITES` hard-fail if set. Client denylist is
absolute. Empty scopes → read-only tool set.

## Project Structure

### Documentation (this feature)

```text
specs/001-manager-readonly-mcp/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── mcp-tools.md
├── version-guard-report.md
└── tasks.md                 # /speckit-tasks — not created here
```

### Source Code (repository root)

```text
src/manager_mcp/
├── __init__.py
├── client.py                # async httpx GET-only wrapper
├── resources.py             # allowlist: collections + report shortcuts
├── server.py                # FastMCP tools + write-flag safety guard
└── spec/
    ├── README.md            # provenance: snapshot vs live
    └── api2.json            # vendored OpenAPI (provenance only)

skills/manager-accounting/
└── SKILL.md

tests/
├── test_client.py
├── test_resources.py
├── test_server_tools.py
└── test_readonly_guard.py

pyproject.toml               # hatchling; script manager-mcp
.env.example
README.md
LICENSE                      # MIT
.github/workflows/ci.yml     # 3.10, 3.12 — ruff + pytest
```

**Structure Decision**: Single installable package under `src/manager_mcp/` with
tests at repo root `tests/`, skill under `skills/manager-accounting/`. Matches
uvx/pip console-script distribution; no monorepo split.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Bank dual exposure (snapshot tool + collection) | Spec: two agent jobs (“balances?” vs “find account X”) | Collapsing to one tool forces awkward parameters and worse discovery |
| ~10 tools vs ultra-minimal 3 | Spec requires named financial snapshots + list/get/discover | One mega-tool increases agent errors and obscures read-only boundary |

## Phase Notes

- Phase 0 research and Phase 1 design artifacts live alongside this plan.
- Multi-business caveat: document in README that v1 assumes the opaque base URL
  uniquely identifies one set of books; validate before claiming multi-business
  support (already out of scope).

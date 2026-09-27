# Changelog

All notable changes to this project are documented in this file.

## [1.0.0] - 2026-09-26

**onexurOSS initial release.** This is the first release under the onexurOSS identity
(onexurOSS Manager MCP), based on the upstream manager-mcp codebase at 0.2.6. Upstream
authorship and the historical entries below are unchanged. The PyPI distribution is
named `mcp-manager.io`; the module (`manager_mcp`) and commands (`manager-mcp`,
`manager-mcp-dev`) are unchanged for compatibility.

### Added

- Read-only reporting layer (`reporting.py`, see `docs/reporting.md`):
  - `manager_report_catalogue`: what each Manager report is, how the API exposes it, and which tool covers it.
  - `get_report_definition`: GET of a stored report definition by key (settings only, no calculated rows).
  - `ledger_transactions`: historical ledger access from Manager's account-level `/transactions` feed,
    complete fetch, client side date and account filters, paging with `next_skip`.
  - `reconstructed_trial_balance`, `reconstructed_profit_and_loss`,
    `reconstructed_aged_receivables`, `reconstructed_aged_payables`.
- Reporting semantics: legacy report tools return a `semantics` block (data class, historical,
  authoritative). Reconstructed reports always return `authoritative: false` and
  `official_manager_report: false`, with source, method, as-at date and ledger completeness.
- Generated per-endpoint capability matrix under `src/manager_mcp/spec/` (built from Manager
  26.8.4.3664; Manager's own API description is not redistributed).
- `scripts/verify_reporting_controls.py` (live, GET-only validation) and
  `scripts/build_capability_matrix.py`.
- Read-only reconciliation/diagnostic tools: `find_records`, `find_broken_invoice_references`,
  `find_unallocated_transactions`, `find_duplicate_transactions`, `verify_invoice_balance`,
  `account_ledger`, `bank_activity`, `find_suspense_candidate_accounts`,
  `general_ledger_summary`, `reconcile_period`. All classify strictly from Manager's
  structured fields; no free-text matching, no external system consulted.
- Corrective tools: `propose_correction`/`apply_correction` (dry-run layer in front of
  every existing create/update), `reallocate_payment_line`/`reallocate_receipt_line`,
  missing-invoice reconstruction (`propose_purchase_invoice_reconstruction` /
  `apply_purchase_invoice_reconstruction` and sales equivalents), and
  `snapshot_and_void` (audit-logged, reference-checked void).
- Local JSONL audit log (`MANAGER_MCP_AUDIT_LOG_PATH`) recording before/after state for
  every corrective write, correlated across a propose/apply pair.
- Regression test suite for the missing-invoice reconstruction workflow, covering the
  general pattern behind a supplier-payment case referencing a non-existent purchase
  invoice (two partial payments summing to the invoice total).
- Read-only `fixed_assets` and `tax_codes` collections, `create_fixed_asset`, an expanded
  `get_server_info`, and an opt-in development supervisor (`MANAGER_MCP_DEV_SUPERVISOR=1`).

### Changed

- `aged_receivables`, `aged_payables`, `bank_balances` and `tax_summary` now reject date and as-at
  parameters instead of returning current data with a notice. Descriptions of these and of
  `trial_balance`, `profit_and_loss` and `balance_sheet` no longer imply official reports.
- Branding, repository URLs and registry identity (`io.github.onexurOSS/manager-mcp`) now use onexurOSS.
  The PyPI distribution name is `mcp-manager.io`; install with `uvx --from mcp-manager.io manager-mcp`.
  The original MIT copyright notice is retained; onexurOSS is added.

### Limitations

- Manager's API returns no finished reports. The report view routes are application only, and
  this MCP does not call or imitate them. Official historical Aged Receivables, Aged Payables,
  Trial Balance, Balance Sheet and VAT returns are not available through the API.
- Report definitions can be read only with a known key; settings only, never calculated rows.
- `aged_receivables`, `aged_payables` and `bank_balances` are current-state only.
- Reconstructed reports are not Manager's official reports. Reconstructed ageing uses transaction
  dates and oldest-first allocation, whereas Manager's report uses due dates and actual
  allocations, so bucket splits can differ. Party totals are checked only against the control
  account total.
- Manager ignores date parameters on `/transactions`; dates are filtered by the MCP.

### Validation

- Live, GET-only run (18 requests, none other than GET): reconstructed balances for Accounts receivable, Accounts payable and Cash at a past date equalled the
  official Manager report figures, the trial balance balanced, and the reconstructed result matched the official one.
  These matches do not make the reconstructions authoritative.
- The reporting expansion performs no accounting writes. Existing write tools are unchanged
  and remain gated by `MANAGER_MCP_WRITE_SCOPES`.

## [0.2.6] - 2026-08-03

### Fixed

- Claude Desktop runtime: use `uv tool run manager-mcp` in mcpb mcp_config.
  Claude maps `server.type: uv` to `uv.exe` and was running `uv manager-mcp`
  instead of `uvx manager-mcp`.

## [0.2.5] - 2026-08-03

### Fixed

- Claude Desktop extension install on Windows: drop manager-mcp from mcpb
  pyproject dependencies (install ran uv sync and failed on cffi without win
  wheels). Runtime still uses uvx; add mcpb/.mcpbignore for .venv and uv.lock.

## [0.2.4] - 2026-08-03

### Fixed

- Claude Desktop extension: migrate MCPB to manifest 0.4 / `uv` server type and drop
  system Python runtime check (uses `uvx`; only requires `uv` on PATH)

## [0.2.3] - 2026-08-03

### Added

- Claude Desktop extension pack (`mcpb.mcpb`) for one-click install via Claude Desktop
- Serena project config (`.serena/`) for symbol-aware agent editing

### Changed

- Refactor: reduce complexity from ponytail audit (-56 lines)

### Fixed

- CI: resolve ruff lint failures from refactor commit (F401, I001, E501)

## [0.2.0] - 2026-07-29

### Added

- 13 intent-shaped task tools replacing generated CRUD as the recommended write path
- Customer deposit workflow (`record_customer_deposit`, `issue_deposit_invoice`, `apply_deposit_to_invoice`)
- `PreconditionResult` pattern for structured setup guidance when instance preconditions fail
- `raw` escape-hatch scope restoring the full CRUD set for advanced use
- `server.json` for MCP Registry discoverability
- Explicit `[tool.hatch.build.targets.sdist]` include list
- Live sandbox integration test suite (`pytest -m integration`, `TEST_MANAGER_API_*` env vars)

### Changed

- `pyproject.toml` description reflects read-first with opt-in scoped writes
- `skills/manager-accounting/SKILL.md`: task tool guidance, deposit is-not-revenue statement
- `README.md`: task tool table, deposit docs, recommended scope config, migration notice

### Deprecated

- All per-resource CRUD `create_*` / `update_*` / `delete_*` tools except `create_customer` and `create_supplier`. Removal target: **0.3.0**.

## [0.1.1] - 2026-07-28

### Fixed

- Banking write hardening, persistence warnings, and scope registration fixes

## [0.1.0] - 2026-07-28

### Added

- Initial release: 10 read tools, 72 scoped CRUD write tools, denylist, agent skill

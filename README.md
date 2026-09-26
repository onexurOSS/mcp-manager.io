<p align="center">
  <a href="https://www.manager.io/">
    <img src="docs/manager-icon.svg" alt="Manager.io" width="72" height="72">
  </a>
</p>

# onexurOSS Manager MCP

First release under the onexurOSS identity (v1.0.0), based on the upstream [manager-mcp](https://github.com/flumpiey/manager-mcp) project (MIT). The PyPI package is `mcp-manager.io`; the module and commands remain `manager_mcp` / `manager-mcp`.

<!-- mcp-name: io.github.onexurOSS/manager-mcp -->

**MCP server for self-hosted [Manager.io](https://www.manager.io/): ask your AI about invoices, balances, and books.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-stdio-green.svg)](https://modelcontextprotocol.io/)
[![CI](https://github.com/onexurOSS/mcp-manager.io/actions/workflows/ci.yml/badge.svg)](https://github.com/onexurOSS/mcp-manager.io/actions/workflows/ci.yml)

## Project status

- Version **1.0.0**, the first onexurOSS release. It is based on upstream manager-mcp 0.2.6 and adds the features listed under [What is new](#what-is-new-since-upstream-026).
- **Not yet published.** At the time of writing this repository has no GitHub Release, no PyPI package (`mcp-manager.io`) and no prebuilt `.mcpb` bundle. The install commands in this README use `mcp-manager.io` and will work once it is published. Until then, install from GitHub:

  ```bash
  uvx --from git+https://github.com/onexurOSS/mcp-manager.io manager-mcp
  ```

  In a client config, replace the args `["--from", "mcp-manager.io", "manager-mcp"]` with `["--from", "git+https://github.com/onexurOSS/mcp-manager.io", "manager-mcp"]`.
- The releases 0.1.x to 0.2.6 in [CHANGELOG.md](CHANGELOG.md) belong to the upstream project. They and their artifacts exist upstream, not in this repository.

### What is new since upstream 0.2.6

- **Read-only reporting layer:** report catalogue, report definitions (settings only), account-level historical ledger access, and clearly labelled reconstructed trial balance, profit and loss, and aged receivables/payables. See [Reporting and authority](#reporting-and-authority) and [docs/reporting.md](docs/reporting.md).
- **Honest date handling:** tools that only return current data now reject `from_date`/`to_date` instead of returning today's data, and legacy report tools carry a `semantics` block that says what they really are.
- **Reconciliation and diagnostics:** ten read-only tools (see [Reconciliation & corrections](#reconciliation--corrections)).
- **Corrective tools with an audit log:** dry-run then apply, payment/receipt reallocation, missing-invoice reconstruction, audited void.
- **Fixed assets:** read collections `fixed_assets` and `tax_codes`, `get_fixed_asset`, plus `create_fixed_asset` and `update_fixed_asset` under the `ledger` scope.
- **`get_server_info`:** process id, start time, git state and registered tools, to prove which process you are talking to.
- **Dev supervisor (opt-in):** automatic restart on source changes, see [Development](#development).
- **Live API specification archive** and a generated per-endpoint capability matrix under `src/manager_mcp/spec/`.

## What is Manager.io?

[Manager.io](https://www.manager.io/) is free, self-hosted accounting software for Windows, macOS, and Linux (also available as [Cloud Edition](https://www.manager.io/cloud-edition)). It covers sales, purchases, banking, payroll, and the full ledger, with an HTTP API (`/api2`) for automation.

This project wires that API into the [Model Context Protocol](https://modelcontextprotocol.io/) so Cursor, Claude, VS Code Copilot, and other MCP hosts can query your live books in natural language.

Useful Manager.io links:

- [Download](https://www.manager.io/download)
- [Guides](https://www.manager.io/guides)
- [Forum](https://forum.manager.io)
- [Releases](https://www.manager.io/releases)

## What this server does

Default is **read-only**. With no write scopes it registers **29 read tools**:

- **Discovery and records (5):** `list_resources`, `list_records`, `get_record`, `get_fixed_asset`, `get_server_info`. `list_records`/`get_record` cover 27 collections.
- **Current-state and raw-feed shortcuts (7):** `aged_receivables` and `aged_payables` (current balances only, not aged, no dates), `bank_balances` (current), `trial_balance`, `profit_and_loss`, `balance_sheet` (raw transaction rows, not reports), `tax_summary` (raw rows, no dates).
- **Diagnostics (10):** duplicate detection, missing-invoice detection, invoice-balance verification, per-account ledger, bank activity and a period reconciliation report.
- **Reporting layer (7):** `manager_report_catalogue`, `get_report_definition`, `ledger_transactions`, `reconstructed_trial_balance`, `reconstructed_profit_and_loss`, `reconstructed_aged_receivables`, `reconstructed_aged_payables`. Reconstructions are always `authoritative: false`.

Opt-in, by write scope:

- **Task tools:** intent-shaped writes such as `record_customer_payment`, `issue_sales_invoice`, `record_customer_deposit`.
- **Corrective tools:** `propose_correction`/`apply_correction` (dry run before write), `reallocate_payment_line`/`reallocate_receipt_line`, and the missing-invoice reconstruction workflow. See [Reconciliation & corrections](#reconciliation--corrections).
- **Fixed-asset tools:** `create_fixed_asset` and `update_fixed_asset` (`ledger` scope).
- **Deprecated CRUD tools:** per-resource `create_*` / `update_*` / `delete_*` still register under scopes in 1.0.0. Upstream planned to remove them in its 0.3.0; that removal has not been done here. Prefer task tools.
- **`raw`:** restores the full CRUD set for advanced use. It does not lift the denylist.
- **Hard denylist:** access tokens, chart of accounts forms, tax/currency, email templates and similar high-risk paths are blocked for every write and delete, including under `raw`. Every non-GET request is checked against the denylist before any scope check.

Tool counts depend on scopes. No scopes: 29. `parties,sales,purchases,banking,ledger`: 78. All nine domain scopes as write and delete scopes: 125 (counted from the registered tools).

Transport is **stdio**. No HTTP server. No global install is required if you use [`uv`](https://docs.astral.sh/uv/) / `uvx`.

## Branding / icons

- **stdio hosts (Cursor, Claude Desktop via `mcp.json`):** the server advertises Manager branding in MCP `serverInfo.icons` (embedded PNG data URI, plus a GitHub raw HTTPS fallback).
- **Cursor plugin:** [`.cursor-plugin/plugin.json`](.cursor-plugin/plugin.json) uses [`docs/manager-icon.svg`](docs/manager-icon.svg).
- **Claude Desktop Extension:** pack [`mcpb/`](mcpb/) (includes `icon.png`). See Installation → Claude Desktop below.
- **Claude.ai remote connectors:** Claude.ai ignores `serverInfo.icons` and uses the **root-domain favicon** of the connector URL. If you host a remote MCP later, serve [`docs/favicon.ico`](docs/favicon.ico) at the registrable domain root (e.g. `https://acme.com/favicon.ico` for `https://mcp.acme.com/...`).

## Requirements

- Python ≥ 3.10 (pulled in automatically by `uvx`)
- [uv](https://docs.astral.sh/uv/) (provides `uvx`)
- A reachable Manager.io API: `MANAGER_API_URL` + `MANAGER_API_KEY`

### Access token

1. In Manager, open **Settings → Access Tokens**.
2. Create a token and copy the value into `MANAGER_API_KEY`.
3. Set `MANAGER_API_URL` to your API base (desktop often `http://127.0.0.1:55667/api2`).

`manager-mcp` sends the token as the `X-API-KEY` header. Full walkthrough: [Access Tokens](https://www.manager.io/guides/access-tokens).

## How the connection works

The MCP server connects directly to the Manager HTTP API. It does not connect to
Manager's database and it does not expose an HTTP server of its own.

```text
MCP host (stdio)
    -> manager-mcp
    -> GET http(s)://<manager-host>/api2/...
       X-API-KEY: <Manager access token>
    -> Manager business data
```

Configure these environment variables in the MCP host configuration:

- `MANAGER_API_URL`: the Manager API base URL, normally ending in `/api2`.
- `MANAGER_API_KEY`: an Access Token created in Manager. The server sends it as
  the `X-API-KEY` request header and never logs it.

For a local Manager server, the URL may look like:

```text
http://127.0.0.1:55667/api2
```

For a remote or self-hosted Manager server, replace the host and port while
keeping the `/api2` suffix when that server requires it. The MCP host starts
`manager-mcp` as a stdio child process; the API URL and key are passed to that
process through its `env` block. The MCP then makes authenticated API requests
on demand when a tool is called.

### Verify the connection without writing

With the same variables available in your shell, use a read-only API request:

```bash
curl -sS \
  -H "X-API-KEY: $MANAGER_API_KEY" \
  "$MANAGER_API_URL/chart-of-accounts?pageSize=1"
```

A successful response confirms that the URL is reachable, the token is valid,
and the token can read the selected Manager business. Keep
`MANAGER_MCP_WRITE_SCOPES` and `MANAGER_MCP_DELETE_SCOPES` unset for a
read-only MCP connection.

## Quick start

Until the package is published (see [Project status](#project-status)), run it from GitHub with [`uvx`](https://docs.astral.sh/uv/guides/tools/):

```bash
uvx --from git+https://github.com/onexurOSS/mcp-manager.io manager-mcp
```

Once `mcp-manager.io` is on PyPI the same command becomes:

```bash
uvx --from mcp-manager.io manager-mcp
```

Paste a client config below, set `MANAGER_API_URL` / `MANAGER_API_KEY`, restart the host, then ask: *“Who owes me money?”* or *“Show bank balances.”*

From a local clone (development): `uv run --directory /path/to/manager-mcp manager-mcp`.

## Installation

Configs below use the package name `mcp-manager.io`, which is **not yet published** to PyPI. Until it is, use the GitHub source in the args instead: `"--from", "git+https://github.com/onexurOSS/mcp-manager.io", "manager-mcp"`. The command they run is `manager-mcp`. Leave write-scope env vars unset for read-only.

<details>
<summary><strong>Cursor</strong></summary>

**Plugin (Configure UI for URL, key, and scopes):** this repo is a Cursor plugin via [`.cursor-plugin/plugin.json`](.cursor-plugin/plugin.json) + root [`mcp.json`](mcp.json).

1. Symlink or copy the clone to `~/.cursor/plugins/local/manager-mcp` (Windows: `%USERPROFILE%\.cursor\plugins\local\manager-mcp`).
2. Reload the window.
3. Open **Plugins → Configure** on `manager-mcp`. Set **Manager API URL** and **Manager API key**. Leave **Write scopes** / **Delete scopes** empty for read-only, or paste a CSV such as `quotes` or `quotes,orders`.
4. Confirm the `manager` MCP server is enabled under Customize / MCP.

Marketplace listing is a separate submit at [cursor.com/marketplace/publish](https://cursor.com/marketplace/publish).

**Manual `mcp.json`:** project [`.cursor/mcp.json`](.cursor/mcp.json) or user-wide `~/.cursor/mcp.json`.

From PyPI:

```json
{
  "mcpServers": {
    "manager": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "mcp-manager.io", "manager-mcp"],
      "env": {
        "MANAGER_API_URL": "http://127.0.0.1:55667/api2",
        "MANAGER_API_KEY": "your-token"
      }
    }
  }
}
```

Local editable (dev):

```json
{
  "mcpServers": {
    "manager": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/path/to/manager-mcp", "manager-mcp"],
      "env": {
        "MANAGER_API_URL": "http://127.0.0.1:55667/api2",
        "MANAGER_API_KEY": "your-token"
      }
    }
  }
}
```

Optional scoped writes in the `env` block:

```json
"MANAGER_MCP_WRITE_SCOPES": "quotes",
"MANAGER_MCP_DELETE_SCOPES": "quotes"
```

Restart Cursor after saving. Confirm `manager` under MCP settings.

</details>

<details>
<summary><strong>Claude Desktop</strong></summary>

**Desktop Extension (`.mcpb`):** not yet published. There is no GitHub Release or prebuilt `mcpb.mcpb` in this repository. Build it yourself as shown below; once a release exists the bundle will be attached to it.

1. Open Claude Desktop → **Settings → Extensions**.
2. Open **Advanced settings** → **Install Extension…**
3. Select the `mcpb.mcpb` you built. Review permissions, enter **Manager API URL** and **Manager API key**, then click **Install**.
4. Leave **Write scopes** and **Delete scopes** empty for read-only.
5. Restart Claude Desktop if tools do not appear.

Build your own bundle from a clone:

```bash
npx @anthropic-ai/mcpb pack mcpb
```

On Windows, double-click often does nothing and dragging the file into chat attaches it to the conversation instead of installing it. Use **Install Extension…** in Settings.

**Manual `mcp.json` config:** edit the Claude Desktop config, then restart the app.

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "manager": {
      "command": "uvx",
      "args": ["--from", "mcp-manager.io", "manager-mcp"],
      "env": {
        "MANAGER_API_URL": "http://127.0.0.1:55667/api2",
        "MANAGER_API_KEY": "your-token"
      }
    }
  }
}
```

Local clone:

```json
{
  "mcpServers": {
    "manager": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/manager-mcp", "manager-mcp"],
      "env": {
        "MANAGER_API_URL": "http://127.0.0.1:55667/api2",
        "MANAGER_API_KEY": "your-token"
      }
    }
  }
}
```

</details>

<details>
<summary><strong>Claude Code</strong></summary>

Add via CLI:

```bash
claude mcp add manager --env MANAGER_API_URL=http://127.0.0.1:55667/api2 --env MANAGER_API_KEY=your-token -- uvx --from mcp-manager.io manager-mcp
```

Or edit `~/.claude.json` / project MCP config:

```json
{
  "mcpServers": {
    "manager": {
      "command": "uvx",
      "args": ["--from", "mcp-manager.io", "manager-mcp"],
      "env": {
        "MANAGER_API_URL": "http://127.0.0.1:55667/api2",
        "MANAGER_API_KEY": "your-token"
      }
    }
  }
}
```

</details>

<details>
<summary><strong>VS Code / GitHub Copilot</strong></summary>

Create [`.vscode/mcp.json`](.vscode/mcp.json) in the project root:

```json
{
  "servers": {
    "manager": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "mcp-manager.io", "manager-mcp"],
      "env": {
        "MANAGER_API_URL": "http://127.0.0.1:55667/api2",
        "MANAGER_API_KEY": "your-token"
      }
    }
  }
}
```

Local editable:

```json
{
  "servers": {
    "manager": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/path/to/manager-mcp", "manager-mcp"],
      "env": {
        "MANAGER_API_URL": "http://127.0.0.1:55667/api2",
        "MANAGER_API_KEY": "your-token"
      }
    }
  }
}
```

Reload the window. Open Copilot Chat and confirm the `manager` tools are available.

</details>

<details>
<summary><strong>Windsurf</strong></summary>

Edit `~/.codeium/windsurf/mcp_config.json` (macOS/Linux) or the Windsurf MCP settings UI:

```json
{
  "mcpServers": {
    "manager": {
      "command": "uvx",
      "args": ["--from", "mcp-manager.io", "manager-mcp"],
      "env": {
        "MANAGER_API_URL": "http://127.0.0.1:55667/api2",
        "MANAGER_API_KEY": "your-token"
      }
    }
  }
}
```

Restart Windsurf after saving.

</details>

<details>
<summary><strong>Zed</strong></summary>

Add under `context_servers` in Zed `settings.json` (Agent Panel → settings also works):

```json
{
  "context_servers": {
    "manager": {
      "command": "uvx",
      "args": ["--from", "mcp-manager.io", "manager-mcp"],
      "env": {
        "MANAGER_API_URL": "http://127.0.0.1:55667/api2",
        "MANAGER_API_KEY": "your-token"
      }
    }
  }
}
```

</details>

<details>
<summary><strong>Cline</strong></summary>

Edit the Cline MCP settings file (`cline_mcp_settings.json` via the Cline MCP UI):

```json
{
  "mcpServers": {
    "manager": {
      "command": "uvx",
      "args": ["--from", "mcp-manager.io", "manager-mcp"],
      "env": {
        "MANAGER_API_URL": "http://127.0.0.1:55667/api2",
        "MANAGER_API_KEY": "your-token"
      }
    }
  }
}
```

</details>

<details>
<summary><strong>Continue</strong></summary>

In `.continue/config.yaml`:

```yaml
mcpServers:
  - name: manager
    command: uvx
    args:
      - --from
      - mcp-manager.io
      - manager-mcp
    env:
      MANAGER_API_URL: http://127.0.0.1:55667/api2
      MANAGER_API_KEY: your-token
```

</details>

<details>
<summary><strong>Generic / any stdio MCP host</strong></summary>

Any host that can spawn a stdio MCP server:

| Field | Value |
|-------|-------|
| Command | `uvx` |
| Args | `--from mcp-manager.io manager-mcp` |
| Env | `MANAGER_API_URL`, `MANAGER_API_KEY` (+ optional write scopes) |

```bash
uvx --from mcp-manager.io manager-mcp
```

Dev from a clone: `uv run --directory /path/to/manager-mcp manager-mcp`.

`npx` only runs npm packages. This is a Python package; use `uvx`.

</details>

## Environment

| Variable | Required | Notes |
|----------|----------|-------|
| `MANAGER_API_URL` | yes | Opaque base URL (include `/api2` when needed) |
| `MANAGER_API_KEY` | yes | Sent as `X-API-KEY`; never logged |
| `MANAGER_MCP_WRITE_SCOPES` | no | Comma-separated domains for create/update. Empty = no writes. |
| `MANAGER_MCP_DELETE_SCOPES` | no | Comma-separated domains for delete only. Never implied by WRITE_SCOPES. |
| `MANAGER_MCP_DEV_SUPERVISOR` | no | `1` runs the development supervisor (auto restart on source changes). Leave unset in production. |
| `MANAGER_MCP_AUDIT_LOG_PATH` | no | Local JSONL audit trail for corrective tools (before/after state). Default `~/.manager_mcp/audit_log.jsonl`. |

Valid scopes: `quotes`, `orders`, `parties`, `items`, `sales`, `purchases`, `banking`, `payroll`, `ledger`, `raw`. No wildcards (`*`, `all`).

**Recommended** (covers most bookkeeping without registering 125 tools):

```json
"MANAGER_MCP_WRITE_SCOPES": "banking,sales,parties",
"MANAGER_MCP_DELETE_SCOPES": "sales,banking"
```

Default with no scopes: **29 tools** (all read-only). `parties,sales,purchases,banking,ledger`: **78**. All nine domain scopes as write and delete scopes: **125**. Use `raw` only when you need the full CRUD escape hatch; the denylist still applies.

Legacy `MANAGER_MCP_ALLOW_WRITES` / `ALLOW_WRITES` / `MANAGER_MCP_WRITES` hard-fail if set. Use the scoped vars instead.

See [`.env.example`](.env.example). Prefer a secret manager for the API key in production configs.

## Write scopes and task tools

When a scope is listed in `MANAGER_MCP_WRITE_SCOPES`, the server registers **task tools** for that domain plus deprecated CRUD twins. `MANAGER_MCP_DELETE_SCOPES` enables `void_document` and `delete_*` per domain.

### Task tools (preferred)

| Tool | Scopes | Purpose |
|------|--------|---------|
| `create_customer`, `create_supplier` | parties | Single-resource party setup |
| `issue_sales_invoice` | sales | Issue invoice with inline lines |
| `issue_purchase_invoice` | purchases | Issue purchase invoice |
| `issue_quote` | quotes | Issue sales or purchase quote |
| `convert_quote_to_invoice` | quotes + sales | Convert quote to invoice |
| `record_customer_payment` | banking | Receipt + invoice allocation |
| `record_supplier_payment` | banking | Payment + invoice allocation |
| `record_expense` | payroll and/or purchases | Expense claim or purchase invoice |
| `transfer_between_accounts` | banking | Inter-account transfer |
| `post_journal_entry` | ledger | Generic journal entry |
| `void_document` | matching delete scope | Void by resource name + key |
| `record_customer_deposit` | banking | Deposit before invoice exists |
| `issue_deposit_invoice` | quotes | Deposit document (quote) |
| `apply_deposit_to_invoice` | ledger | Apply deposit via journal |
| `create_fixed_asset` | ledger | Create a fixed asset record (acquisition cost comes from purchase invoice lines tagged with the asset; cost and read-only fields are rejected) |
| `update_fixed_asset` | ledger | Update fixed asset metadata only |

Bodies for composite tools use Manager-native JSON where noted. Clone `get_record` templates; do not invent field names.

### Deprecated CRUD (deprecated since 0.2.0, still present)

Per-resource `create_*` / `update_*` / `delete_*` still register when their domain scope is enabled. Descriptions are prefixed `[DEPRECATED in 0.2.0; use task tools]` except `create_customer` / `create_supplier`. Set `raw` in `MANAGER_MCP_WRITE_SCOPES` to register CRUD without deprecation prefixes.

| Scope | Resources (CRUD when enabled) |
|-------|-------------------------------|
| `quotes` | sales_quotes, purchase_quotes |
| `orders` | sales_orders, purchase_orders |
| `parties` | customers, suppliers |
| `items` | inventory_items, non_inventory_items |
| `sales` | sales_invoices, credit_notes, delivery_notes |
| `purchases` | purchase_invoices, debit_notes, goods_receipts |
| `banking` | receipts, payments, inter_account_transfers, bank_accounts |
| `payroll` | employees, payslips, expense_claims |
| `ledger` | journal_entries, depreciation_entries, amortization_entries |

Example with recommended scopes only:

```json
"MANAGER_MCP_WRITE_SCOPES": "banking,sales,parties",
"MANAGER_MCP_DELETE_SCOPES": "sales"
```

**Denylist (always blocked):** access-token forms, chart-of-accounts / `*-account-form` (except bank-or-cash), bank reconciliation, customer portal, starting balances, tax codes, exchange rates, currencies, custom fields/buttons, themes, email templates/settings.

## Customer deposit workflow

A **deposit is not revenue**. Money received before delivery must not be booked to an income account. Confirm tax/VAT treatment with your accountant.

1. Ensure a **Customer deposits** bank/cash account exists in Manager (Settings → Bank and Cash Accounts).
2. `record_customer_deposit` - posts cash to that account. If the account is missing, the tool returns `precondition_failed` with exact setup steps (Option A: guide only, no auto-create).
3. `issue_deposit_invoice` (optional) - quote styled as a deposit document for the customer.
4. `issue_sales_invoice` when the real invoice is raised.
5. `apply_deposit_to_invoice` - journal entry moving deposit balance to the invoice (clone an existing journal via `get_record`).

Required scopes: `banking`, `quotes` (deposit doc), `ledger` (apply), `sales` (final invoice via MCP).

## Reconciliation & corrections

**Manager is the sole source of truth.** Every tool below classifies and matches using Manager's own structured fields (transaction type, Manager Key, structured `Account`, structured invoice allocation, structured `Customer`/`Supplier`, date, amount, `Lines`), never free-text `Description`/payee text, and never amount-only or name-similarity matching. No external system is consulted by any of these tools.

### Read-only diagnostics (always available; no scope required)

| Tool | Purpose |
|------|---------|
| `find_records` | Exact-match structured filter over a collection (Manager's own query API has no field-value filter) |
| `find_broken_invoice_references` | Payments/receipts whose AR/AP invoice-Key line reference doesn't resolve to any current invoice (the general "missing invoice" pattern) |
| `find_unallocated_transactions` | Receipts/payments with no AR/AP invoice allocation on any line |
| `find_duplicate_transactions` | Exact-match duplicate detector (party + Reference + Date + total + line signature must ALL match; partial matches are `unresolved`, never a duplicate) |
| `verify_invoice_balance` | Self-computed invoice total vs. allocated receipts/payments, with contributing Keys |
| `account_ledger` | Every structured line across all transaction types for one chart-of-accounts account (Manager has no native GL-by-account endpoint) |
| `bank_activity` | Money-in/out for one bank/cash account, assembled from receipts/payments/transfers |
| `find_suspense_candidate_accounts` | Chart-of-accounts rows whose structured `Name` matches placeholder-account naming (candidates for human confirmation only) |
| `general_ledger_summary` | Single-pass debit/credit balance check grouped by account |
| `reconcile_period` | Composes all of the above into one PERIOD reconciliation report; every exception carries exact Manager Keys. P&L/Balance Sheet/Tax Summary sections surface the raw Manager transactions feed with an explicit notice, since Manager API2 does not expose computed report totals in this version, never a fabricated total. |

### Corrective tools (opt-in; reuse the write/delete scopes above)

| Tool | Scopes | Purpose |
|------|--------|---------|
| `propose_correction` | any write scope | Dry run: validate and preview a create/update without writing; returns a `proposal_token` |
| `apply_correction` | matching resource scope | Commit a proposal; refuses if `proposal_token` doesn't match the exact `(resource, key, fields)` |
| `reallocate_payment_line` | banking | Repoint one payment line's `AccountsPayablePurchaseInvoice` to a different, verified-to-exist purchase invoice |
| `reallocate_receipt_line` | banking | Repoint one receipt line's `AccountsReceivableSalesInvoice` to a different, verified-to-exist sales invoice |
| `propose_purchase_invoice_reconstruction` / `apply_purchase_invoice_reconstruction` | purchases + banking | Missing-invoice workflow: investigate payments citing a non-existent purchase invoice, refuse to guess the per-line breakdown, create the invoice once you supply real line evidence, then reallocate the citing payments and verify the balance |
| `propose_sales_invoice_reconstruction` / `apply_sales_invoice_reconstruction` | sales + banking | Same workflow for receipts citing a missing sales invoice |
| `snapshot_and_void` | matching delete scope | Void a document only after logging its full before-state and confirming nothing else still references it; requires an explicit `confirmed_duplicate_of` justification, never deletes a merely "unexplained" record |

Every corrective write appends a before/after entry to a local audit log (`MANAGER_MCP_AUDIT_LOG_PATH`, default `~/.manager_mcp/audit_log.jsonl`) with a `correlation_id` linking a propose/apply pair.

**Missing-invoice workflow example** (the general missing-invoice payment pattern): two supplier payments both structurally reference the same non-existent Purchase Invoice Key. `propose_purchase_invoice_reconstruction` reports the shared supplier, the missing Key, and the total owed, but returns `unresolved` because the per-line breakdown of the invoice cannot be derived from payment totals alone. Once you supply the actual invoice lines (from the source document, not a guess), the proposal succeeds; `apply_purchase_invoice_reconstruction` creates the invoice, reallocates both payments onto it, and verifies the resulting balance is zero.

## Upstream history and migration

These milestones come from the upstream project's numbering. This repository restarts at 1.0.0.

- **Upstream 0.2.0:** task tools added; CRUD tools deprecated but still present under scopes.
- **Upstream 0.3.0 (planned, not done):** removal of the CRUD tools except `create_customer` / `create_supplier`. In 1.0.0 the CRUD tools are still registered. Use task tools or `raw` scope.
- Update `MANAGER_MCP_WRITE_SCOPES` to the recommended narrow set above instead of enabling all domains.

## Tools

### Read tools

| Tool | Returns | Data class | Dates |
|------|---------|------------|-------|
| `list_resources` | Discovery; read-only flag and live write/delete scopes | n/a | n/a |
| `get_server_info` | Which process you are connected to (version, pid, start time, git state, registered tools) | n/a | n/a |
| `list_records` / `get_record` / `get_fixed_asset` | Stored records as they are today | current state | none |
| `aged_receivables` / `aged_payables` | Current customer or supplier balances. **Not an aged report.** | current state | rejected |
| `bank_balances` | Current bank and cash balances | current state | rejected |
| `trial_balance` / `profit_and_loss` / `balance_sheet` | Raw transaction feed rows. **Not Manager's reports.** | raw feed | `from_date`/`to_date` forwarded |
| `tax_summary` | Raw tax feed rows. **Not a VAT return.** | raw feed | rejected |
| `find_records` and the other diagnostics | See [Reconciliation & corrections](#reconciliation--corrections) | historical / derived | varies |
| `manager_report_catalogue` | What each Manager report is and how it can be read | catalogue | n/a |
| `get_report_definition` | Stored report settings for a known key (never calculated rows) | settings only | n/a |
| `ledger_transactions` | Account-level ledger rows with client side date, account and party filters, paged | historical transaction data | `from_date`/`to_date` |
| `reconstructed_trial_balance` / `reconstructed_profit_and_loss` | Calculated from the ledger, `authoritative: false` | reconstructed | `as_at` / period |
| `reconstructed_aged_receivables` / `reconstructed_aged_payables` | Calculated from the ledger, `authoritative: false` | reconstructed | `as_at` |

Tools that cannot honour a date reject it with an error. They never return current data for a past date.

Collections for `list_records` / `get_record` (27): `customers`, `suppliers`, `sales_invoices`, `purchase_invoices`, `chart_of_accounts`, `bank_accounts`, `sales_quotes`, `purchase_quotes`, `sales_orders`, `purchase_orders`, `inventory_items`, `non_inventory_items`, `credit_notes`, `delivery_notes`, `debit_notes`, `goods_receipts`, `receipts`, `payments`, `inter_account_transfers`, `employees`, `payslips`, `expense_claims`, `journal_entries`, `depreciation_entries`, `amortization_entries`, `fixed_assets`, `tax_codes`. `fixed_assets` and `tax_codes` are read-only.

`chart_of_accounts` is list/search only (no single-form GET). Chart of accounts changes cannot be made through this MCP because those endpoints are on the denylist.

**Bank dual path (intentional):** `bank_balances` answers “what are my balances?”; `list_records` / `get_record` on `bank_accounts` answers “find account X and show detail.”

### Write tools (deprecated)

Registered only for resources in enabled scopes. Prefer task tools above.

| Pattern | Requires | Notes |
|---------|----------|-------|
| `create_{stem}` | write scope | Deprecated in 0.2.0 |
| `update_{stem}` | write scope | Deprecated in 0.2.0 |
| `delete_{stem}` | delete scope | Deprecated in 0.2.0; use `void_document` |

## Agent Skill

Companion skill: [`skills/manager-accounting/SKILL.md`](skills/manager-accounting/SKILL.md).

The Cursor plugin discovers this skill from `skills/`. Without the plugin, copy or symlink that folder into your agent skills path. It tells the model to call `list_resources` first, verify after writes, and which report tools to prefer.

## Development

```bash
uv sync --extra dev
uv run manager-mcp
```

Offline tests only (respx). No live Manager required:

```bash
uv run ruff check src tests
uv run pytest
```

GitHub Actions matrix: Python 3.10 and 3.12.

### Running from local source vs. the published package

Your MCP host's `.mcp.json` (or equivalent) must point at **this checkout**,
not the published PyPI package, or local edits will silently have no effect:

```jsonc
{
  "mcpServers": {
    "manager-mcp": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/manager-mcp", "manager-mcp"],
      "env": { "...": "..." }
    }
  }
}
```

**Do not use `"command": "uvx", "args": ["--from", "mcp-manager.io", "manager-mcp"]`** while developing:
`uvx --from mcp-manager.io manager-mcp` resolves and runs the *published* package from PyPI,
completely independent of this source tree. It will start, it will look
correct, and every source edit you make will be silently ignored.

### Hot reload during development (opt-in)

By default the lifecycle is manual: edit source, run tests, reconnect the MCP host, and a new process loads the new code.

To avoid the reconnect for code changes, set `MANAGER_MCP_DEV_SUPERVISOR=1` in the host's `env`, keeping the same launch command. The server then runs a small stdio proxy that starts the real server as a child process and watches `src/manager_mcp/**/*.py`. When a file changes it waits for in-flight requests to finish (so a write is not interrupted), restarts the child, and replays the MCP initialize handshake to it. It also respawns the child if it crashes. Supervisor logging goes to stderr only, never the MCP stream. A `manager-mcp-dev` command runs the supervisor directly. Leave the variable unset in production.

Your host may still need a reconnect to see tools that were added or removed, because hosts can cache the tool list. `get_server_info` shows the new process id and start time either way.

### Verifying which process you're actually connected to

Call the `get_server_info` tool (works read-only against any running
instance, no Manager API call is made). Before starting work, and again
after any reconnect following a source edit:

```text
get_server_info
```

Confirm the fields you expect:

- `version` matches the `version` in `pyproject.toml` (or whatever you just bumped it to)
- `source_path` points at *this* checkout, not some other install
- `git_sha` / `git_dirty` match `git rev-parse HEAD` / `git status --porcelain` here
- `effective_write_scopes` / `effective_delete_scopes` match what you expect from `MANAGER_MCP_WRITE_SCOPES`/`MANAGER_MCP_DELETE_SCOPES`
- `pid` and `process_started_at` change after a restart, and `registered_tool_count` / `registered_tools` list what this process actually exposes

**Development checklist after modifying source:**

1. `uv run pytest`: tests must pass first.
2. Reconnect/restart the MCP connection in your host (not needed for code-only changes when the dev supervisor is enabled).
3. Call `get_server_info`.
4. Confirm version/commit/source match what you just changed. If they
   don't, the host is still talking to the old process (or the wrong
   launch command), and nothing past this point should be trusted.
5. Only once confirmed, run live read tests against Manager.

This is the exact checklist that would have caught, immediately, the
`uvx`-vs-local-source mismatch this project hit in practice: a server that
looked connected and answered read requests, but was silently running
weeks-old published code with no pagination fix and no `fixed_assets`
support.

## Caveats

- No response caching, anywhere, deliberately. Every read tool hits Manager
  live on every call. For live accounting data, a stale cache is worse than
  an extra HTTP round trip; this is a conscious choice, not an oversight.
- One process ↔ one `MANAGER_API_URL`. Multi-instance routing is out of scope.
- Multi-business disambiguation on a shared host is **unverified**. Do not claim multi-business support until validated against a live multi-business setup.
- The specification files under `src/manager_mcp/spec/` (the curated `api2.json`, the archived live spec and the capability matrix) are provenance only; runtime always hits the live URL.
- Reconstructed reports are not Manager's official reports, and reconstructed ageing can differ from Manager's own ageing. See [docs/reporting.md](docs/reporting.md).
- ChatGPT Apps need a hosted HTTP MCP endpoint. This package is stdio-only.

## License

MIT. See [LICENSE](LICENSE).

## Reporting and authority

Manager's API returns no finished calculated reports. The finished report views exist only inside the Manager application and are not used by this MCP.

| Kind | Meaning | Tools |
|------|---------|-------|
| Current state | Balances as they are today, never historical | `aged_receivables`, `aged_payables`, `bank_balances`, `list_records`, `get_record` |
| Raw feed | Rows from Manager's `*-transactions` feeds, not a report | `trial_balance`, `profit_and_loss`, `balance_sheet`, `tax_summary` |
| Historical transaction data | Ledger rows with the account on every row | `ledger_transactions`, `account_ledger`, `bank_activity` |
| Reconstructed | Calculated here, always `authoritative: false` | `reconstructed_*` |
| Report settings | Stored definition only, no rows | `get_report_definition` |

Reconstructed reports matched a live Manager instance's official receivables, payables and cash figures at a past date in a validation run, but that does not make them official. Full details, methods and limits: [docs/reporting.md](docs/reporting.md).

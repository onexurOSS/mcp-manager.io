# Onexur Manager MCP

An MCP (Model Context Protocol) server that connects AI assistants such as Claude and ChatGPT to a [Manager](https://www.manager.io) accounting instance. It exposes Manager's data and operations as a set of scoped tools. The default is read-only, with reporting, diagnostic and reconciliation tools available out of the box and every write capability behind explicit configuration.

This project began as a fork of [manager-mcp](https://github.com/flumpiey/manager-mcp) 0.2.6. See [Attribution](https://github.com/onexurOSS/mcp-manager.io#7-attribution) below.

## 1. Project description

Onexur Manager MCP lets an AI assistant read and, where explicitly permitted, act on a Manager accounting instance through a structured tool interface rather than free-form API calls. The server enforces a tiered permission model so that read access and write access are each opted into separately, and delete access is opted into separately again.

Typical uses:

- Ask an assistant to explain a balance, find unpaid invoices, or summarise account activity.
- Run reconciliation and diagnostic checks (duplicate transactions, unallocated payments, broken invoice references, suspense account candidates) without writing a script.
- Rebuild a trial balance, profit and loss or aged balances as at a past date from Manager's ledger, clearly labelled as a reconstruction and not an official report.
- Propose and, after review, apply corrections to specific transaction types, always through an explicit propose then apply pattern rather than a direct write.

Nothing that changes Manager is registered by default. Every write request is also checked against a permanent denylist before any scope is consulted (see [Configuration](https://github.com/onexurOSS/mcp-manager.io#3-configuration)).

## 2. Installation

The package is named `mcp-manager.io`, the Python module is `manager_mcp`, and the commands are `manager-mcp` and `manager-mcp-dev`. It requires Python 3.10 or later and [uv](https://docs.astral.sh/uv/).

Until the first release is published to PyPI, run it from a clone:

```bash
git clone https://github.com/onexurOSS/mcp-manager.io.git
cd mcp-manager.io
uv sync
uv run manager-mcp
```

After publication, no clone is needed:

```bash
uvx --from mcp-manager.io manager-mcp
```

The server speaks MCP over stdio. To use it from a client, configure the client to run one of the commands above with your Manager connection details as environment variables, for example:

```json
{
  "mcpServers": {
    "manager": {
      "command": "uvx",
      "args": ["--from", "mcp-manager.io", "manager-mcp"],
      "env": {
        "MANAGER_API_URL": "http://127.0.0.1:55667/api2",
        "MANAGER_API_KEY": "your-access-token"
      }
    }
  }
}
```

Client integrations in this repository:

- [`mcpb/`](https://github.com/onexurOSS/mcp-manager.io/tree/main/mcpb) holds the manifest for a Claude Desktop extension bundle. Built bundles are not committed. The manifest runs the published `mcp-manager.io` package through `uv`, so it works once the package is published.
- [`.cursor-plugin/plugin.json`](https://github.com/onexurOSS/mcp-manager.io/blob/main/.cursor-plugin/plugin.json) is a Cursor plugin manifest.
- For other clients, such as a `.cursor/mcp.json` or `.vscode/mcp.json` entry in your own project, use the pattern above.

ChatGPT Apps need a hosted HTTP endpoint. This package is stdio only.

## 3. Configuration

One process talks to one Manager instance, configured through environment variables:

| Variable | Purpose |
|---|---|
| `MANAGER_API_URL` | Base URL of the Manager API, including `/api2` when required. Required. |
| `MANAGER_API_KEY` | Access token created in Manager Settings, sent as `X-API-KEY`. Required. |
| `MANAGER_MCP_WRITE_SCOPES` | Comma-separated write scopes. Empty by default. |
| `MANAGER_MCP_DELETE_SCOPES` | Comma-separated delete scopes. Empty by default, and never implied by write scopes. |
| `MANAGER_MCP_AUDIT_LOG_PATH` | Where corrective writes are logged as JSON lines (before and after state). Defaults to `~/.manager_mcp/audit_log.jsonl`. |
| `MANAGER_MCP_DEV_SUPERVISOR` | Set to `1` to restart the server automatically when source files change. Development use only. |

The older `MANAGER_MCP_ALLOW_WRITES`, `ALLOW_WRITES` and `MANAGER_MCP_WRITES` variables are rejected with an error that points to the two scope variables above.

Never commit your API key. Keep it in the environment of the MCP client or in a private env file.

### Permission scopes

Valid scopes are `quotes`, `orders`, `parties`, `items`, `sales`, `purchases`, `banking`, `payroll` and `ledger`, plus `raw`, an escape hatch that enables the full create and update (or delete) set for every domain. A recommended starting point for day-to-day bookkeeping is `banking,sales,parties`.

With no scopes set, 30 read-only tools are registered. Enabling a scope registers only the tools for that domain. Scopes are additive and independent, and unknown scope names are refused at startup. With every write and delete scope enabled, up to 126 tools are registered.

Every request that would change Manager passes a policy check first. Requests to paths such as access tokens, the chart of accounts, tax codes, exchange rates, starting balances, bank reconciliation, custom fields, email settings and the customer portal are permanently denied, whatever scopes are enabled.

## 4. Tool descriptions

**Read tools (registered by default, 30 in total)**

- Discovery and raw access: `list_resources`, `list_records`, `get_record`, `get_fixed_asset` and `get_server_info` (server identity, process, git state, registered tools and active scopes).
- Legacy report shortcuts: `aged_receivables`, `aged_payables`, `bank_balances`, `trial_balance`, `profit_and_loss`, `balance_sheet` and `tax_summary`. These return Manager's current state or raw feeds and are not finished reports (see [Reporting limitations](https://github.com/onexurOSS/mcp-manager.io#5-reporting-limitations)).
- Reporting layer (`reporting.py`): `manager_report_catalogue`, `get_report_definition`, `ledger_transactions`, `reconstructed_trial_balance`, `reconstructed_profit_and_loss`, `reconstructed_aged_receivables` and `reconstructed_aged_payables`.
- Diagnostics (`diagnostics.py`): `find_records`, `find_broken_invoice_references`, `find_unallocated_transactions`, `find_duplicate_transactions`, `verify_invoice_balance`, `account_ledger`, `bank_activity`, `find_suspense_candidate_accounts` and `general_ledger_summary`.
- Reconciliation (`reconciliation.py`): `reconcile_period`.
- `list_incomplete_reconstructions` (`corrections.py`): invoice-reconstruction attempts that started but have not completed, from the local audit log only.

**Write tools (registered only when the matching scope is enabled)**

- Task tools (`task_tools.py`) shaped around an intent, for example `issue_sales_invoice`, `issue_purchase_invoice`, `record_customer_payment`, `record_supplier_payment`, `record_expense`, `transfer_between_accounts` and `post_journal_entry`. These are the recommended write path.
- Per-resource `create_*` and `update_*` tools for each enabled scope, and `delete_*` tools for each enabled delete scope. `create_fixed_asset` and `update_fixed_asset` (`fixed_assets.py`) need the `ledger` scope.
- Corrections (`corrections.py`): `propose_correction` and `apply_correction`, `propose_*_reconstruction` and `apply_*_reconstruction` for purchase and sales invoices, `reallocate_payment_line` and `reallocate_receipt_line`, and `snapshot_and_void`, which is preferred over the plain `void_document` tool. Nothing here writes without a separate apply step after a proposal, and each corrective write is recorded in the audit log.

Descriptions and schemas for every registered tool are available from your MCP client, and `get_server_info` reports which are active.

## 5. Reporting limitations

Manager's API does not return finished reports. The report view routes are application only and answer HTTP 401 to an API key, and this project neither calls nor imitates them. The reporting tools work from what the API does return, and label what they return. The full guide is [docs/reporting.md](https://github.com/onexurOSS/mcp-manager.io/blob/main/docs/reporting.md).

- **Reconstructions are never authoritative.** `reconstructed_trial_balance`, `reconstructed_profit_and_loss`, `reconstructed_aged_receivables` and `reconstructed_aged_payables` are calculated by this server from Manager's account level ledger. Each result carries `authoritative: false` and `official_manager_report: false`, with its source, calculation method, as-at date and ledger completeness. A match with a figure in Manager does not make a reconstruction official.
- **Reconstructed ageing can differ from Manager's.** Ageing here uses transaction dates and applies payments oldest first, whereas Manager uses due dates and actual allocations, so bucket splits can differ. Party totals are checked only against the control account total.
- **Current state tools reject dates.** `aged_receivables`, `aged_payables`, `bank_balances` and `tax_summary` show balances as they are today. If you pass a date or period they refuse the request, and they never return today's figures for a past date.
- **Dates on the ledger are filtered here.** Manager ignores date parameters on its `/transactions` ledger feed, so the server fetches the complete ledger and filters it itself. Results are never silently truncated. Paged results report totals and a next position, and feeds report whether they are complete.
- **Not available from the API:** Manager's own group and subtotal layout, invoice due dates and allocations as at a past date, VAT returns, and finished report output.

Do not treat any reporting tool as a substitute for Manager's own reports where precision matters, for example VAT or tax filings.

## 6. Licensing

Onexur Manager MCP is distributed under the GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later).

This project incorporates material from manager-mcp 0.2.6, which remains licensed under the MIT License. Incorporating that material under this project's distribution does not transfer or extinguish the existing MIT rights and copyright in the upstream-derived portions; those rights remain in effect for recipients. See [`NOTICE`](https://github.com/onexurOSS/mcp-manager.io/blob/main/NOTICE) for the full attribution statement and [`PROVENANCE.json`](https://github.com/onexurOSS/mcp-manager.io/blob/main/PROVENANCE.json) for a file-level breakdown of which parts of this repository are upstream-derived (MIT) and which are original to Xalterra Ltd, trading as Onexur (AGPL-3.0-or-later).

A commercial license, covering use of Xalterra Ltd's Onexur-owned code without the obligations of the AGPL, is available. Contact licensing@xalterra.com for terms.

The complete AGPL-3.0-or-later text is reproduced in [`LICENSE-AGPL`](https://github.com/onexurOSS/mcp-manager.io/blob/main/LICENSE-AGPL), and the complete MIT License text is reproduced in [`LICENSE-MIT`](https://github.com/onexurOSS/mcp-manager.io/blob/main/LICENSE-MIT).

## 7. Attribution

This project incorporates material from [manager-mcp](https://github.com/flumpiey/manager-mcp) 0.2.6, released under the MIT License, copyright (c) 2026 manager-mcp contributors.

A detailed, file-level breakdown of which parts of this repository are upstream-derived and which are original to Xalterra Ltd, trading as Onexur, is maintained in [`PROVENANCE.json`](https://github.com/onexurOSS/mcp-manager.io/blob/main/PROVENANCE.json), alongside a human-readable summary in [`PROVENANCE_SUMMARY.md`](https://github.com/onexurOSS/mcp-manager.io/blob/main/PROVENANCE_SUMMARY.md). Historical upstream release notes (versions 0.1.0 through 0.2.6) are preserved unchanged in [docs/upstream-history.md](https://github.com/onexurOSS/mcp-manager.io/blob/main/docs/upstream-history.md). Current release notes are in [CHANGELOG.md](https://github.com/onexurOSS/mcp-manager.io/blob/main/CHANGELOG.md).

The complete MIT License text is reproduced in [`LICENSE-MIT`](https://github.com/onexurOSS/mcp-manager.io/blob/main/LICENSE-MIT). See [`NOTICE`](https://github.com/onexurOSS/mcp-manager.io/blob/main/NOTICE) for the full attribution statement.

## 8. Trademark disclaimer

"Manager" and any associated logos are trademarks of their respective owner. This project is an independent, third-party integration and is not affiliated with, endorsed by, or sponsored by Manager or its publisher. No Manager branding, logos, or proprietary API description material are distributed with this project.

---

## Contributing

See [`CONTRIBUTING.md`](https://github.com/onexurOSS/mcp-manager.io/blob/main/CONTRIBUTING.md) and [`CLA.md`](https://github.com/onexurOSS/mcp-manager.io/blob/main/CLA.md). Contributions to files identified in [`PROVENANCE.json`](https://github.com/onexurOSS/mcp-manager.io/blob/main/PROVENANCE.json) as upstream-derived remain subject to the upstream MIT license; contributions to Onexur-original files are covered by the project's Contributor License Agreement (version 1.0), which you accept by posting a comment on your pull request, as [`CONTRIBUTING.md`](https://github.com/onexurOSS/mcp-manager.io/blob/main/CONTRIBUTING.md) describes.

## Further reading

- [docs/reporting.md](https://github.com/onexurOSS/mcp-manager.io/blob/main/docs/reporting.md): reporting and authority guide
- [CHANGELOG.md](https://github.com/onexurOSS/mcp-manager.io/blob/main/CHANGELOG.md): current release notes
- [docs/upstream-history.md](https://github.com/onexurOSS/mcp-manager.io/blob/main/docs/upstream-history.md): historical upstream release notes
- [`PROVENANCE.json`](https://github.com/onexurOSS/mcp-manager.io/blob/main/PROVENANCE.json) and [`PROVENANCE_SUMMARY.md`](https://github.com/onexurOSS/mcp-manager.io/blob/main/PROVENANCE_SUMMARY.md): file-level licensing provenance
- [`SECURITY.md`](https://github.com/onexurOSS/mcp-manager.io/blob/main/SECURITY.md): vulnerability disclosure policy

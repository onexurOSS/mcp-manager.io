# Data Model: Manager.io Read-Only MCP Server

Logical entities for v1. No local database; values are projected from Manager
GET responses.

## ManagerInstanceConnection

| Field | Source | Rules |
|-------|--------|-------|
| `base_url` | `MANAGER_API_URL` | Required; opaque; strip trailing slash consistently; typically ends with `/api2` |
| `api_key` | `MANAGER_API_KEY` | Required; sent as `X-API-KEY`; never logged or returned in tool payloads |

**Validation**: Missing/blank → config error. Write-related env truthy →
hard-fail (see plan write-flag policy).

## ResourceDescriptor

| Field | Description |
|-------|-------------|
| `name` | Short allowlist key (e.g. `customers`, `bank_accounts`) |
| `kind` | `collection` \| `report` |
| `path` | Relative Manager path |
| `supports_form` | Collections: true (`{path}-form/{key}` pattern); reports: false |
| `date_params` | Optional list of query keys supported by this report path (empty ⇒ current/default-only) |

`resolve(name)` → descriptor or `None`.

## CollectionPage

| Field | Description |
|-------|-------------|
| `resource` | Allowlist name |
| `items` | List of record summaries (opaque JSON objects from Manager) |
| `skip` | Requested skip |
| `page_size` | Requested page size |
| `term` | Optional search term |
| `truncated` / `has_more` | Agent-visible signal that another page or narrowed search may be needed |

## Record

| Field | Description |
|-------|-------------|
| `resource` | Allowlist collection name |
| `key` | GUID string |
| `body` | Opaque JSON object from `/{resource}-form/{key}` |

**Identity**: `(resource, key)` unique within one connection.

## FinancialSnapshot

| Field | Description |
|-------|-------------|
| `report` | One of: aged_receivables, aged_payables, bank_balances, trial_balance, profit_and_loss, balance_sheet, tax_summary |
| `body` | Opaque JSON from report GET |
| `period_applied` | true if request included supported date/period params; false if current/default |
| `period_unsupported_notice` | Present when caller asked for a period but `date_params` empty |

## AgentSkillPackage

| Field | Description |
|-------|-------------|
| `path` | `skills/manager-accounting/SKILL.md` |
| `triggers` | Manager.io / bookkeeping questions |
| `boundary` | States read-only; points at MCP tools |

## Relationships

```text
ManagerInstanceConnection
    └── serves many ResourceDescriptor (static allowlist)
            ├── collection → CollectionPage → Record
            └── report → FinancialSnapshot
```

## Validation rules (cross-cutting)

- Unknown resource name → error; suggest `list_resources`
- Unknown/unsupported write env → hard-fail before serving
- Query keys outside allowlist (+ per-report date_params) → not forwarded
- No entity lifecycle for creates/updates (writes out of scope)

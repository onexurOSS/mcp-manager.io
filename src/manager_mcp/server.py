"""FastMCP Manager.io server (stdio via `manager-mcp`). Writes via scope envs."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP
from mcp.types import Icon

from manager_mcp import corrections as _corr
from manager_mcp import diagnostics as _diag
from manager_mcp import fixed_assets as _fixed_assets
from manager_mcp import paging as _paging
from manager_mcp import reconciliation as _recon
from manager_mcp import reporting as _reporting
from manager_mcp import server_info as _server_info
from manager_mcp import task_tools as _tt
from manager_mcp.client import ManagerClient
from manager_mcp.resources import all_resources, extract_items, form_path, resolve
from manager_mcp.scopes import DOMAIN_SCOPES, WritePolicy
from manager_mcp.writable import WRITABLE, implemented_for_scope
from manager_mcp.write_validate import diff_persisted, validate_write_body

_SOURCE_PATH = Path(__file__).resolve()

_PERIOD_ALIASES = {
    "from_date": "fromDate",
    "to_date": "toDate",
    "from": "fromDate",
    "to": "toDate",
}

_ICON_PATH = Path(__file__).resolve().parent / "assets" / "icon.png"
_ICON_HTTPS = (
    "https://raw.githubusercontent.com/onexurOSS/mcp-manager.io/main/docs/icon-512.png"
)


def server_icons() -> list[Icon]:
    """Icons for initialize serverInfo (data URI + public HTTPS fallback)."""
    icons: list[Icon] = []
    if _ICON_PATH.is_file():
        b64 = base64.standard_b64encode(_ICON_PATH.read_bytes()).decode("ascii")
        icons.append(
            Icon(
                src=f"data:image/png;base64,{b64}",
                mimeType="image/png",
                sizes=["512x512"],
            )
        )
    icons.append(Icon(src=_ICON_HTTPS, mimeType="image/png", sizes=["512x512"]))
    return icons


mcp = FastMCP(
    "manager-mcp",
    version=_server_info.resolve_version(_SOURCE_PATH.parent.parent),
    website_url="https://www.manager.io/",
    icons=server_icons(),
)
_client: ManagerClient | None = None
_policy: WritePolicy | None = None
_write_tools_registered = False
_task_tools_registered = False

_CRUD_EXEMPT_FROM_DEPRECATION = frozenset({"customer", "supplier"})


def get_policy() -> WritePolicy:
    global _policy
    if _policy is None:
        _policy = WritePolicy.from_env()
    return _policy


def get_client() -> ManagerClient:
    global _client
    if _client is None:
        _client = ManagerClient.from_env(policy=get_policy())
    return _client


def reset_client() -> None:
    """Test helper: drop cached client and policy."""
    global _client, _policy, _write_tools_registered, _task_tools_registered
    _client = None
    _policy = None
    _write_tools_registered = False
    _task_tools_registered = False


def _normalize_period(period: dict[str, Any], date_params: tuple[str, ...]) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for key, value in period.items():
        if value is None or value == "":
            continue
        raw[_PERIOD_ALIASES.get(key, key)] = value
    if not date_params:
        return {}
    return {key: raw[key] for key in date_params if key in raw}


async def _fetch_report(name: str, **period: Any) -> dict[str, Any]:
    desc = resolve(name)
    if desc is None or desc.kind != "report":
        raise ValueError(f"Unknown report '{name}'. Use list_resources.")
    requested = {k: v for k, v in period.items() if v is not None and v != ""}
    if requested and not desc.date_params:
        sem = _reporting.REPORT_SEMANTICS.get(name, {})
        raise ValueError(
            f"{name} does not support date or as-at selection: Manager exposes no "
            f"historical form of this view ({sem.get('data_class', 'current data')}). "
            "Refusing rather than returning current data as if it were historical. "
            "Use reconstructed_* tools or ledger_transactions for dated data."
        )
    forward = _normalize_period(period, desc.date_params)
    client = get_client()
    client._extra_query_keys = frozenset(desc.date_params)
    try:
        paged = await _paging.fetch_paginated(client, name, params=forward or None)
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"Manager HTTP {exc.response.status_code} for {name}") from exc
    if not paged.saw_list:
        # Report shape Manager returns as a single flat object (no list
        # under items_key): one page IS the whole answer; return it as-is,
        # matching the pre-pagination contract for that shape.
        body = paged.first_body if paged.first_body is not None else {}
        result: dict[str, Any] = {
            "report": name,
            "body": body,
            "period_applied": bool(forward),
            "complete": True,
            "completeness": "single_object",
        }
    else:
        body = dict(paged.first_body or {})
        if desc.items_key:
            body[desc.items_key] = paged.items
        if paged.total_records is not None:
            body["totalRecords"] = paged.total_records
        body["skip"] = 0
        body["pageSize"] = len(paged.items)
        result = {
            "report": name,
            "body": body,
            "period_applied": bool(forward),
            "complete": paged.complete,
            "completeness": paged.completeness,
            "returned_count": len(paged.items),
            "pages_fetched": paged.pages_fetched,
        }
        if paged.total_records is not None:
            result["total_records"] = paged.total_records
        if paged.completeness == "max_pages_reached":
            result["truncation_notice"] = (
                f"Only {len(paged.items)} of {paged.total_records} records "
                f"returned after {paged.pages_fetched} pages (pagination cap "
                "reached). This result is INCOMPLETE: do not treat it as "
                "the full report; narrow the date range or ask for a higher "
                "page cap."
            )
        elif paged.completeness == "unable_to_determine":
            result["truncation_notice"] = (
                f"{len(paged.items)} records returned after "
                f"{paged.pages_fetched} pages, all full-sized, with no "
                "totalRecords from Manager to check against: there is NO "
                "basis to call this complete. Treat it as unknown-completeness, "
                "not as the full report."
            )
    result["semantics"] = _reporting.report_semantics(name, period_applied=bool(forward))
    return result


@mcp.tool(
    description=(
        "List curated Manager.io capabilities. Default is read-only (10 tools). "
        "Task tools register when write scopes match; CRUD tools are deprecated "
        "unless raw scope is set."
    )
)
async def list_resources() -> dict[str, Any]:
    policy = get_policy()
    resources = [
        {
            "name": r.name,
            "kind": r.kind,
            "description": r.description,
            "path": r.path,
        }
        for r in all_resources()
    ]
    write_scopes = sorted(policy.write_scopes)
    delete_scopes = sorted(policy.delete_scopes)
    effective_write = sorted(policy.effective_write_scopes)
    read_only = not (write_scopes or delete_scopes)
    if read_only:
        boundary = (
            "Default is read-only: no task or CRUD write tools are registered. "
            "Set MANAGER_MCP_WRITE_SCOPES / MANAGER_MCP_DELETE_SCOPES to enable mutations. "
            "Recommended write scopes: banking,sales,parties."
        )
    else:
        boundary = (
            "Scoped writes enabled. Prefer task tools (issue_sales_invoice, "
            "record_customer_payment, record_customer_deposit, …). "
            "Per-resource CRUD is deprecated in 0.2.0 (removed in 0.3.0) unless "
            "raw is in WRITE_SCOPES. write_scopes="
            f"{write_scopes}; effective_write={effective_write}; "
            f"delete_scopes={delete_scopes}. Verify writes with list_records/get_record."
        )
    return {
        "resources": resources,
        "read_only": read_only,
        "write_scopes": write_scopes,
        "delete_scopes": delete_scopes,
        "effective_write_scopes": effective_write,
        "boundary": boundary,
    }


_server_info.register_server_info_tool(mcp, lambda: get_policy(), _SOURCE_PATH)


@mcp.tool(
    description=(
        "Search/page a curated collection. Core: customers, suppliers, sales_invoices, "
        "purchase_invoices, chart_of_accounts, bank_accounts. Also writable domains "
        "when present in discovery (e.g. receipts, payments, sales_quotes). "
        "bank_accounts is the searchable collection; use bank_balances for snapshot balances."
    )
)
async def list_records(
    resource: str,
    term: str | None = None,
    sort_by: str | None = None,
    sort_by_desc: bool | None = None,
    skip: int = 0,
    page_size: int = 50,
) -> dict[str, Any]:
    desc = resolve(resource)
    if desc is None or desc.kind != "collection":
        raise ValueError(
            f"Unknown collection '{resource}'. Use list_resources for supported names."
        )
    params: dict[str, Any] = {"skip": skip, "pageSize": page_size}
    if term is not None:
        params["term"] = term
    if sort_by is not None:
        params["sortBy"] = sort_by
    if sort_by_desc is not None:
        params["sortByDesc"] = sort_by_desc
    client = get_client()
    try:
        body = await client.get(desc.path, params=params)
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"Manager HTTP {exc.response.status_code}") from exc
    items = extract_items(desc, body)
    total = body.get("totalRecords") if isinstance(body, dict) else None
    if isinstance(total, int):
        has_more = skip + len(items) < total
    else:
        has_more = len(items) >= page_size
    return {
        "resource": resource,
        "items": items,
        "skip": skip,
        "page_size": page_size,
        "term": term,
        "truncated": has_more,
        "has_more": has_more,
    }


@mcp.tool(
    description=(
        "Fetch one collection record by GUID via Manager form endpoint "
        "(e.g. /customer-form/{key}). chart_of_accounts has no single form. "
        "For bank/cash account detail use resource=bank_accounts (not bank_balances)."
    )
)
async def get_record(resource: str, key: str) -> dict[str, Any]:
    path = form_path(resource, key)
    if path is None:
        raise ValueError(
            f"Unknown collection '{resource}' or form fetch unsupported. Use list_resources."
        )
    client = get_client()
    try:
        body = await client.get(path)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise RuntimeError(f"Record not found: {resource}/{key}") from exc
        raise RuntimeError(f"Manager HTTP {exc.response.status_code}") from exc
    return {"resource": resource, "key": key, "body": body}


_fixed_assets.register_fixed_asset_read_tool(mcp, lambda: get_client())


@mcp.tool(
    description=(
        "CURRENT customer balances only (read-only). Not an aged report and not as at any "
        "past date; rejects from_date/to_date. For a labelled as-at reconstruction use "
        "reconstructed_aged_receivables."
    )
)
async def aged_receivables(
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    return await _fetch_report("aged_receivables", from_date=from_date, to_date=to_date)


@mcp.tool(
    description=(
        "CURRENT supplier balances only (read-only). Not an aged report and not as at any "
        "past date; rejects from_date/to_date. For a labelled as-at reconstruction use "
        "reconstructed_aged_payables."
    )
)
async def aged_payables(
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    return await _fetch_report("aged_payables", from_date=from_date, to_date=to_date)


@mcp.tool(
    description=(
        "CURRENT bank/cash balances only (read-only; rejects dates). "
        "For search/drill-in of individual accounts use list_records/get_record on bank_accounts."
    )
)
async def bank_balances(
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    return await _fetch_report("bank_balances", from_date=from_date, to_date=to_date)


@mcp.tool(
    description=(
        "Raw /trial-balance-transactions rows (read-only). NOT Manager's Trial Balance "
        "report and no account per row; see reconstructed_trial_balance."
    )
)
async def trial_balance(
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    return await _fetch_report("trial_balance", from_date=from_date, to_date=to_date)


@mcp.tool(
    description=(
        "Raw /profit-and-loss-statement-transactions rows (read-only). NOT Manager's "
        "Profit and Loss Statement; see reconstructed_profit_and_loss."
    )
)
async def profit_and_loss(
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    return await _fetch_report("profit_and_loss", from_date=from_date, to_date=to_date)


@mcp.tool(
    description=(
        "Raw /balance-sheet-transactions rows (read-only). NOT Manager's Balance Sheet "
        "and no account per row; see reconstructed_trial_balance."
    )
)
async def balance_sheet(
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    return await _fetch_report("balance_sheet", from_date=from_date, to_date=to_date)


@mcp.tool(
    description=(
        "Raw /tax-summary-transactions rows (read-only). No date support and not a VAT "
        "return; for dated tax rows use ledger_transactions with tax_only=true."
    )
)
async def tax_summary(
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    return await _fetch_report("tax_summary", from_date=from_date, to_date=to_date)


_diag.register_diagnostic_tools(mcp, lambda: get_client())


_recon.register_reconciliation_tools(mcp, lambda: get_client())


async def _persist_and_verify(
    resource_name: str,
    fields: dict[str, Any],
    body: Any,
) -> dict[str, Any]:
    w = WRITABLE[resource_name]
    result: dict[str, Any] = {"resource": resource_name, "body": body, "warnings": []}
    if not w.known_keys or not isinstance(body, dict):
        return result
    key = body.get("Key") or body.get("key")
    if not key:
        result["warnings"] = ["create/update response had no Key; could not verify"]
        return result
    persisted = await get_client().get(f"{w.form_path}/{key}")
    form = persisted if isinstance(persisted, dict) else None
    result["warnings"] = diff_persisted(w, fields, form)
    result["verified"] = persisted
    return result


def _make_create_tool(resource_name: str) -> Any:
    w = WRITABLE[resource_name]
    stem = w.tool_stem

    async def _create(fields: dict[str, Any]) -> dict[str, Any]:
        validate_write_body(w, fields, creating=True)
        body = await get_client().post(w.form_path, json=fields)
        if w.known_keys:
            return await _persist_and_verify(resource_name, fields, body)
        return {"resource": resource_name, "body": body}

    _create.__name__ = f"create_{stem}"
    notes = f" {w.create_notes}" if w.create_notes else ""
    _create.__doc__ = (
        f"Create a {stem.replace('_', ' ')} via POST {w.form_path}. "
        f"Requires {w.scope!r} in MANAGER_MCP_WRITE_SCOPES.{notes}"
    )
    return _create


def _make_update_tool(resource_name: str) -> Any:
    w = WRITABLE[resource_name]
    stem = w.tool_stem

    async def _update(key: str, fields: dict[str, Any]) -> dict[str, Any]:
        validate_write_body(w, fields, creating=False)
        path = f"{w.form_path}/{key}"
        body = await get_client().put(path, json=fields)
        if w.known_keys:
            out = await _persist_and_verify(resource_name, fields, body or {"Key": key})
            out["key"] = key
            return out
        return {"resource": resource_name, "key": key, "body": body}

    _update.__name__ = f"update_{stem}"
    _update.__doc__ = (
        f"Update a {stem.replace('_', ' ')} via PUT {w.form_path}/{{key}}. "
        f"Requires {w.scope!r} in MANAGER_MCP_WRITE_SCOPES. "
        "Prefer GET form → modify → PUT (full document replace)."
    )
    return _update


def _make_delete_tool(resource_name: str) -> Any:
    w = WRITABLE[resource_name]
    stem = w.tool_stem

    async def _delete(key: str) -> dict[str, Any]:
        path = f"{w.form_path}/{key}"
        body = await get_client().delete(path)
        return {"resource": resource_name, "key": key, "body": body}

    _delete.__name__ = f"delete_{stem}"
    _delete.__doc__ = (
        f"Delete a {stem.replace('_', ' ')} via DELETE {w.form_path}/{{key}}. "
        f"Requires {w.scope!r} in MANAGER_MCP_DELETE_SCOPES "
        "(write scope alone is not enough)."
    )
    return _delete


_WRITE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
_DELETE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}


def _deprecation_prefix(policy: WritePolicy, stem: str) -> str:
    if "raw" in policy.write_scopes:
        return ""
    if stem in _CRUD_EXEMPT_FROM_DEPRECATION:
        return ""
    return "[DEPRECATED in 0.2.0; use task tools] "


def _scopes_for_registration(policy: WritePolicy) -> set[str]:
    if "raw" in policy.write_scopes or "raw" in policy.delete_scopes:
        return set(DOMAIN_SCOPES)
    return set(policy.write_scopes | policy.delete_scopes) - {"raw"}


def register_write_tools() -> None:
    """Validate scope env and register create_*/update_*/delete_* for implemented scopes."""
    global _write_tools_registered
    if _write_tools_registered:
        return
    policy = get_policy()

    if "ledger" in policy.effective_write_scopes:
        _fixed_assets.register_fixed_asset_write_tools(
            mcp, lambda: get_client(), lambda: get_policy(), _WRITE_ANNOTATIONS
        )

    def prefix_fn(stem: str) -> str:
        return _deprecation_prefix(policy, stem)

    for scope in sorted(_scopes_for_registration(policy)):
        for w in implemented_for_scope(scope):
            stem = w.tool_stem
            dep = prefix_fn(stem)
            if w.scope in policy.effective_write_scopes:
                create_fn = _make_create_tool(w.name)
                update_fn = _make_update_tool(w.name)
                mcp.tool(
                    name=f"create_{stem}",
                    description=dep + (create_fn.__doc__ or ""),
                    annotations=_WRITE_ANNOTATIONS,
                )(create_fn)
                mcp.tool(
                    name=f"update_{stem}",
                    description=dep + (update_fn.__doc__ or ""),
                    annotations=_WRITE_ANNOTATIONS,
                )(update_fn)
            if w.scope in policy.effective_delete_scopes:
                delete_fn = _make_delete_tool(w.name)
                mcp.tool(
                    name=f"delete_{stem}",
                    description=dep + (delete_fn.__doc__ or ""),
                    annotations=_DELETE_ANNOTATIONS,
                )(delete_fn)
    _write_tools_registered = True


def register_task_tools() -> None:
    """Register intent-shaped task tools when required write scopes are active."""
    global _task_tools_registered
    if _task_tools_registered:
        return
    policy = get_policy()
    effective = policy.effective_write_scopes

    _corr.register_correction_tools(
        mcp, lambda: get_client(), lambda: get_policy(), _WRITE_ANNOTATIONS
    )

    if "sales" in effective:

        @mcp.tool(
            name="issue_sales_invoice",
            description=(
                "Issue a sales invoice with inline line items. Requires sales scope. "
                "Body is Manager-native JSON (clone get_record template)."
            ),
            annotations=_WRITE_ANNOTATIONS,
        )
        async def issue_sales_invoice(fields: dict[str, Any]) -> dict[str, Any]:
            return await _tt.issue_sales_invoice(get_client(), get_policy(), fields)

    if "purchases" in effective:

        @mcp.tool(
            name="issue_purchase_invoice",
            description=(
                "Issue a purchase invoice. Requires purchases scope. "
                "Body is Manager-native JSON."
            ),
            annotations=_WRITE_ANNOTATIONS,
        )
        async def issue_purchase_invoice(fields: dict[str, Any]) -> dict[str, Any]:
            return await _tt.issue_purchase_invoice(get_client(), get_policy(), fields)

    if "quotes" in effective:

        @mcp.tool(
            name="issue_quote",
            description=(
                "Issue a sales or purchase quote. Requires quotes scope. "
                "Set purchase=true for purchase quotes."
            ),
            annotations=_WRITE_ANNOTATIONS,
        )
        async def issue_quote(
            fields: dict[str, Any],
            purchase: bool = False,
        ) -> dict[str, Any]:
            return await _tt.issue_quote(get_client(), get_policy(), fields, purchase=purchase)

        @mcp.tool(
            name="issue_deposit_invoice",
            description=(
                "Issue a sales quote styled as a deposit invoice (not revenue). "
                "Requires quotes scope. Confirm tax treatment with accountant."
            ),
            annotations=_WRITE_ANNOTATIONS,
        )
        async def issue_deposit_invoice(fields: dict[str, Any]) -> dict[str, Any]:
            return await _tt.issue_deposit_invoice(get_client(), get_policy(), fields)

    if "quotes" in effective and "sales" in effective:

        @mcp.tool(
            name="convert_quote_to_invoice",
            description=(
                "Convert a sales quote to a sales invoice. Requires quotes and sales scopes."
            ),
            annotations=_WRITE_ANNOTATIONS,
        )
        async def convert_quote_to_invoice(
            quote_key: str,
            extra_fields: dict[str, Any] | None = None,
            purchase: bool = False,
        ) -> dict[str, Any]:
            return await _tt.convert_quote_to_invoice(
                get_client(),
                get_policy(),
                quote_key,
                purchase=purchase,
                extra_fields=extra_fields,
            )

    if "banking" in effective:

        @mcp.tool(
            name="record_customer_payment",
            description=(
                "Record a customer receipt and allocate it to an open sales invoice. "
                "Requires banking scope. Atomic: receipt + allocation."
            ),
            annotations=_WRITE_ANNOTATIONS,
        )
        async def record_customer_payment(
            customer: str,
            bank_account: str,
            date: str,
            amount: float,
            invoice_key: str,
            reference: str | None = None,
            paid_by: int = 1,
            description: str | None = None,
        ) -> dict[str, Any]:
            return await _tt.record_customer_payment(
                get_client(),
                get_policy(),
                customer=customer,
                bank_account=bank_account,
                date=date,
                amount=amount,
                invoice_key=invoice_key,
                reference=reference,
                paid_by=paid_by,
                description=description,
            )

        @mcp.tool(
            name="record_supplier_payment",
            description=(
                "Record a supplier payment allocated to a purchase invoice. "
                "Requires banking scope."
            ),
            annotations=_WRITE_ANNOTATIONS,
        )
        async def record_supplier_payment(
            supplier: str,
            bank_account: str,
            date: str,
            amount: float,
            invoice_key: str,
            reference: str | None = None,
            paid_by: int = 1,
            description: str | None = None,
        ) -> dict[str, Any]:
            return await _tt.record_supplier_payment(
                get_client(),
                get_policy(),
                supplier=supplier,
                bank_account=bank_account,
                date=date,
                amount=amount,
                invoice_key=invoice_key,
                reference=reference,
                paid_by=paid_by,
                description=description,
            )

        @mcp.tool(
            name="record_customer_deposit",
            description=(
                "Record money received before an invoice exists (not revenue). "
                "Requires banking scope and a deposit bank/cash account. "
                "Returns precondition_failed with setup steps if the account is missing."
            ),
            annotations=_WRITE_ANNOTATIONS,
        )
        async def record_customer_deposit(
            customer: str,
            amount: float,
            date: str,
            bank_account: str | None = None,
            reference: str | None = None,
            paid_by: int = 1,
            description: str | None = None,
        ) -> dict[str, Any]:
            return await _tt.record_customer_deposit(
                get_client(),
                get_policy(),
                customer=customer,
                amount=amount,
                date=date,
                bank_account=bank_account,
                reference=reference,
                paid_by=paid_by,
                description=description,
            )

        @mcp.tool(
            name="transfer_between_accounts",
            description="Transfer between bank/cash accounts. Requires banking scope.",
            annotations=_WRITE_ANNOTATIONS,
        )
        async def transfer_between_accounts(fields: dict[str, Any]) -> dict[str, Any]:
            return await _tt.transfer_between_accounts(get_client(), get_policy(), fields)

    if "payroll" in effective or "purchases" in effective:

        @mcp.tool(
            name="record_expense",
            description=(
                "Record an expense via expense_claim (payroll) or purchase_invoice "
                "(purchases). Requires payroll and/or purchases scope."
            ),
            annotations=_WRITE_ANNOTATIONS,
        )
        async def record_expense(
            fields: dict[str, Any],
            via: str = "auto",
        ) -> dict[str, Any]:
            return await _tt.record_expense(
                get_client(), get_policy(), fields, via=via
            )

    if "ledger" in effective:

        @mcp.tool(
            name="post_journal_entry",
            description="Post a generic journal entry. Requires ledger scope.",
            annotations=_WRITE_ANNOTATIONS,
        )
        async def post_journal_entry(fields: dict[str, Any]) -> dict[str, Any]:
            return await _tt.post_journal_entry(get_client(), get_policy(), fields)

        @mcp.tool(
            name="apply_deposit_to_invoice",
            description=(
                "Apply held customer deposit to a sales invoice via journal entry. "
                "Requires ledger scope. Clone get_record on journal_entries. "
                "Deposits are not revenue."
            ),
            annotations=_WRITE_ANNOTATIONS,
        )
        async def apply_deposit_to_invoice(fields: dict[str, Any]) -> dict[str, Any]:
            return await _tt.apply_deposit_to_invoice(get_client(), get_policy(), fields)

    if policy.effective_delete_scopes:

        @mcp.tool(
            name="void_document",
            description=(
                "Void (delete) a document by resource name and key. "
                "Requires matching DELETE scope (e.g. sales for sales_invoices)."
            ),
            annotations=_DELETE_ANNOTATIONS,
        )
        async def void_document(resource: str, key: str) -> dict[str, Any]:
            return await _tt.void_document(get_client(), get_policy(), resource, key)

        _corr.register_snapshot_and_void(
            mcp, lambda: get_client(), lambda: get_policy(), _DELETE_ANNOTATIONS
        )

    _task_tools_registered = True


_reporting.register_reporting_tools(mcp, get_client)


def main() -> None:
    from manager_mcp.dev_supervisor import is_dev_supervisor_enabled, run_supervisor

    if is_dev_supervisor_enabled():
        run_supervisor()
        return
    register_task_tools()
    register_write_tools()
    mcp.run()


if __name__ == "__main__":
    main()

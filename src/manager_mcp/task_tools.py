"""Intent-shaped accounting task tools (compose scoped CRUD operations)."""

from __future__ import annotations

from typing import Any

from manager_mcp.client import ManagerClient
from manager_mcp.preconditions import (
    _DEPOSIT_ACCOUNT_GUIDE,
    PreconditionItem,
    PreconditionResult,
    precondition_failed_response,
)
from manager_mcp.scopes import WritePolicy, WritesDeniedError
from manager_mcp.writable import WRITABLE, WritableResource
from manager_mcp.write_validate import diff_persisted, validate_write_body


def _body_key(body: Any) -> str:
    return body.get("Key", "") if isinstance(body, dict) else ""


def _envelope(
    status: str,
    *,
    keys: dict[str, str],
    body: Any = None,
    warnings: list[str] | None = None,
    next_steps: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "keys": keys,
        "body": body,
        "warnings": warnings or [],
        "next_steps": next_steps or [],
    }


def require_write_scopes(policy: WritePolicy, *scopes: str) -> None:
    effective = policy.effective_write_scopes
    missing = [s for s in scopes if s not in effective]
    if missing:
        raise WritesDeniedError(
            f"Task requires scope(s) {missing!r} in MANAGER_MCP_WRITE_SCOPES "
            f"(effective: {sorted(effective)})."
        )


def require_delete_scope(policy: WritePolicy, scope: str) -> None:
    if scope not in policy.effective_delete_scopes:
        raise WritesDeniedError(
            f"void_document requires scope {scope!r} in MANAGER_MCP_DELETE_SCOPES."
        )


async def _post(
    client: ManagerClient,
    resource: WritableResource,
    fields: dict[str, Any],
) -> dict[str, Any]:
    validate_write_body(resource, fields, creating=True)
    body = await client.post(resource.form_path, json=fields)
    warnings: list[str] = []
    if resource.known_keys and isinstance(body, dict):
        key = body.get("Key") or body.get("key")
        if key:
            persisted = await client.get(f"{resource.form_path}/{key}")
            form = persisted if isinstance(persisted, dict) else None
            warnings = diff_persisted(resource, fields, form)
    return {"body": body, "warnings": warnings}


async def _find_deposit_bank_account(client: ManagerClient) -> dict[str, Any] | None:
    body = await client.get(
        "/bank-and-cash-accounts",
        params={"skip": 0, "pageSize": 50},
    )
    if not isinstance(body, dict):
        return None
    items = body.get("bankAndCashAccounts") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("Key") or item.get("key")
        if not key:
            continue
        name = str(item.get("Name") or item.get("name") or item.get("text") or "").casefold()
        if "deposit" in name:
            return {**item, "Key": key}
    return None


async def issue_sales_invoice(
    client: ManagerClient,
    policy: WritePolicy,
    fields: dict[str, Any],
) -> dict[str, Any]:
    require_write_scopes(policy, "sales")
    out = await _post(client, WRITABLE["sales_invoices"], fields)
    return _envelope(
        "ok",
        keys={"sales_invoice": _body_key(out["body"])},
        body=out["body"],
        warnings=out["warnings"],
    )


async def issue_purchase_invoice(
    client: ManagerClient,
    policy: WritePolicy,
    fields: dict[str, Any],
) -> dict[str, Any]:
    require_write_scopes(policy, "purchases")
    out = await _post(client, WRITABLE["purchase_invoices"], fields)
    return _envelope(
        "ok",
        keys={"purchase_invoice": _body_key(out["body"])},
        body=out["body"],
        warnings=out["warnings"],
    )


async def issue_quote(
    client: ManagerClient,
    policy: WritePolicy,
    fields: dict[str, Any],
    *,
    purchase: bool = False,
) -> dict[str, Any]:
    require_write_scopes(policy, "quotes")
    resource = WRITABLE["purchase_quotes"] if purchase else WRITABLE["sales_quotes"]
    out = await _post(client, resource, fields)
    label = "purchase_quote" if purchase else "sales_quote"
    return _envelope(
        "ok", keys={label: _body_key(out["body"])}, body=out["body"], warnings=out["warnings"]
    )


async def convert_quote_to_invoice(
    client: ManagerClient,
    policy: WritePolicy,
    quote_key: str,
    *,
    purchase: bool = False,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_write_scopes(policy, "quotes", "sales" if not purchase else "purchases")
    quote_resource = WRITABLE["purchase_quotes"] if purchase else WRITABLE["sales_quotes"]
    invoice_resource = (
        WRITABLE["purchase_invoices"] if purchase else WRITABLE["sales_invoices"]
    )
    quote = await client.get(f"{quote_resource.form_path}/{quote_key}")
    if not isinstance(quote, dict):
        raise ValueError(f"Quote {quote_key} returned no form body.")
    fields = dict(quote)
    fields.pop("Key", None)
    if extra_fields:
        fields.update(extra_fields)
    out = await _post(client, invoice_resource, fields)
    label = "purchase_invoice" if purchase else "sales_invoice"
    return _envelope(
        "ok",
        keys={"quote": quote_key, label: _body_key(out["body"])},
        body=out["body"],
        warnings=out["warnings"],
    )


def _receipt_with_allocation(
    *,
    customer: str,
    bank_account: str,
    date: str,
    amount: float,
    invoice_key: str | None,
    reference: str | None,
    paid_by: int,
    description: str | None,
) -> dict[str, Any]:
    line: dict[str, Any] = {
        "Amount": amount,
        "AccountsReceivableCustomer": customer,
    }
    if invoice_key:
        line["AccountsReceivableSalesInvoice"] = invoice_key
    fields: dict[str, Any] = {
        "ReceivedIn": bank_account,
        "Customer": customer,
        "Date": date,
        "PaidBy": paid_by,
        "Lines": [line],
    }
    if reference:
        fields["Reference"] = reference
    if description:
        fields["Description"] = description
    return fields


async def record_customer_payment(
    client: ManagerClient,
    policy: WritePolicy,
    *,
    customer: str,
    bank_account: str,
    date: str,
    amount: float,
    invoice_key: str,
    reference: str | None = None,
    paid_by: int = 1,
    description: str | None = None,
) -> dict[str, Any]:
    require_write_scopes(policy, "banking")
    fields = _receipt_with_allocation(
        customer=customer,
        bank_account=bank_account,
        date=date,
        amount=amount,
        invoice_key=invoice_key,
        reference=reference,
        paid_by=paid_by,
        description=description,
    )
    try:
        out = await _post(client, WRITABLE["receipts"], fields)
    except Exception as exc:
        return _envelope(
            "partial",
            keys={},
            warnings=[str(exc)],
            next_steps=[
                "Fix the receipt body (clone get_record template) and call "
                "record_customer_payment again with the same allocation."
            ],
        )
    key = _body_key(out["body"])
    if not invoice_key:
        return _envelope(
            "partial",
            keys={"receipt": key},
            warnings=["No invoice_key supplied; receipt is unallocated."],
            next_steps=[
                "Call record_customer_payment again with invoice_key, or allocate "
                "manually in Manager."
            ],
            body=out["body"],
        )
    return _envelope(
        "ok",
        keys={"receipt": key, "invoice": invoice_key},
        body=out["body"],
        warnings=out["warnings"],
    )


async def record_supplier_payment(
    client: ManagerClient,
    policy: WritePolicy,
    *,
    supplier: str,
    bank_account: str,
    date: str,
    amount: float,
    invoice_key: str,
    reference: str | None = None,
    paid_by: int = 1,
    description: str | None = None,
) -> dict[str, Any]:
    require_write_scopes(policy, "banking")
    line: dict[str, Any] = {
        "Amount": amount,
        "AccountsPayableSupplier": supplier,
        "AccountsPayablePurchaseInvoice": invoice_key,
    }
    fields: dict[str, Any] = {
        "PaidFrom": bank_account,
        "Supplier": supplier,
        "Date": date,
        "PaidBy": paid_by,
        "Lines": [line],
    }
    if reference:
        fields["Reference"] = reference
    if description:
        fields["Description"] = description
    try:
        out = await _post(client, WRITABLE["payments"], fields)
    except Exception as exc:
        return _envelope(
            "partial",
            keys={},
            warnings=[str(exc)],
            next_steps=["Fix payment body and retry record_supplier_payment."],
        )
    return _envelope(
        "ok",
        keys={"payment": _body_key(out["body"]), "invoice": invoice_key},
        body=out["body"],
        warnings=out["warnings"],
    )


async def record_expense(
    client: ManagerClient,
    policy: WritePolicy,
    fields: dict[str, Any],
    *,
    via: str = "auto",
) -> dict[str, Any]:
    effective = policy.effective_write_scopes
    use_payroll = via == "expense_claim" or (
        via == "auto" and "payroll" in effective and "purchases" not in effective
    )
    if use_payroll and "payroll" in effective:
        require_write_scopes(policy, "payroll")
        resource = WRITABLE["expense_claims"]
        label = "expense_claim"
    elif "purchases" in effective:
        require_write_scopes(policy, "purchases")
        resource = WRITABLE["purchase_invoices"]
        label = "purchase_invoice"
    else:
        raise WritesDeniedError(
            "record_expense requires payroll and/or purchases in WRITE_SCOPES."
        )
    out = await _post(client, resource, fields)
    return _envelope(
        "ok", keys={label: _body_key(out["body"])}, body=out["body"], warnings=out["warnings"]
    )


async def transfer_between_accounts(
    client: ManagerClient,
    policy: WritePolicy,
    fields: dict[str, Any],
) -> dict[str, Any]:
    require_write_scopes(policy, "banking")
    out = await _post(client, WRITABLE["inter_account_transfers"], fields)
    return _envelope(
        "ok", keys={"transfer": _body_key(out["body"])}, body=out["body"], warnings=out["warnings"]
    )


async def post_journal_entry(
    client: ManagerClient,
    policy: WritePolicy,
    fields: dict[str, Any],
) -> dict[str, Any]:
    require_write_scopes(policy, "ledger")
    out = await _post(client, WRITABLE["journal_entries"], fields)
    return _envelope(
        "ok",
        keys={"journal_entry": _body_key(out["body"])},
        body=out["body"],
        warnings=out["warnings"],
    )


async def void_document(
    client: ManagerClient,
    policy: WritePolicy,
    resource: str,
    key: str,
) -> dict[str, Any]:
    w = WRITABLE.get(resource)
    if w is None or not w.implemented:
        raise ValueError(
            f"Unknown voidable resource {resource!r}. "
            f"Use a writable resource name (e.g. sales_invoices, receipts)."
        )
    require_delete_scope(policy, w.scope)
    path = f"{w.form_path}/{key}"
    body = await client.delete(path)
    return _envelope("ok", keys={resource: key}, body=body)


async def record_customer_deposit(
    client: ManagerClient,
    policy: WritePolicy,
    *,
    customer: str,
    amount: float,
    date: str,
    bank_account: str | None = None,
    reference: str | None = None,
    paid_by: int = 1,
    description: str | None = None,
) -> dict[str, Any]:
    require_write_scopes(policy, "banking")
    deposit_account = None
    if bank_account:
        deposit_account = bank_account
    else:
        found = await _find_deposit_bank_account(client)
        if found:
            deposit_account = str(found["Key"])
    if not deposit_account:
        result = PreconditionResult(
            ok=False,
            missing=(
                PreconditionItem(
                    name="deposit_bank_account",
                    why=(
                        "Customer deposits must post to a dedicated bank/cash holding "
                        "account so they are not booked as revenue."
                    ),
                    how_to_create=_DEPOSIT_ACCOUNT_GUIDE,
                ),
            ),
        )
        return precondition_failed_response(result)
    fields = _receipt_with_allocation(
        customer=customer,
        bank_account=deposit_account,
        date=date,
        amount=amount,
        invoice_key=None,
        reference=reference,
        paid_by=paid_by,
        description=description or "Customer deposit (not revenue)",
    )
    out = await _post(client, WRITABLE["receipts"], fields)
    return _envelope(
        "ok",
        keys={"receipt": _body_key(out["body"]), "deposit_account": deposit_account},
        body=out["body"],
        warnings=out["warnings"],
        next_steps=[
            "Deposit recorded. When the final invoice exists, call "
            "apply_deposit_to_invoice or allocate via journal entry."
        ],
    )


async def issue_deposit_invoice(
    client: ManagerClient,
    policy: WritePolicy,
    fields: dict[str, Any],
) -> dict[str, Any]:
    require_write_scopes(policy, "quotes")
    body = dict(fields)
    desc = str(body.get("Description") or "")
    if "deposit" not in desc.casefold():
        body["Description"] = (desc + " - Deposit invoice (not revenue)").strip(" -")
    out = await _post(client, WRITABLE["sales_quotes"], body)
    return _envelope(
        "ok",
        keys={"sales_quote": _body_key(out["body"])},
        body=out["body"],
        warnings=out["warnings"],
        next_steps=[
            "This is a quote styled as a deposit invoice, not a sales invoice. "
            "Call record_customer_deposit when cash is received."
        ],
    )


async def apply_deposit_to_invoice(
    client: ManagerClient,
    policy: WritePolicy,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Post a journal entry moving deposit balance to the sales invoice / AR."""
    require_write_scopes(policy, "ledger")
    out = await _post(client, WRITABLE["journal_entries"], fields)
    return _envelope(
        "ok",
        keys={"journal_entry": _body_key(out["body"])},
        body=out["body"],
        warnings=out["warnings"],
        next_steps=["Verify invoice balance and customer available credit with reads."],
    )

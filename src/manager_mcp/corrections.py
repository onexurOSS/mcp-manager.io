"""Two-phase (propose/apply) corrective operations.

These reuse the existing write scopes, field validation, and persistence
verification -- there is no parallel permission system. What's new is:

- a dry-run layer (propose_correction / apply_correction) in front of the
  existing create/update path, so a caller can see the exact before/after
  diff before anything is written to Manager;
- reallocate_payment_line / reallocate_receipt_line, which repoint one
  existing line's AR/AP invoice reference without touching anything else
  on the record;
- the missing-invoice reconstruction workflow, which is deliberately
  unable to invent a per-line breakdown it cannot derive from structured
  data -- it reports 'unresolved' with the exact evidence gap instead;
- snapshot_and_void, which never deletes a "genuine but unexplained"
  transaction on its own judgment -- it requires an explicit human
  justification and refuses if anything else still references the record.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx

from manager_mcp.audit_log import AuditEntry, new_correlation_id, record_event
from manager_mcp.client import ManagerClient
from manager_mcp.diagnostics import (
    _first,
    _key_of,
    _line_amount,
    _lines_of,
    find_transactions_referencing_invoice,
    verify_invoice_balance,
)
from manager_mcp.resources import form_path
from manager_mcp.scopes import WritePolicy
from manager_mcp.task_tools import require_delete_scope, require_write_scopes
from manager_mcp.writable import WRITABLE
from manager_mcp.write_validate import diff_persisted, validate_write_body

# invoice_resource, write-time field (what task_tools writes on create/update),
# all read-time aliases (what real historical data may carry instead --
# verified live: payments can read back with a plain `PurchaseInvoice` key
# even though the write path uses `AccountsPayablePurchaseInvoice`).
_ALLOCATION: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "payments": (
        "purchase_invoices",
        "AccountsPayablePurchaseInvoice",
        ("PurchaseInvoice", "AccountsPayablePurchaseInvoice"),
    ),
    "receipts": (
        "sales_invoices",
        "AccountsReceivableSalesInvoice",
        ("AccountsReceivableSalesInvoice", "SalesInvoice"),
    ),
}


def _proposal_token(resource: str, key: str | None, fields: dict[str, Any]) -> str:
    payload = json.dumps(
        {"resource": resource, "key": key, "fields": fields}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def _get_or_404(client: ManagerClient, path: str, *, not_found_message: str) -> Any:
    try:
        return await client.get(path)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ValueError(not_found_message) from exc
        raise


# --------------------------------------------------------------------------
# Generic propose / apply
# --------------------------------------------------------------------------


async def propose_correction(
    client: ManagerClient,
    policy: WritePolicy,
    resource: str,
    fields: dict[str, Any],
    *,
    key: str | None = None,
) -> dict[str, Any]:
    """Stage a create (key=None) or update (key=given) without writing.

    Runs the same field validation the real write would run and fetches
    the current before-state when updating. Returns a proposal_token that
    apply_correction must be called with, echoing the same (resource,
    key, fields) -- this is the dry-run layer.
    """
    w = WRITABLE.get(resource)
    if w is None or not w.implemented:
        raise ValueError(f"Unknown writable resource '{resource}'.")
    require_write_scopes(policy, w.scope)
    validate_write_body(w, fields, creating=key is None)
    before = None
    if key is not None:
        path = form_path(resource, key)
        if path is None:
            raise ValueError(f"Cannot resolve form path for {resource}/{key}.")
        before = await _get_or_404(
            client, path, not_found_message=f"{resource}/{key} not found; cannot propose an update."
        )
    return {
        "status": "proposed",
        "proposal_token": _proposal_token(resource, key, fields),
        "operation": "update" if key is not None else "create",
        "resource": resource,
        "key": key,
        "before": before,
        "proposed_fields": fields,
    }


async def apply_correction(
    client: ManagerClient,
    policy: WritePolicy,
    proposal_token: str,
    resource: str,
    fields: dict[str, Any],
    *,
    key: str | None = None,
) -> dict[str, Any]:
    """Commit a create/update previously staged by propose_correction.

    Refuses to run unless proposal_token matches the exact (resource,
    key, fields) -- this guards against applying a correction against a
    body that drifted since it was proposed.
    """
    w = WRITABLE.get(resource)
    if w is None or not w.implemented:
        raise ValueError(f"Unknown writable resource '{resource}'.")
    require_write_scopes(policy, w.scope)
    if proposal_token != _proposal_token(resource, key, fields):
        raise ValueError(
            "proposal_token does not match (resource, key, fields). Call "
            "propose_correction again with the exact fields you intend to apply."
        )
    validate_write_body(w, fields, creating=key is None)

    before = None
    if key is not None:
        path = form_path(resource, key)
        if path is None:
            raise ValueError(f"Cannot resolve form path for {resource}/{key}.")
        before = await _get_or_404(
            client, path, not_found_message=f"{resource}/{key} not found; cannot apply update."
        )
        body = await client.put(path, json=fields)
    else:
        body = await client.post(w.form_path, json=fields)

    after = body
    warnings: list[str] = []
    result_key = key
    if isinstance(body, dict):
        result_key = body.get("Key") or body.get("key") or key
        if w.known_keys and result_key:
            verify_path = form_path(resource, str(result_key))
            if verify_path:
                after = await client.get(verify_path)
                warnings = diff_persisted(w, fields, after if isinstance(after, dict) else None)

    correlation_id = new_correlation_id()
    record_event(
        AuditEntry(
            operation="update" if key is not None else "create",
            resource=resource,
            key=str(result_key) if result_key else None,
            before=before,
            submitted=fields,
            after=after,
            warnings=warnings,
            correlation_id=correlation_id,
            status="ok",
        )
    )
    return {
        "status": "ok",
        "correlation_id": correlation_id,
        "resource": resource,
        "key": result_key,
        "before": before,
        "after": after,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# Reallocation
# --------------------------------------------------------------------------


async def _reallocate(
    client: ManagerClient,
    policy: WritePolicy,
    resource: str,
    key: str,
    line_index: int,
    new_invoice_key: str,
) -> dict[str, Any]:
    invoice_resource, write_field, read_aliases = _ALLOCATION[resource]
    w = WRITABLE[resource]
    require_write_scopes(policy, w.scope)

    invoice_path = form_path(invoice_resource, new_invoice_key)
    if invoice_path is None:
        raise ValueError(f"Cannot resolve {invoice_resource}/{new_invoice_key}.")
    await _get_or_404(
        client,
        invoice_path,
        not_found_message=(
            f"{invoice_resource}/{new_invoice_key} does not exist; refusing to "
            "reallocate onto a non-existent invoice."
        ),
    )

    path = form_path(resource, key)
    if path is None:
        raise ValueError(f"Cannot resolve {resource}/{key}.")
    before = await _get_or_404(client, path, not_found_message=f"{resource}/{key} not found.")
    if not isinstance(before, dict):
        raise ValueError(f"{resource}/{key} returned no form body.")
    lines = _lines_of(before)
    if line_index < 0 or line_index >= len(lines):
        raise ValueError(
            f"{resource}/{key} has {len(lines)} line(s); index {line_index} is out of range."
        )

    fields = {k: v for k, v in before.items() if k not in ("Key", "id", "text")}
    new_lines = [dict(line) for line in lines]
    target_line = new_lines[line_index]
    old_value = _first(target_line, read_aliases)
    # Clear every alias this line might carry (e.g. a stale `PurchaseInvoice`
    # from old data) before setting the canonical write-time field, so the
    # line never ends up with two conflicting invoice references.
    for alias in read_aliases:
        target_line.pop(alias, None)
    target_line[write_field] = new_invoice_key
    fields["Lines"] = new_lines

    validate_write_body(w, fields, creating=False)
    await client.put(path, json=fields)
    after = await client.get(path)
    warnings = diff_persisted(w, fields, after if isinstance(after, dict) else None)

    correlation_id = new_correlation_id()
    record_event(
        AuditEntry(
            operation="reallocate",
            resource=resource,
            key=key,
            before=before,
            submitted={
                "line_index": line_index,
                write_field: {"from": old_value, "to": new_invoice_key},
            },
            after=after,
            warnings=warnings,
            correlation_id=correlation_id,
            status="ok",
        )
    )
    return {
        "status": "ok",
        "correlation_id": correlation_id,
        "resource": resource,
        "key": key,
        "line_index": line_index,
        "reallocated_from": old_value,
        "reallocated_to": new_invoice_key,
        "after": after,
        "warnings": warnings,
    }


async def reallocate_payment_line(
    client: ManagerClient,
    policy: WritePolicy,
    key: str,
    line_index: int,
    new_purchase_invoice_key: str,
) -> dict[str, Any]:
    return await _reallocate(client, policy, "payments", key, line_index, new_purchase_invoice_key)


async def reallocate_receipt_line(
    client: ManagerClient,
    policy: WritePolicy,
    key: str,
    line_index: int,
    new_sales_invoice_key: str,
) -> dict[str, Any]:
    return await _reallocate(client, policy, "receipts", key, line_index, new_sales_invoice_key)


# --------------------------------------------------------------------------
# Missing-invoice reconstruction (missing-invoice payment workflow)
# --------------------------------------------------------------------------


async def _propose_invoice_reconstruction(
    client: ManagerClient,
    policy: WritePolicy,
    txn_resource: str,
    txn_keys: list[str],
    *,
    lines: list[dict[str, Any]] | None,
    date: str | None,
    reference: str | None,
) -> dict[str, Any]:
    if not txn_keys:
        raise ValueError("At least one payment/receipt key is required.")
    invoice_resource, write_field, invoice_aliases = _ALLOCATION[txn_resource]
    party_line_aliases = (
        ("AccountsPayableSupplier",)
        if txn_resource == "payments"
        else ("AccountsReceivableCustomer",)
    )
    party_header_field = "Supplier" if txn_resource == "payments" else "Customer"
    invoice_w = WRITABLE[invoice_resource]
    require_write_scopes(policy, invoice_w.scope)

    party_values: set[str] = set()
    broken_invoice_keys: set[str] = set()
    matched_total = 0.0
    matched: list[dict[str, Any]] = []
    for txn_key in txn_keys:
        path = form_path(txn_resource, txn_key)
        if path is None:
            raise ValueError(f"Cannot resolve {txn_resource}/{txn_key}.")
        txn = await _get_or_404(
            client, path, not_found_message=f"{txn_resource}/{txn_key} not found."
        )
        if not isinstance(txn, dict):
            raise ValueError(f"{txn_resource}/{txn_key} returned no form body.")
        header_party = txn.get(party_header_field)
        found_line = False
        for idx, line in enumerate(_lines_of(txn)):
            ref = _first(line, invoice_aliases)
            if not ref:
                continue
            broken_invoice_keys.add(str(ref))
            # Prefer the line-level party reference over the header field --
            # more specific to this exact allocation (source-of-truth
            # priority: structured Lines over structured header).
            party_values.add(str(_first(line, party_line_aliases) or header_party or ""))
            amount = _line_amount(line)
            matched_total += amount or 0.0
            matched.append({"key": txn_key, "line_index": idx, "amount": amount})
            found_line = True
        if not found_line:
            return {
                "status": "unresolved",
                "reason": (
                    f"{txn_resource}/{txn_key} has no {'/'.join(invoice_aliases)} line "
                    "reference; nothing to reconstruct against."
                ),
                "evidence_required": [f"one of {invoice_aliases} on at least one line"],
            }

    if len(party_values - {""}) != 1:
        return {
            "status": "unresolved",
            "reason": (
                "Cited transactions do not share a single structured party "
                "(Supplier/Customer). Refusing to guess which one owns the "
                "missing invoice."
            ),
            "party_values_seen": sorted(party_values),
        }
    if len(broken_invoice_keys) != 1:
        return {
            "status": "unresolved",
            "reason": (
                "Cited transactions reference more than one distinct invoice "
                "Key; they are not all evidence for the same missing invoice."
            ),
            "invoice_keys_seen": sorted(broken_invoice_keys),
        }
    missing_key = next(iter(broken_invoice_keys))
    invoice_path = form_path(invoice_resource, missing_key)
    if invoice_path is not None:
        try:
            await client.get(invoice_path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
        else:
            return {
                "status": "unresolved",
                "reason": (
                    f"{invoice_resource}/{missing_key} already exists -- it is not "
                    "missing. Use reallocate_payment_line / reallocate_receipt_line "
                    "instead if the linkage itself is wrong."
                ),
            }

    party = next(iter(party_values))
    matched_total = round(matched_total, 2)

    if not lines:
        return {
            "status": "unresolved",
            "reason": (
                "The per-line breakdown of a missing invoice cannot be derived "
                "from payment/receipt totals alone. Structural evidence "
                f"establishes only the party ({party}), the missing invoice Key "
                f"({missing_key}), and the total owed ({matched_total}). Supply "
                "the actual invoice line items (from the source document) via "
                "the 'lines' argument to proceed -- this tool will not invent them."
            ),
            "party": party,
            "missing_invoice_key": missing_key,
            "total_from_transactions": matched_total,
            "contributing": matched,
        }

    lines_total = round(
        sum(_line_amount(line) or 0.0 for line in lines if isinstance(line, dict)), 2
    )
    if lines_total != matched_total:
        return {
            "status": "unresolved",
            "reason": (
                f"Supplied lines total {lines_total} but the cited transactions "
                f"total {matched_total}. Refusing to create an invoice that "
                "doesn't reconcile to the payments/receipts citing it."
            ),
            "lines_total": lines_total,
            "total_from_transactions": matched_total,
        }

    fields: dict[str, Any] = {party_header_field: party, "Lines": lines}
    date_field = "IssueDate" if invoice_resource == "sales_invoices" else "Date"
    if date:
        fields[date_field] = date
    if reference:
        fields["Reference"] = reference

    proposal = await propose_correction(client, policy, invoice_resource, fields, key=None)
    proposal["missing_invoice_key"] = missing_key
    proposal["contributing_transactions"] = matched
    proposal["party"] = party
    return proposal


async def propose_purchase_invoice_reconstruction(
    client: ManagerClient,
    policy: WritePolicy,
    payment_keys: list[str],
    *,
    lines: list[dict[str, Any]] | None = None,
    date: str | None = None,
    reference: str | None = None,
) -> dict[str, Any]:
    return await _propose_invoice_reconstruction(
        client,
        policy,
        "payments",
        payment_keys,
        lines=lines,
        date=date,
        reference=reference,
    )


async def propose_sales_invoice_reconstruction(
    client: ManagerClient,
    policy: WritePolicy,
    receipt_keys: list[str],
    *,
    lines: list[dict[str, Any]] | None = None,
    date: str | None = None,
    reference: str | None = None,
) -> dict[str, Any]:
    return await _propose_invoice_reconstruction(
        client,
        policy,
        "receipts",
        receipt_keys,
        lines=lines,
        date=date,
        reference=reference,
    )


async def _apply_invoice_reconstruction(
    client: ManagerClient,
    policy: WritePolicy,
    invoice_resource: str,
    proposal_token: str,
    fields: dict[str, Any],
    txn_keys: list[str],
) -> dict[str, Any]:
    txn_resource = "payments" if invoice_resource == "purchase_invoices" else "receipts"
    _, _, invoice_aliases = _ALLOCATION[txn_resource]
    created = await apply_correction(
        client, policy, proposal_token, invoice_resource, fields, key=None
    )
    new_key = created["key"]
    if not new_key:
        return {
            "status": "partial",
            "reason": "Invoice create returned no Key.",
            "created": created,
        }

    reallocations: list[dict[str, Any]] = []
    for txn_key in txn_keys:
        path = form_path(txn_resource, txn_key)
        txn = await client.get(path) if path else None
        line_index = None
        if isinstance(txn, dict):
            for idx, line in enumerate(_lines_of(txn)):
                if _first(line, invoice_aliases):
                    line_index = idx
                    break
        if line_index is None:
            reallocations.append(
                {"key": txn_key, "status": "skipped", "reason": "no matching line found"}
            )
            continue
        detail = await _reallocate(client, policy, txn_resource, txn_key, line_index, str(new_key))
        reallocations.append({"key": txn_key, "status": "ok", "detail": detail})

    verification = await verify_invoice_balance(client, invoice_resource, str(new_key))
    return {
        "status": "ok" if all(r["status"] == "ok" for r in reallocations) else "partial",
        "invoice": created,
        "reallocations": reallocations,
        "verification": verification,
    }


async def apply_purchase_invoice_reconstruction(
    client: ManagerClient,
    policy: WritePolicy,
    proposal_token: str,
    fields: dict[str, Any],
    payment_keys: list[str],
) -> dict[str, Any]:
    return await _apply_invoice_reconstruction(
        client,
        policy,
        "purchase_invoices",
        proposal_token,
        fields,
        payment_keys,
    )


async def apply_sales_invoice_reconstruction(
    client: ManagerClient,
    policy: WritePolicy,
    proposal_token: str,
    fields: dict[str, Any],
    receipt_keys: list[str],
) -> dict[str, Any]:
    return await _apply_invoice_reconstruction(
        client,
        policy,
        "sales_invoices",
        proposal_token,
        fields,
        receipt_keys,
    )


# --------------------------------------------------------------------------
# Safe void
# --------------------------------------------------------------------------


async def snapshot_and_void(
    client: ManagerClient,
    policy: WritePolicy,
    resource: str,
    key: str,
    *,
    confirmed_duplicate_of: str | None = None,
) -> dict[str, Any]:
    """Void (hard delete) a document only after snapshotting its full
    before-state to the audit log and confirming no other transaction
    still references it via a structured AR/AP allocation line. Manager
    has no recycle bin -- this is the closest available safety net.

    Never deletes a record merely because it is unexplained: the caller
    must pass confirmed_duplicate_of as an explicit, human-reviewed
    justification (e.g. the Key it duplicates, or an approved exception
    ticket reference).
    """
    w = WRITABLE.get(resource)
    if w is None or not w.implemented:
        raise ValueError(f"Unknown voidable resource '{resource}'.")
    require_delete_scope(policy, w.scope)

    path = form_path(resource, key)
    if path is None:
        raise ValueError(f"Cannot resolve {resource}/{key}.")
    before = await _get_or_404(
        client, path, not_found_message=f"{resource}/{key} not found; nothing to void."
    )

    if resource in ("sales_invoices", "purchase_invoices"):
        allocation_resource = "receipts" if resource == "sales_invoices" else "payments"
        blockers = await find_transactions_referencing_invoice(client, allocation_resource, key)
        if blockers:
            return {
                "status": "blocked",
                "reason": (
                    f"{len(blockers)} {allocation_resource} still reference this "
                    f"{resource[:-1]}. Reallocate or void those first."
                ),
                "blocking_keys": [_key_of(b) for b in blockers],
            }

    if confirmed_duplicate_of is None:
        return {
            "status": "confirmation_required",
            "reason": (
                "snapshot_and_void requires confirmed_duplicate_of as an "
                "explicit, human-reviewed justification (the Key this record "
                "duplicates, or an approved exception-list reference). This "
                "tool does not delete a record merely because it is unexplained."
            ),
            "before": before,
        }

    await client.delete(path)
    correlation_id = new_correlation_id()
    record_event(
        AuditEntry(
            operation="void",
            resource=resource,
            key=key,
            before=before,
            submitted={"confirmed_duplicate_of": confirmed_duplicate_of},
            after=None,
            warnings=[],
            correlation_id=correlation_id,
            status="ok",
        )
    )
    return {
        "status": "ok",
        "correlation_id": correlation_id,
        "resource": resource,
        "key": key,
        "voided_snapshot": before,
    }

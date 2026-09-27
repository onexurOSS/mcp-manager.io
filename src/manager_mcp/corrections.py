# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
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
import sys as _sys
import time
from typing import Any

import httpx

from manager_mcp.audit_log import (
    AuditEntry,
    audit_log_path,
    new_correlation_id,
    read_events,
    record_event,
)
from manager_mcp.client import ManagerApiError, ManagerClient, ManagerUnavailableError
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


def _reconstruction_token(
    invoice_resource: str, fields: dict[str, Any], txn_keys: list[str]
) -> str:
    """Token for the invoice-reconstruction workflow, binding the invoice fields to
    the exact citing payment/receipt keys. Unlike _proposal_token, this must be
    recomputed and checked by _apply_invoice_reconstruction itself -- apply_correction
    only ever sees a plain token over (resource, fields), with no knowledge of
    txn_keys at all.
    """
    payload = json.dumps(
        {"resource": invoice_resource, "fields": fields, "txn_keys": sorted(txn_keys)},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def _invoice_exists(client: ManagerClient, invoice_resource: str, key: str) -> bool:
    """True if invoice_resource/key resolves to a real, already-persisted invoice."""
    path = form_path(invoice_resource, key)
    if path is None:
        return False
    try:
        await client.get(path)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return False
        raise
    return True


def _partial_reconstruction_message(new_key: str | None, original: Exception) -> str:
    """Message for a failure that happens after at least one write in this workflow
    has already reached Manager. This operation is resumable (see the pending/resume
    logic in _apply_invoice_reconstruction), so unlike an ordinary single-step write,
    the recommended action here is to call apply again with the same arguments, not
    to avoid it.
    """
    if new_key is None:
        where = "Creating the invoice for this reconstruction"
    else:
        where = f"Reallocating a citing transaction onto invoice {new_key!r} (already created)"
    return (
        f"{where} failed partway through this multi-step operation ({type(original).__name__}). "
        "This operation is resumable: call apply again with the exact same proposal_token, "
        "fields and transaction keys, and it will continue from what already completed rather "
        "than starting over or creating a duplicate invoice. Use "
        "list_incomplete_reconstructions to see what has been recorded so far, or check the "
        f"audit log ({audit_log_path()}) directly, or void the created invoice with "
        "snapshot_and_void if you want to abandon this reconstruction instead of resuming it. "
        "See the chained cause of this exception for the underlying detail."
    )


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
    correlation_id: str | None = None,
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

    result_key = key
    if isinstance(body, dict):
        result_key = body.get("Key") or body.get("key") or key

    # Committed now, before the verification read below -- so a failing read can never
    # leave this write unaudited. If the read succeeds, a second "verify" entry below
    # carries the richer, persistence-checked after/warnings under the same
    # correlation_id; this first entry's after/warnings reflect only the raw write
    # response.
    correlation_id = correlation_id or new_correlation_id()
    record_event(
        AuditEntry(
            operation="update" if key is not None else "create",
            resource=resource,
            key=str(result_key) if result_key else None,
            before=before,
            submitted=fields,
            after=body,
            warnings=[],
            correlation_id=correlation_id,
            status="ok",
        )
    )

    after = body
    warnings: list[str] = []
    if isinstance(body, dict) and w.known_keys and result_key:
        verify_path = form_path(resource, str(result_key))
        if verify_path:
            after = await client.get(verify_path)
            warnings = diff_persisted(w, fields, after if isinstance(after, dict) else None)
            record_event(
                AuditEntry(
                    operation="verify",
                    resource=resource,
                    key=str(result_key) if result_key else None,
                    before=None,
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
    *,
    correlation_id: str | None = None,
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

    # Committed now, before the verification read below -- see apply_correction for why.
    submitted = {
        "line_index": line_index,
        write_field: {"from": old_value, "to": new_invoice_key},
    }
    correlation_id = correlation_id or new_correlation_id()
    record_event(
        AuditEntry(
            operation="reallocate",
            resource=resource,
            key=key,
            before=before,
            submitted=submitted,
            after=None,
            warnings=[],
            correlation_id=correlation_id,
            status="ok",
        )
    )

    after = await client.get(path)
    warnings = diff_persisted(w, fields, after if isinstance(after, dict) else None)
    record_event(
        AuditEntry(
            operation="verify",
            resource=resource,
            key=key,
            before=None,
            submitted=submitted,
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
    # Binds the token to the exact citing transaction keys, not just the invoice fields --
    # apply_sales_invoice_reconstruction/apply_purchase_invoice_reconstruction verify this
    # before doing anything, so applying against a different set of transactions than what
    # was proposed is refused up front rather than silently accepted.
    proposal["proposal_token"] = _reconstruction_token(invoice_resource, fields, txn_keys)
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


async def _plan_reconstruction(
    client: ManagerClient,
    invoice_resource: str,
    txn_resource: str,
    invoice_aliases: tuple[str, ...],
    txn_keys: list[str],
) -> list[dict[str, Any]]:
    """One eligibility decision per cited transaction, decided up front and reused for
    the rest of the operation (including on resume) so nothing is fetched twice and a
    resumed attempt never re-derives a different plan than the one it started with.
    """
    plans: list[dict[str, Any]] = []
    for txn_key in txn_keys:
        path = form_path(txn_resource, txn_key)
        txn = await client.get(path) if path else None
        line_index = None
        current_ref: str | None = None
        if isinstance(txn, dict):
            for idx, line in enumerate(_lines_of(txn)):
                ref = _first(line, invoice_aliases)
                if ref:
                    line_index = idx
                    current_ref = str(ref)
                    break
        if line_index is None:
            plans.append(
                {"key": txn_key, "status": "skipped", "reason": "no matching line found"}
            )
            continue
        if current_ref is not None and await _invoice_exists(client, invoice_resource, current_ref):
            plans.append(
                {
                    "key": txn_key,
                    "status": "refused",
                    "reason": (
                        f"currently references {current_ref!r}, which already exists as "
                        f"an invoice, not a missing one; refusing to repoint it. Use "
                        "reallocate_payment_line/reallocate_receipt_line instead if this "
                        "linkage needs to change."
                    ),
                }
            )
            continue
        plans.append({"key": txn_key, "status": "pending", "line_index": line_index})
    return plans


def _reconstruction_trail(events: list[dict[str, Any]], token: str) -> list[dict[str, Any]]:
    return [e for e in events if e.get("correlation_id") == token]


async def _apply_invoice_reconstruction(
    client: ManagerClient,
    policy: WritePolicy,
    invoice_resource: str,
    proposal_token: str,
    fields: dict[str, Any],
    txn_keys: list[str],
) -> dict[str, Any]:
    """Idempotent and resumable: state is a sequence of audit-log entries sharing
    correlation_id == proposal_token, not a separate storage system. A "reconstruct"/
    "pending" entry records the plan at the start of a fresh attempt; the existing
    "create" entry (from apply_correction) marks the invoice as created; each
    existing "reallocate" entry marks one transaction as done; a final
    "reconstruct"/"complete" entry, holding the full result, marks the operation
    finished and is what a repeat call with the same token returns directly.

    See docs/write-path-spike.md for why this exists: without it, a retry after a
    partial failure created a second invoice for the same debt, and the only record
    of an orphaned invoice's key was a line in the audit log nothing ever read back.
    """
    txn_resource = "payments" if invoice_resource == "purchase_invoices" else "receipts"
    _, _, invoice_aliases = _ALLOCATION[txn_resource]

    # Fix 1: the token must match exactly what was proposed, including which
    # transactions it was proposed against -- nothing here has been written yet, so
    # a mismatch just means "propose again", not a partial-failure state.
    expected_token = _reconstruction_token(invoice_resource, fields, txn_keys)
    if proposal_token != expected_token:
        raise ValueError(
            "proposal_token does not match (resource, fields, payment/receipt keys). "
            "Call propose_purchase_invoice_reconstruction / "
            "propose_sales_invoice_reconstruction again with the exact fields and "
            "transaction keys you intend to apply."
        )

    trail = _reconstruction_trail(read_events(), proposal_token)
    complete_entry = next(
        (e for e in trail if e["operation"] == "reconstruct" and e["status"] == "complete"), None
    )
    if complete_entry is not None:
        # Idempotent replay: this exact operation already finished. Return the result
        # recorded at the time, with no new API calls -- this is the actual fix for a
        # retry creating a duplicate invoice, not just advice not to retry.
        return complete_entry["after"]

    pending_entry = next(
        (e for e in trail if e["operation"] == "reconstruct" and e["status"] == "pending"), None
    )
    if pending_entry is None:
        # A fresh attempt. Decide the plan now and record it before making any API
        # call that writes anything, so a crash right after this point still leaves
        # enough to resume from (though nothing to resume yet, since nothing was
        # written).
        plans = await _plan_reconstruction(
            client, invoice_resource, txn_resource, invoice_aliases, txn_keys
        )
        if not any(plan["status"] == "pending" for plan in plans):
            return {
                "status": "refused",
                "reason": (
                    "None of the cited transactions still reference a missing invoice; "
                    "there is nothing left to reconstruct. No invoice was created and "
                    "nothing was changed."
                ),
                "reallocations": plans,
            }
        record_event(
            AuditEntry(
                operation="reconstruct",
                resource=invoice_resource,
                key=None,
                before=None,
                submitted={"fields": fields, "txn_keys": sorted(txn_keys), "plans": plans},
                after=None,
                warnings=[],
                correlation_id=proposal_token,
                status="pending",
            )
        )
    else:
        # Resuming an incomplete attempt: trust the plan decided the first time
        # rather than re-deriving it. Re-checking eligibility now would be wrong for
        # a transaction this same operation already repointed in an earlier partial
        # attempt -- its line now points at the invoice this operation itself
        # created, which very much exists, and a fresh eligibility check would
        # wrongly read that as "already fixed by someone else" and refuse it.
        plans = pending_entry["submitted"]["plans"]

    created_entry = next((e for e in trail if e["operation"] == "create"), None)
    if created_entry is not None:
        new_key = created_entry["key"]
        created = {
            "status": "ok",
            "correlation_id": proposal_token,
            "resource": invoice_resource,
            "key": new_key,
            "before": created_entry["before"],
            "after": created_entry["after"],
            "warnings": created_entry["warnings"],
        }
    else:
        plain_token = _proposal_token(invoice_resource, None, fields)
        try:
            created = await apply_correction(
                client,
                policy,
                plain_token,
                invoice_resource,
                fields,
                key=None,
                correlation_id=proposal_token,
            )
        except (ManagerApiError, ManagerUnavailableError) as exc:
            raise type(exc)(_partial_reconstruction_message(None, exc)) from exc
        new_key = created["key"]
        if not new_key:
            return {
                "status": "partial",
                "reason": "Invoice create returned no Key.",
                "created": created,
            }

    done_keys = {e["key"] for e in trail if e["operation"] == "reallocate"}

    reallocations: list[dict[str, Any]] = []
    for plan in plans:
        if plan["status"] != "pending":
            reallocations.append(
                {"key": plan["key"], "status": plan["status"], "reason": plan["reason"]}
            )
            continue
        if plan["key"] in done_keys:
            # Already repointed in an earlier attempt at this same operation.
            reallocations.append(
                {
                    "key": plan["key"],
                    "status": "ok",
                    "detail": {"resumed_from_audit_log": True, "reallocated_to": new_key},
                }
            )
            continue
        try:
            detail = await _reallocate(
                client,
                policy,
                txn_resource,
                plan["key"],
                plan["line_index"],
                str(new_key),
                correlation_id=proposal_token,
            )
        except (ManagerApiError, ManagerUnavailableError) as exc:
            raise type(exc)(_partial_reconstruction_message(str(new_key), exc)) from exc
        reallocations.append({"key": plan["key"], "status": "ok", "detail": detail})

    verification = await verify_invoice_balance(client, invoice_resource, str(new_key))
    result = {
        "status": "ok" if all(r["status"] == "ok" for r in reallocations) else "partial",
        "invoice": created,
        "reallocations": reallocations,
        "verification": verification,
    }
    if result["status"] == "ok":
        record_event(
            AuditEntry(
                operation="reconstruct",
                resource=invoice_resource,
                key=new_key,
                before=None,
                submitted={"txn_keys": sorted(txn_keys)},
                after=result,
                warnings=[],
                correlation_id=proposal_token,
                status="complete",
            )
        )
    return result


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
# Listing incomplete reconstructions (read-only; local audit log only)
# --------------------------------------------------------------------------


def list_incomplete_reconstructions() -> dict[str, Any]:
    """Every invoice-reconstruction attempt with a "pending" audit entry and no
    matching "complete" one, from the local audit log only -- no Manager API call.

    This is the tool that replaces reading the audit log file on the server host by
    hand to find an orphaned invoice's key after a partial failure (see
    docs/write-path-spike.md). It reports the state _apply_invoice_reconstruction
    itself would resume from if called again with the same proposal_token.
    """
    events = read_events()
    by_token: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        correlation_id = event.get("correlation_id")
        if event.get("operation") == "reconstruct" and correlation_id:
            by_token.setdefault(correlation_id, []).append(event)
        elif event.get("operation") in ("create", "reallocate") and correlation_id:
            by_token.setdefault(correlation_id, []).append(event)

    now = time.time()
    incomplete: list[dict[str, Any]] = []
    for token, trail in by_token.items():
        pending = next(
            (e for e in trail if e["operation"] == "reconstruct" and e["status"] == "pending"),
            None,
        )
        if pending is None:
            continue  # a create/reallocate correlation_id that isn't a reconstruction at all
        if any(e["operation"] == "reconstruct" and e["status"] == "complete" for e in trail):
            continue
        created = next((e for e in trail if e["operation"] == "create"), None)
        done_keys = sorted({e["key"] for e in trail if e["operation"] == "reallocate"})
        plans = pending["submitted"].get("plans", [])
        pending_keys = sorted(
            p["key"] for p in plans if p["status"] == "pending" and p["key"] not in done_keys
        )
        incomplete.append(
            {
                "proposal_token": token,
                "resource": pending["resource"],
                "cited_transaction_keys": sorted(pending["submitted"].get("txn_keys", [])),
                "started_at": pending["timestamp"],
                "age_seconds": round(now - pending["timestamp"], 1),
                "created_invoice_key": created["key"] if created else None,
                "transactions_done": done_keys,
                "transactions_pending": pending_keys,
            }
        )
    incomplete.sort(key=lambda row: row["started_at"])
    return {"incomplete_reconstructions": incomplete, "count": len(incomplete)}


def register_reconstruction_listing_tool(mcp: Any) -> None:
    """Registered unconditionally, like the other read tools: it only reads the local
    audit log and makes no Manager API call, so it needs no scope.
    """
    _corr = _sys.modules[__name__]

    @mcp.tool(
        description=(
            "Invoice-reconstruction attempts (propose_*_invoice_reconstruction / "
            "apply_*_invoice_reconstruction) that started but have not completed: "
            "the created invoice's key if one exists, which cited transactions have "
            "been repointed, and which remain. Calling apply again with the same "
            "proposal_token resumes from exactly this state rather than starting "
            "over. Reads only the local audit log; makes no Manager API call."
        )
    )
    def list_incomplete_reconstructions() -> dict[str, Any]:
        return _corr.list_incomplete_reconstructions()


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


def register_correction_tools(
    mcp: Any, get_client: Any, get_policy: Any, write_annotations: Any
) -> None:
    """Register propose/apply correction, reallocation and reconstruction tools.

    Conditions and registration order are exactly those of the original inline code.
    """
    _corr = _sys.modules[__name__]
    policy = get_policy()
    effective = policy.effective_write_scopes

    if policy.any_enabled:

        @mcp.tool(
            name="propose_correction",
            description=(
                "Stage a create/update without writing to Manager (dry run). "
                "Runs the same validation a real write would run and returns "
                "a proposal_token that apply_correction must echo back."
            ),
            annotations=write_annotations,
        )
        async def propose_correction(
            resource: str,
            fields: dict[str, Any],
            key: str | None = None,
        ) -> dict[str, Any]:
            return await _corr.propose_correction(
                get_client(), get_policy(), resource, fields, key=key
            )

        @mcp.tool(
            name="apply_correction",
            description=(
                "Commit a create/update previously staged by propose_correction. "
                "proposal_token must match the exact (resource, key, fields) "
                "that were proposed."
            ),
            annotations=write_annotations,
        )
        async def apply_correction(
            proposal_token: str,
            resource: str,
            fields: dict[str, Any],
            key: str | None = None,
        ) -> dict[str, Any]:
            return await _corr.apply_correction(
                get_client(), get_policy(), proposal_token, resource, fields, key=key
            )

    if "banking" in effective:

        @mcp.tool(
            name="reallocate_payment_line",
            description=(
                "Repoint one existing payment line's AccountsPayablePurchaseInvoice "
                "to a different, verified-to-exist purchase invoice, leaving every "
                "other field untouched. Refuses if the target invoice does not "
                "exist. Requires banking scope."
            ),
            annotations=write_annotations,
        )
        async def reallocate_payment_line(
            key: str, line_index: int, new_purchase_invoice_key: str
        ) -> dict[str, Any]:
            return await _corr.reallocate_payment_line(
                get_client(), get_policy(), key, line_index, new_purchase_invoice_key
            )

        @mcp.tool(
            name="reallocate_receipt_line",
            description=(
                "Repoint one existing receipt line's AccountsReceivableSalesInvoice "
                "to a different, verified-to-exist sales invoice, leaving every "
                "other field untouched. Requires banking scope."
            ),
            annotations=write_annotations,
        )
        async def reallocate_receipt_line(
            key: str, line_index: int, new_sales_invoice_key: str
        ) -> dict[str, Any]:
            return await _corr.reallocate_receipt_line(
                get_client(), get_policy(), key, line_index, new_sales_invoice_key
            )

    if "purchases" in effective and "banking" in effective:

        @mcp.tool(
            name="propose_purchase_invoice_reconstruction",
            description=(
                "Investigate payments that structurally reference a missing "
                "purchase invoice (the missing-invoice payment pattern). Returns 'unresolved' "
                "with the exact evidence gap when the per-line breakdown can't "
                "be derived from the payments alone -- supply it via 'lines' "
                "once you have the real invoice document. Requires purchases "
                "and banking scopes."
            ),
            annotations=write_annotations,
        )
        async def propose_purchase_invoice_reconstruction(
            payment_keys: list[str],
            lines: list[dict[str, Any]] | None = None,
            date: str | None = None,
            reference: str | None = None,
        ) -> dict[str, Any]:
            return await _corr.propose_purchase_invoice_reconstruction(
                get_client(),
                get_policy(),
                payment_keys,
                lines=lines,
                date=date,
                reference=reference,
            )

        @mcp.tool(
            name="apply_purchase_invoice_reconstruction",
            description=(
                "Create the invoice proposed by propose_purchase_invoice_reconstruction, "
                "reallocate the citing payments onto it, and verify the resulting "
                "balance. Requires purchases and banking scopes."
            ),
            annotations=write_annotations,
        )
        async def apply_purchase_invoice_reconstruction(
            proposal_token: str,
            fields: dict[str, Any],
            payment_keys: list[str],
        ) -> dict[str, Any]:
            return await _corr.apply_purchase_invoice_reconstruction(
                get_client(), get_policy(), proposal_token, fields, payment_keys
            )

    if "sales" in effective and "banking" in effective:

        @mcp.tool(
            name="propose_sales_invoice_reconstruction",
            description=(
                "Same workflow as propose_purchase_invoice_reconstruction, for "
                "receipts referencing a missing sales invoice. Requires sales "
                "and banking scopes."
            ),
            annotations=write_annotations,
        )
        async def propose_sales_invoice_reconstruction(
            receipt_keys: list[str],
            lines: list[dict[str, Any]] | None = None,
            date: str | None = None,
            reference: str | None = None,
        ) -> dict[str, Any]:
            return await _corr.propose_sales_invoice_reconstruction(
                get_client(),
                get_policy(),
                receipt_keys,
                lines=lines,
                date=date,
                reference=reference,
            )

        @mcp.tool(
            name="apply_sales_invoice_reconstruction",
            description=(
                "Create the invoice proposed by propose_sales_invoice_reconstruction, "
                "reallocate the citing receipts onto it, and verify the resulting "
                "balance. Requires sales and banking scopes."
            ),
            annotations=write_annotations,
        )
        async def apply_sales_invoice_reconstruction(
            proposal_token: str,
            fields: dict[str, Any],
            receipt_keys: list[str],
        ) -> dict[str, Any]:
            return await _corr.apply_sales_invoice_reconstruction(
                get_client(), get_policy(), proposal_token, fields, receipt_keys
            )


def register_snapshot_and_void(
    mcp: Any, get_client: Any, get_policy: Any, delete_annotations: Any
) -> None:
    """Register the audited snapshot_and_void tool (delete scope)."""
    _corr = _sys.modules[__name__]

    @mcp.tool(
        name="snapshot_and_void",
        description=(
            "Void a document only after snapshotting its full before-state to "
            "the audit log and confirming nothing else still references it. "
            "Requires confirmed_duplicate_of as an explicit human-reviewed "
            "justification; refuses to delete an 'unexplained' record on its "
            "own. Prefer this over void_document for anything found by a "
            "diagnostic tool."
        ),
        annotations=delete_annotations,
    )
    async def snapshot_and_void(
        resource: str, key: str, confirmed_duplicate_of: str | None = None
    ) -> dict[str, Any]:
        return await _corr.snapshot_and_void(
            get_client(),
            get_policy(),
            resource,
            key,
            confirmed_duplicate_of=confirmed_duplicate_of,
        )

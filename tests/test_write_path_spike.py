# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Gate 4 write-path spike: partial failure in sales invoice reconstruction.

These are characterisation tests. They record what apply_sales_invoice_reconstruction
actually does today when a call in its sequence fails, against an in-memory fake Manager
that keeps real state (invoices and receipts) and can inject a failure at any point. They
assert the observed behaviour, including its gaps. If the workflow is later made safer,
expect several of these to need updating on purpose. The write-up is docs/write-path-spike.md.

The workflow is create then reallocate, not void then create: one POST creates the missing
sales invoice, then each citing receipt is repointed with a PUT.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from manager_mcp.audit_log import read_events
from manager_mcp.client import ManagerApiError, ManagerClient, ManagerUnavailableError
from manager_mcp.corrections import (
    _proposal_token,
    _reconstruction_token,
    apply_sales_invoice_reconstruction,
    propose_sales_invoice_reconstruction,
    reallocate_receipt_line,
    snapshot_and_void,
)
from manager_mcp.diagnostics import find_broken_invoice_references, verify_invoice_balance
from manager_mcp.scopes import WritePolicy

BASE = "http://example.test/api2"
MISSING = "missing-inv-key"
CUSTOMER = "example-customer-key"
LINES = [
    {"Account": "sales-account", "Amount": 380.00},
    {"Account": "sales-account", "Amount": 35.00},
]

# The full request sequence apply makes for two receipts, as observed (18 calls, 3 writes).
# The eligibility check (Fix 1: does the receipt's current reference already resolve to a
# real invoice?) now runs for every receipt BEFORE the invoice is created (the orphan fix),
# so both "GET .../missing-inv-key" checks happen up front, ahead of the POST.
EXPECTED_HAPPY_PATH_CALLS: list[str] = [
    "GET /receipt-form/rc-a",
    "GET /sales-invoice-form/missing-inv-key",
    "GET /receipt-form/rc-b",
    "GET /sales-invoice-form/missing-inv-key",
    "POST /sales-invoice-form",
    "GET /sales-invoice-form/new-inv-1",
    "GET /receipt-form/rc-a",
    "PUT /receipt-form/rc-a",
    "GET /receipt-form/rc-a",
    "GET /sales-invoice-form/new-inv-1",
    "GET /receipt-form/rc-b",
    "PUT /receipt-form/rc-b",
    "GET /receipt-form/rc-b",
    "GET /sales-invoice-form/new-inv-1",
    "GET /sales-invoices",
    "GET /receipts",
    "GET /receipt-form/rc-a",
    "GET /receipt-form/rc-b",
]

FailRule = tuple[Callable[["FakeManager", str, str], bool], "httpx.Response | Exception"]


def _receipt(key: str, amount: float, invoice: str = MISSING) -> dict[str, Any]:
    return {
        "Key": key,
        "ReceivedIn": "bank-1",
        "Customer": CUSTOMER,
        "Date": "2025-03-15",
        "Lines": [
            {
                "Amount": amount,
                "AccountsReceivableCustomer": CUSTOMER,
                "AccountsReceivableSalesInvoice": invoice,
            }
        ],
    }


class FakeManager:
    """A small stateful Manager: receipts, sales invoices, and injectable failures."""

    def __init__(self) -> None:
        self.receipts: dict[str, dict[str, Any]] = {
            "rc-a": _receipt("rc-a", 380.00),
            "rc-b": _receipt("rc-b", 35.00),
        }
        self.invoices: dict[str, dict[str, Any]] = {}
        self.log: list[tuple[str, str]] = []
        self.fail: list[FailRule] = []
        self._next = 0

    # -- helpers -----------------------------------------------------------------------
    def writes(self) -> list[str]:
        return [f"{m} {p}" for m, p in self.log if m in ("POST", "PUT", "DELETE")]

    def references(self, invoice_key: str) -> list[str]:
        return sorted(
            k
            for k, r in self.receipts.items()
            if any(line.get("AccountsReceivableSalesInvoice") == invoice_key for line in r["Lines"])
        )

    def state(self) -> dict[str, Any]:
        return {
            "invoices": sorted(self.invoices),
            "receipt_targets": {
                k: [line.get("AccountsReceivableSalesInvoice") for line in r["Lines"]]
                for k, r in sorted(self.receipts.items())
            },
        }

    def _invoice_row(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        total = round(sum(line["Amount"] for line in body["Lines"]), 2)
        allocated = round(
            sum(
                line["Amount"]
                for r in self.receipts.values()
                for line in r["Lines"]
                if line.get("AccountsReceivableSalesInvoice") == key
            ),
            2,
        )
        return {
            "key": key,
            "invoiceAmount": {"value": total},
            "balanceDue": {"value": round(total - allocated, 2)},
        }

    # -- the HTTP surface --------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.method.upper()
        path = request.url.path.removeprefix("/api2")
        for predicate, outcome in self.fail:
            if predicate(self, method, path):
                self.log.append((method, path))
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        self.log.append((method, path))

        if method == "GET" and path == "/receipts":
            rows = [{"key": k} for k in self.receipts]
            return httpx.Response(200, json={"totalRecords": len(rows), "receipts": rows})
        if path.startswith("/receipt-form/"):
            key = path.rsplit("/", 1)[-1]
            if key not in self.receipts:
                return httpx.Response(404, json={})
            if method == "GET":
                return httpx.Response(200, json=self.receipts[key])
            if method == "PUT":
                body = json.loads(request.content)
                body["Key"] = key
                self.receipts[key] = body
                return httpx.Response(200, json=body)
        if method == "GET" and path == "/sales-invoices":
            rows = [self._invoice_row(k, b) for k, b in self.invoices.items()]
            return httpx.Response(200, json={"totalRecords": len(rows), "salesInvoices": rows})
        if path == "/sales-invoice-form" and method == "POST":
            self._next += 1
            key = f"new-inv-{self._next}"
            body = {**json.loads(request.content), "Key": key}
            self.invoices[key] = body
            return httpx.Response(201, json=body)
        if path.startswith("/sales-invoice-form/"):
            key = path.rsplit("/", 1)[-1]
            if method == "GET":
                if key in self.invoices:
                    return httpx.Response(200, json=self.invoices[key])
                return httpx.Response(404, json={})
            if method == "DELETE":
                self.invoices.pop(key, None)
                return httpx.Response(204)
        raise AssertionError(f"unmodelled request: {method} {path}")


def _rule(method: str, path: str, *, when: Callable[[FakeManager], bool] | None = None):
    def predicate(fake: FakeManager, m: str, p: str) -> bool:
        return m == method and re.fullmatch(path, p) is not None and (when is None or when(fake))

    return predicate


@pytest.fixture(autouse=True)
def _audit_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("MANAGER_MCP_AUDIT_LOG_PATH", str(log))
    return log


@pytest.fixture
def fake():
    manager = FakeManager()
    with respx.mock:
        respx.route(url__regex=rf"^{re.escape(BASE)}/.*").mock(side_effect=manager.handler)
        yield manager


def _client(*, delete: frozenset[str] = frozenset()) -> ManagerClient:
    return ManagerClient(
        BASE, "test-key", policy=WritePolicy(frozenset({"sales", "banking"}), delete)
    )


async def _propose(client: ManagerClient, keys: list[str] | None = None) -> dict[str, Any]:
    proposal = await propose_sales_invoice_reconstruction(
        client,
        client.policy,
        keys or ["rc-a", "rc-b"],
        lines=LINES,
        date="2025-02-10",
        reference="INV-EXAMPLE-0001",
    )
    assert proposal["status"] == "proposed"
    return proposal


async def _apply(
    client: ManagerClient, proposal: dict[str, Any], keys: list[str]
) -> dict[str, Any]:
    return await apply_sales_invoice_reconstruction(
        client, client.policy, proposal["proposal_token"], proposal["proposed_fields"], keys
    )


# ---------------------------------------------------------------------------------------
# Step 1 and 2: the chain and the exact call sequence
# ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_makes_no_writes_and_apply_is_a_separate_call(fake: FakeManager) -> None:
    client = _client()
    before = fake.state()
    await _propose(client)
    assert fake.writes() == []
    assert fake.state() == before
    await client.aclose()


@pytest.mark.asyncio
async def test_happy_path_call_sequence_is_one_post_then_one_put_per_receipt(
    fake: FakeManager, _audit_isolation: Path
) -> None:
    client = _client()
    proposal = await _propose(client)
    fake.log.clear()
    result = await _apply(client, proposal, ["rc-a", "rc-b"])
    await client.aclose()

    assert result["status"] == "ok"
    # Three independent writes, no transaction: create, then repoint each receipt.
    assert fake.writes() == [
        "POST /sales-invoice-form",
        "PUT /receipt-form/rc-a",
        "PUT /receipt-form/rc-b",
    ]
    counts = Counter(f"{m} {p}" for m, p in fake.log)
    assert counts["POST /sales-invoice-form"] == 1
    assert counts["PUT /receipt-form/rc-a"] == 1
    # Total HTTP calls inside apply for two receipts (writes plus reads plus verification).
    assert [f"{m} {p}" for m, p in fake.log] == EXPECTED_HAPPY_PATH_CALLS
    assert result["verification"]["fully_paid"] is True
    # Fix 3: create, then one "reallocate" (committed before the read) plus a "verify"
    # (committed after the read, sharing the same correlation_id) per receipt.
    events = read_events(_audit_isolation)
    assert [e["operation"] for e in events] == [
        "create",
        "reallocate",
        "verify",
        "reallocate",
        "verify",
    ]
    ids = [e["correlation_id"] for e in events]
    assert ids[1] == ids[2] and ids[3] == ids[4] and len({ids[0], ids[1], ids[3]}) == 3


@pytest.mark.asyncio
async def test_apply_refuses_a_token_that_was_not_computed_over_the_receipt_keys(
    fake: FakeManager,
) -> None:
    """Fix 1: a plain, pre-binding-style token (no receipt keys in its hash input) is
    refused up front, before any write, rather than accepted the way it used to be."""
    client = _client()
    proposal = await _propose(client)
    stale_token = _proposal_token("sales_invoices", None, proposal["proposed_fields"])

    with pytest.raises(ValueError, match="does not match"):
        await _apply(client, {**proposal, "proposal_token": stale_token}, ["rc-a", "rc-b"])

    assert fake.writes() == []
    await client.aclose()


@pytest.mark.asyncio
async def test_apply_refuses_the_whole_operation_when_every_transaction_is_already_fixed(
    fake: FakeManager,
) -> None:
    """Fix 1 plus the orphan fix: a correctly-computed token (bound to the right
    resource, fields and receipt keys) is not enough on its own -- apply also checks,
    live, that each receipt still references a genuinely missing invoice before
    repointing it, and does this BEFORE creating anything. A receipt that was never
    missing one (or was already fixed) is refused, and if every cited receipt turns out
    that way, no invoice is created at all: there is nothing left to reconstruct.
    """
    client = _client()
    fields = {
        "Customer": CUSTOMER,
        "Lines": LINES,
        "IssueDate": "2025-02-10",
        "Reference": "INV-EXAMPLE-0001",
    }
    # A receipt that points at a perfectly good, different, already-existing invoice.
    fake.invoices["inv-existing"] = {"Key": "inv-existing", "Lines": [{"Amount": 50.0}]}
    fake.receipts["rc-other"] = _receipt("rc-other", 50.0, invoice="inv-existing")
    token = _reconstruction_token("sales_invoices", fields, ["rc-other"])  # validly computed

    result = await apply_sales_invoice_reconstruction(
        client, client.policy, token, fields, ["rc-other"]
    )
    await client.aclose()

    assert result["status"] == "refused"
    assert "nothing left to reconstruct" in result["reason"]
    assert result["reallocations"] == [
        {
            "key": "rc-other",
            "status": "refused",
            "reason": (
                "currently references 'inv-existing', which already exists as an "
                "invoice, not a missing one; refusing to repoint it. Use "
                "reallocate_payment_line/reallocate_receipt_line instead if this "
                "linkage needs to change."
            ),
        }
    ]
    assert fake.references("inv-existing") == ["rc-other"]  # untouched
    assert fake.writes() == []  # no invoice created, nothing else changed
    assert list(fake.invoices) == ["inv-existing"]


@pytest.mark.asyncio
async def test_apply_refuses_the_whole_operation_when_every_cited_transaction_is_fixed(
    fake: FakeManager,
) -> None:
    """Same as above, generalised to more than one transaction: if ALL of them are
    already fixed by the time apply runs, nothing is created and nothing is touched,
    rather than creating an orphan invoice with zero transactions ever pointing at it.
    """
    client = _client()
    fields = {
        "Customer": CUSTOMER,
        "Lines": LINES,
        "IssueDate": "2025-02-10",
        "Reference": "INV-EXAMPLE-0001",
    }
    fake.invoices["inv-existing"] = {"Key": "inv-existing", "Lines": [{"Amount": 415.0}]}
    fake.receipts["rc-a"] = _receipt("rc-a", 380.00, invoice="inv-existing")
    fake.receipts["rc-b"] = _receipt("rc-b", 35.00, invoice="inv-existing")
    token = _reconstruction_token("sales_invoices", fields, ["rc-a", "rc-b"])

    result = await apply_sales_invoice_reconstruction(
        client, client.policy, token, fields, ["rc-a", "rc-b"]
    )
    await client.aclose()

    assert result["status"] == "refused"
    assert "nothing left to reconstruct" in result["reason"]
    assert {r["key"] for r in result["reallocations"]} == {"rc-a", "rc-b"}
    assert all(r["status"] == "refused" for r in result["reallocations"])
    assert fake.writes() == []
    assert list(fake.invoices) == ["inv-existing"]
    assert fake.references("inv-existing") == ["rc-a", "rc-b"]


# ---------------------------------------------------------------------------------------
# Step 3: first call succeeds, second fails
# ---------------------------------------------------------------------------------------

_FIRST_PUT_FAILURES = [
    ("http_500", httpx.Response(500, text="internal error"), ManagerApiError),
    ("connect_error", httpx.ConnectError("refused"), ManagerUnavailableError),
    ("timeout", httpx.ReadTimeout("slow"), ManagerUnavailableError),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("label", "outcome", "expected"), _FIRST_PUT_FAILURES)
async def test_failure_on_first_receipt_update_leaves_an_orphan_invoice(
    label: str,
    outcome: httpx.Response | Exception,
    expected: type[Exception],
    fake: FakeManager,
    _audit_isolation: Path,
) -> None:
    client = _client()
    proposal = await _propose(client)
    fake.fail.append((_rule("PUT", r"/receipt-form/rc-a"), outcome))

    with pytest.raises(expected) as excinfo:
        await _apply(client, proposal, ["rc-a", "rc-b"])

    # What Manager is left in: the invoice exists, no receipt points at it.
    assert list(fake.invoices) == ["new-inv-1"]
    assert fake.references("new-inv-1") == []
    assert fake.references(MISSING) == ["rc-a", "rc-b"]
    # Fix 2: the caller is told the created invoice's key and told not to retry, instead
    # of being given no key and advice that (before the fix) led straight to a duplicate.
    message = str(excinfo.value)
    assert "new-inv-1" in message
    assert "do not retry" in message.casefold()
    assert "retry once" not in message.casefold() and "and retry" not in message.casefold()
    assert "snapshot_and_void" in message
    # The only trace is one audit event for the create, which records the new key.
    events = read_events(_audit_isolation)
    assert [(e["operation"], e["key"], e["status"]) for e in events] == [
        ("create", "new-inv-1", "ok")
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_a_4xx_on_the_first_receipt_update_is_left_unwrapped(
    fake: FakeManager, _audit_isolation: Path
) -> None:
    """Fix 2 only touches the ambiguous cases (5xx, connection errors) where Manager's
    response does not confirm whether the request was applied. A 4xx means the request
    was rejected outright, so it is left as the plain, unwrapped httpx error it always
    was; wrapping it would be a claim about API behaviour item 3 of the Gate 4 minimum
    fix list doesn't ask this pass to make."""
    client = _client()
    proposal = await _propose(client)
    fake.fail.append(
        (_rule("PUT", r"/receipt-form/rc-a"), httpx.Response(400, json={"error": "bad"}))
    )

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await _apply(client, proposal, ["rc-a", "rc-b"])

    assert "new-inv-1" not in str(excinfo.value)
    assert list(fake.invoices) == ["new-inv-1"]
    await client.aclose()


@pytest.mark.asyncio
async def test_orphan_is_detectable_by_verification_but_nothing_links_it_to_the_failure(
    fake: FakeManager,
) -> None:
    client = _client()
    proposal = await _propose(client)
    fake.fail.append((_rule("PUT", r"/receipt-form/rc-a"), httpx.Response(500, text="x")))
    with pytest.raises(ManagerApiError):
        await _apply(client, proposal, ["rc-a", "rc-b"])

    # Someone who already knows the key can see the problem...
    verification = await verify_invoice_balance(client, "sales_invoices", "new-inv-1")
    assert verification["allocated_total"] == 0.0
    assert verification["fully_paid"] is False
    # ...and the original breakage is still reported, but as if nothing had been attempted.
    broken = await find_broken_invoice_references(client, "receipts")
    assert "rc-a" in json.dumps(broken) and "rc-b" in json.dumps(broken)
    assert "new-inv-1" not in json.dumps(broken)
    await client.aclose()


@pytest.mark.asyncio
async def test_a_plain_retry_after_a_partial_failure_creates_a_duplicate_invoice(
    # Still true after Fixes 1 to 3. Fix 2 removed the advice to retry and now names
    # this exact risk in the error message, but a caller who retries anyway is not
    # stopped: nothing here is idempotent yet. That is item 3 of the Gate 4 minimum
    # fix list (docs/write-path-spike.md) and is a separate, tracked follow-up.
    fake: FakeManager,
) -> None:
    client = _client()
    proposal = await _propose(client)
    fake.fail.append((_rule("PUT", r"/receipt-form/rc-a"), httpx.Response(500, text="x")))
    with pytest.raises(ManagerApiError):
        await _apply(client, proposal, ["rc-a", "rc-b"])
    fake.fail.clear()

    retry_proposal = await _propose(client)  # the original key is still missing, so this proposes
    assert retry_proposal["proposal_token"] == proposal["proposal_token"]
    result = await _apply(client, retry_proposal, ["rc-a", "rc-b"])

    assert result["status"] == "ok"
    assert sorted(fake.invoices) == ["new-inv-1", "new-inv-2"]  # two invoices for one debt
    assert fake.references("new-inv-1") == []
    assert fake.references("new-inv-2") == ["rc-a", "rc-b"]
    await client.aclose()


@pytest.mark.asyncio
async def test_failure_on_second_receipt_leaves_a_mixed_state(
    fake: FakeManager, _audit_isolation: Path
) -> None:
    client = _client()
    proposal = await _propose(client)
    fake.fail.append((_rule("PUT", r"/receipt-form/rc-b"), httpx.ConnectError("refused")))

    with pytest.raises(ManagerUnavailableError) as excinfo:
        await _apply(client, proposal, ["rc-a", "rc-b"])

    assert fake.references("new-inv-1") == ["rc-a"]
    assert fake.references(MISSING) == ["rc-b"]
    # Fix 2: rc-a's successful reallocation onto new-inv-1 is now named in the error,
    # so a human reading it knows exactly which invoice and which receipt to look at.
    message = str(excinfo.value)
    assert "new-inv-1" in message
    assert "do not retry" in message.casefold()
    events = read_events(_audit_isolation)
    assert [(e["operation"], e["key"]) for e in events] == [
        ("create", "new-inv-1"),
        ("reallocate", "rc-a"),
        ("verify", "rc-a"),  # Fix 3: rc-a's successful reallocation was also verified
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_create_response_without_a_key_returns_partial_but_the_invoice_exists(
    fake: FakeManager,
) -> None:
    client = _client()
    proposal = await _propose(client)

    def _no_key(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        fake.invoices["new-inv-1"] = {**body, "Key": "new-inv-1"}
        return httpx.Response(200, json={"ok": True})

    original = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/sales-invoice-form"):
            fake.log.append(("POST", "/sales-invoice-form"))
            return _no_key(request)
        return original(request)

    respx.routes.clear()
    respx.route(url__regex=rf"^{re.escape(BASE)}/.*").mock(side_effect=handler)

    result = await _apply(client, proposal, ["rc-a", "rc-b"])

    assert result["status"] == "partial"
    assert "no Key" in result["reason"]
    assert list(fake.invoices) == ["new-inv-1"]  # created, and the caller was not told its key
    assert fake.references(MISSING) == ["rc-a", "rc-b"]
    assert "new-inv-1" not in json.dumps(result)
    await client.aclose()


@pytest.mark.asyncio
async def test_create_response_that_is_not_json_raises_after_the_invoice_was_created(
    fake: FakeManager, _audit_isolation: Path
) -> None:
    client = _client()
    proposal = await _propose(client)
    original = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/sales-invoice-form"):
            original(request)  # the create happens in Manager
            return httpx.Response(200, content=b"<html>gateway page</html>")
        return original(request)

    respx.routes.clear()
    respx.route(url__regex=rf"^{re.escape(BASE)}/.*").mock(side_effect=handler)

    with pytest.raises(ValueError):  # json.JSONDecodeError
        await _apply(client, proposal, ["rc-a", "rc-b"])

    assert list(fake.invoices) == ["new-inv-1"]
    assert read_events(_audit_isolation) == []  # not even the create is audited
    await client.aclose()


@pytest.mark.asyncio
async def test_a_failing_verification_read_does_not_prevent_the_write_from_being_audited(
    fake: FakeManager, _audit_isolation: Path
) -> None:
    """Fix 3: the reallocate PUT for rc-a succeeds, and then its own follow-up
    verification read fails. Before the fix, the audit entry for that PUT was only
    written after the read succeeded, so this exact scenario left the write completely
    unaudited (an audit log that no longer matches what actually happened in Manager).
    After the fix, the write's audit entry is committed immediately after the PUT,
    before the read is attempted, so it survives even when the read fails.
    """
    client = _client()
    proposal = await _propose(client)

    def repointed(f: FakeManager) -> bool:
        return f.references("new-inv-1") == ["rc-a"]

    fake.fail.append(
        (_rule("GET", r"/receipt-form/rc-a", when=repointed), httpx.Response(500, text="x"))
    )

    with pytest.raises(ManagerApiError):
        await _apply(client, proposal, ["rc-a", "rc-b"])

    assert fake.references("new-inv-1") == ["rc-a"]  # the PUT took effect
    events = read_events(_audit_isolation)
    # The reallocate write is audited despite its own verification read failing; there
    # is no "verify" entry for rc-a, since that read never completed.
    assert [(e["operation"], e["key"]) for e in events] == [
        ("create", "new-inv-1"),
        ("reallocate", "rc-a"),
    ]
    reallocate_entry = events[1]
    assert reallocate_entry["status"] == "ok"
    assert reallocate_entry["after"] is None  # committed before the read; no read result yet
    await client.aclose()


@pytest.mark.asyncio
async def test_a_failing_final_verification_reports_an_error_although_every_write_succeeded(
    fake: FakeManager,
) -> None:
    client = _client()
    proposal = await _propose(client)

    def all_repointed(f: FakeManager) -> bool:
        return f.references("new-inv-1") == ["rc-a", "rc-b"]

    fake.fail.append(
        (_rule("GET", r"/sales-invoices", when=all_repointed), httpx.Response(500, text="x"))
    )

    with pytest.raises(ManagerApiError):
        await _apply(client, proposal, ["rc-a", "rc-b"])

    assert fake.references("new-inv-1") == ["rc-a", "rc-b"]  # the work is fully done
    await client.aclose()


# ---------------------------------------------------------------------------------------
# Recovery: what actually works from each failed state
# ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_from_a_mixed_state_by_finishing_the_remaining_receipt(
    fake: FakeManager, _audit_isolation: Path
) -> None:
    client = _client()
    proposal = await _propose(client)
    rule = (_rule("PUT", r"/receipt-form/rc-b"), httpx.ConnectError("refused"))
    fake.fail.append(rule)
    with pytest.raises(ManagerUnavailableError):
        await _apply(client, proposal, ["rc-a", "rc-b"])
    fake.fail.clear()

    # The new key is only recoverable from the local audit log; no MCP tool reads it.
    new_key = next(e["key"] for e in read_events(_audit_isolation) if e["operation"] == "create")
    out = await reallocate_receipt_line(client, client.policy, "rc-b", 0, new_key)
    verification = await verify_invoice_balance(client, "sales_invoices", new_key)
    await client.aclose()

    assert out["status"] == "ok"
    assert fake.references(new_key) == ["rc-a", "rc-b"]
    assert verification["fully_paid"] is True


@pytest.mark.asyncio
async def test_recovery_from_an_orphan_by_voiding_it_restores_the_original_state(
    fake: FakeManager,
) -> None:
    client = _client(delete=frozenset({"sales"}))
    original = fake.state()
    proposal = await _propose(client)
    fake.fail.append((_rule("PUT", r"/receipt-form/rc-a"), httpx.Response(500, text="x")))
    with pytest.raises(ManagerApiError):
        await _apply(client, proposal, ["rc-a", "rc-b"])
    fake.fail.clear()

    out = await snapshot_and_void(
        client,
        client.policy,
        "sales_invoices",
        "new-inv-1",
        confirmed_duplicate_of="failed reconstruction attempt",
    )
    await client.aclose()

    assert out["status"] == "ok"
    assert fake.state() == original


@pytest.mark.asyncio
async def test_a_mixed_state_cannot_be_rolled_back_with_the_existing_tools(
    fake: FakeManager,
) -> None:
    client = _client(delete=frozenset({"sales"}))
    proposal = await _propose(client)
    fake.fail.append((_rule("PUT", r"/receipt-form/rc-b"), httpx.ConnectError("refused")))
    with pytest.raises(ManagerUnavailableError):
        await _apply(client, proposal, ["rc-a", "rc-b"])
    fake.fail.clear()

    # Repointing rc-a back to the original (still missing) key is refused by design.
    with pytest.raises(ValueError, match="does not exist"):
        await reallocate_receipt_line(client, client.policy, "rc-a", 0, MISSING)
    # Voiding the new invoice is blocked while rc-a still references it.
    out = await snapshot_and_void(
        client,
        client.policy,
        "sales_invoices",
        "new-inv-1",
        confirmed_duplicate_of="failed reconstruction attempt",
    )
    assert out["status"] == "blocked"
    assert out["blocking_keys"] == ["rc-a"]
    await client.aclose()

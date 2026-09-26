"""Registration gating for the Phase 2 reconciliation/corrective tools."""

from __future__ import annotations

import pytest

from manager_mcp.server import mcp, register_task_tools, reset_client


def _capture_names(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    captured: list[str] = []

    def fake_tool(*_args: object, **kwargs: object):
        def deco(fn: object) -> object:
            captured.append(str(kwargs.get("name") or getattr(fn, "__name__", "")))
            return fn

        return deco

    monkeypatch.setattr(mcp, "tool", fake_tool)
    return captured


@pytest.mark.asyncio
async def test_read_diagnostics_always_registered() -> None:
    names = {t.name for t in await mcp.list_tools()}
    for expected in (
        "find_records",
        "find_broken_invoice_references",
        "find_unallocated_transactions",
        "find_duplicate_transactions",
        "verify_invoice_balance",
        "account_ledger",
        "bank_activity",
        "find_suspense_candidate_accounts",
        "general_ledger_summary",
        "reconcile_period",
    ):
        assert expected in names


def test_propose_apply_correction_require_any_write_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANAGER_MCP_WRITE_SCOPES", raising=False)
    monkeypatch.delenv("MANAGER_MCP_DELETE_SCOPES", raising=False)
    reset_client()
    captured = _capture_names(monkeypatch)
    register_task_tools()
    assert "propose_correction" not in captured
    assert "apply_correction" not in captured


def test_propose_apply_correction_registered_with_any_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "parties")
    reset_client()
    captured = _capture_names(monkeypatch)
    register_task_tools()
    assert "propose_correction" in captured
    assert "apply_correction" in captured


def test_reallocation_tools_require_banking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "purchases")
    reset_client()
    captured = _capture_names(monkeypatch)
    register_task_tools()
    assert "reallocate_payment_line" not in captured
    assert "reallocate_receipt_line" not in captured


def test_reallocation_tools_registered_with_banking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "banking")
    reset_client()
    captured = _capture_names(monkeypatch)
    register_task_tools()
    assert "reallocate_payment_line" in captured
    assert "reallocate_receipt_line" in captured


def test_purchase_reconstruction_requires_purchases_and_banking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "purchases")
    reset_client()
    captured = _capture_names(monkeypatch)
    register_task_tools()
    assert "propose_purchase_invoice_reconstruction" not in captured

    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "purchases,banking")
    reset_client()
    captured = _capture_names(monkeypatch)
    register_task_tools()
    assert "propose_purchase_invoice_reconstruction" in captured
    assert "apply_purchase_invoice_reconstruction" in captured
    assert "propose_sales_invoice_reconstruction" not in captured


def test_sales_reconstruction_requires_sales_and_banking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "sales,banking")
    reset_client()
    captured = _capture_names(monkeypatch)
    register_task_tools()
    assert "propose_sales_invoice_reconstruction" in captured
    assert "apply_sales_invoice_reconstruction" in captured
    assert "propose_purchase_invoice_reconstruction" not in captured


def test_snapshot_and_void_requires_delete_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "sales")
    monkeypatch.delenv("MANAGER_MCP_DELETE_SCOPES", raising=False)
    reset_client()
    captured = _capture_names(monkeypatch)
    register_task_tools()
    assert "snapshot_and_void" not in captured

    monkeypatch.setenv("MANAGER_MCP_DELETE_SCOPES", "sales")
    reset_client()
    captured = _capture_names(monkeypatch)
    register_task_tools()
    assert "snapshot_and_void" in captured
    assert "void_document" in captured

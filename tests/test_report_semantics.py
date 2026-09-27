# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Report tool semantics added by Onexur: pagination of report feeds and date rejection."""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp.resources import resolve
from manager_mcp.server import mcp, reset_client

BASE = "http://example.test/api2"


@pytest.fixture(autouse=True)
def _env_and_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_API_URL", BASE)
    monkeypatch.setenv("MANAGER_API_KEY", "test-key")
    reset_client()
    yield
    reset_client()


async def _call(name: str, arguments: dict | None = None) -> dict:
    result = await mcp.call_tool(name, arguments or {})
    assert not result.is_error, result
    assert result.structured_content is not None
    return result.structured_content


@pytest.mark.asyncio
@respx.mock
async def test_profit_and_loss_pages_through_all_records() -> None:
    """A report with more rows than one page must return every row, not
    just Manager's first page: this is the pagination bug fix itself.
    Pages are sized to the tool's real default (200) so the "short page
    means last page" heuristic behaves as it would against live Manager."""
    path = resolve("profit_and_loss").path  # type: ignore[union-attr]
    page1_items = [{"transaction": f"T{i}"} for i in range(200)]
    page2_items = [{"transaction": "LAST"}]
    common = {"fromDate": "2025-01-01", "toDate": "2025-12-31"}
    respx.get(
        f"{BASE}{path}", params={"skip": "0", "pageSize": "200", **common}
    ).mock(
        return_value=httpx.Response(
            200,
            json={"totalRecords": 201, "profitAndLossStatementTransactions": page1_items},
        )
    )
    route2 = respx.get(
        f"{BASE}{path}", params={"skip": "200", "pageSize": "200", **common}
    ).mock(
        return_value=httpx.Response(
            200,
            json={"totalRecords": 201, "profitAndLossStatementTransactions": page2_items},
        )
    )
    out = await _call("profit_and_loss", {"from_date": "2025-01-01", "to_date": "2025-12-31"})
    rows = out["body"]["profitAndLossStatementTransactions"]
    assert len(rows) == 201
    assert rows[-1]["transaction"] == "LAST"
    assert out["complete"] is True
    assert out["returned_count"] == 201
    assert out["total_records"] == 201
    assert route2.called


@pytest.mark.asyncio
@respx.mock
async def test_profit_and_loss_reports_incomplete_when_capped() -> None:
    """If the pagination cap is hit before totalRecords is satisfied, the
    tool must say so explicitly; never silently return a partial report."""
    path = resolve("profit_and_loss").path  # type: ignore[union-attr]

    def _responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "totalRecords": 100000,
                "profitAndLossStatementTransactions": [{"transaction": "X"}] * 200,
            },
        )

    respx.get(f"{BASE}{path}").mock(side_effect=_responder)
    out = await _call("profit_and_loss", {"from_date": "2025-01-01", "to_date": "2025-12-31"})
    assert out["complete"] is False
    assert out["completeness"] == "max_pages_reached"
    assert "truncation_notice" in out
    assert "INCOMPLETE" in out["truncation_notice"]


@pytest.mark.asyncio
@respx.mock
async def test_profit_and_loss_unable_to_determine_without_total() -> None:
    """No totalRecords ever sent, every page full: must be flagged as
    unknown-completeness, not silently reported as complete."""
    path = resolve("profit_and_loss").path  # type: ignore[union-attr]

    def _responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"profitAndLossStatementTransactions": [{"transaction": "X"}] * 200},
        )

    respx.get(f"{BASE}{path}").mock(side_effect=_responder)
    out = await _call("profit_and_loss", {"from_date": "2025-01-01", "to_date": "2025-12-31"})
    assert out["complete"] is False
    assert out["completeness"] == "unable_to_determine"
    assert "truncation_notice" in out
    assert "total_records" not in out


@pytest.mark.asyncio
@respx.mock
async def test_aged_receivables_rejects_period_instead_of_returning_current_data() -> None:
    path = resolve("aged_receivables").path  # type: ignore[union-attr]
    route = respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json={"customers": []}))  # noqa: E501
    with pytest.raises(Exception) as exc:
        await mcp.call_tool("aged_receivables", {"to_date": "2025-12-31"})
    assert "does not support date or as-at" in str(exc.value)
    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_aged_receivables_labels_itself_current_state() -> None:
    path = resolve("aged_receivables").path  # type: ignore[union-attr]
    respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json={"customers": []}))
    out = await _call("aged_receivables")
    assert out["semantics"]["data_class"] == "current_state"
    assert out["semantics"]["historical"] is False
    assert out["semantics"]["is_aged_report"] is False

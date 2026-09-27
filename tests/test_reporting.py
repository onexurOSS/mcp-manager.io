# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Reporting layer tests (respx, no live Manager). Everything here is GET only."""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp import reporting
from manager_mcp.server import mcp, reset_client

BASE = "http://example.test/api2"


def _row(
    date,
    txn,
    account,
    amount,
    *,
    bs=None,
    pl=None,
    customer=None,
    supplier=None,
    ref=None,
    tax=None,
):
    return {
        "date": date,
        "transaction": txn,
        "reference": ref,
        "customer": customer,
        "supplier": supplier,
        "account": account,
        "balanceSheetAccount": bs,
        "profitAndLossStatementAccount": pl,
        "taxCode": tax,
        "taxAmount": None,
        "bankOrCashAccount": None,
        "amount": amount,
    }


LEDGER = [
    _row(
        "2024-12-01",
        "Sales Invoice",
        "Accounts receivable",
        30,
        bs="Accounts receivable",
        customer="B",
        ref="B1",
    ),
    _row("2024-12-01", "Sales Invoice", "Sales", -30, pl="Sales", customer="B", ref="B1"),
    _row(
        "2025-01-05",
        "Sales Invoice",
        "Accounts receivable",
        120,
        bs="Accounts receivable",
        customer="A",
        ref="A1",
    ),
    _row(
        "2025-01-05", "Sales Invoice", "Sales", -100, pl="Sales", customer="A", ref="A1", tax="VAT"
    ),
    _row(
        "2025-01-05",
        "Sales Invoice",
        "Output VAT",
        -20,
        bs="Output VAT",
        customer="A",
        ref="A1",
        tax="VAT",
    ),
    _row(
        "2025-02-10",
        "Receipt",
        "Cash & cash equivalents",
        50,
        bs="Cash & cash equivalents",
        customer="A",
        ref="A1",
    ),
    _row(
        "2025-02-10",
        "Receipt",
        "Accounts receivable",
        -50,
        bs="Accounts receivable",
        customer="A",
        ref="A1",
    ),
    _row(
        "2025-03-01",
        "Purchase Invoice",
        "Accounts payable",
        -60,
        bs="Accounts payable",
        supplier="S",
        ref="P1",
    ),
    _row("2025-03-01", "Purchase Invoice", "Office", 60, pl="Office", supplier="S", ref="P1"),
    _row(
        "2026-01-01",
        "Receipt",
        "Cash & cash equivalents",
        70,
        bs="Cash & cash equivalents",
        customer="A",
    ),
    _row(
        "2026-01-01", "Receipt", "Accounts receivable", -70, bs="Accounts receivable", customer="A"
    ),
]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_API_URL", BASE)
    monkeypatch.setenv("MANAGER_API_KEY", "test-key")
    monkeypatch.setattr(reporting, "LEDGER_PAGE_SIZE", 4)  # force multi-page fetches
    reset_client()
    yield
    reset_client()


def _mock_ledger() -> respx.Route:
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        skip, size = int(q.get("skip", 0)), int(q.get("pageSize", 4))
        return httpx.Response(
            200, json={"totalRecords": len(LEDGER), "transactions": LEDGER[skip : skip + size]}
        )

    return respx.get(f"{BASE}/transactions").mock(side_effect=handler)


async def _call(name: str, args: dict | None = None) -> dict:
    result = await mcp.call_tool(name, args or {})
    assert not result.is_error
    return result.structured_content


def _assert_only_get() -> None:
    assert all(c.request.method == "GET" for c in respx.calls)


@pytest.mark.asyncio
@respx.mock
async def test_ledger_paginates_completely_and_filters_dates_client_side() -> None:
    route = _mock_ledger()
    out = await _call("ledger_transactions", {"from_date": "2025-01-01", "to_date": "2025-12-31"})
    assert route.call_count == 3  # 11 rows at page size 4
    assert out["ledger"]["complete"] is True and out["ledger"]["rows_loaded"] == 11
    assert out["total_matching"] == 7
    assert out["date_filter"] == "client_side"
    assert out["authoritative"] is False
    _assert_only_get()


@pytest.mark.asyncio
@respx.mock
async def test_ledger_account_tax_and_paging_controls() -> None:
    _mock_ledger()
    out = await _call("ledger_transactions", {"account": "Accounts receivable", "limit": 2})
    assert out["total_matching"] == 4 and out["returned"] == 2 and out["next_skip"] == 2
    tax = await _call("ledger_transactions", {"tax_only": True})
    assert tax["total_matching"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_ledger_rejects_bad_dates_and_limits() -> None:
    _mock_ledger()
    with pytest.raises(Exception, match="YYYY-MM-DD"):
        await _call("ledger_transactions", {"from_date": "31/12/2025"})
    with pytest.raises(Exception, match="limit"):
        await _call("ledger_transactions", {"limit": 0})


@pytest.mark.asyncio
@respx.mock
async def test_reconstructed_trial_balance_as_at_excludes_later_rows() -> None:
    _mock_ledger()
    out = await _call("reconstructed_trial_balance", {"as_at": "2025-03-31"})
    assert out["authoritative"] is False and out["official_manager_report"] is False
    assert out["balanced"] is True and out["difference"] == 0
    by = {ln["account"]: ln for ln in out["lines"]}
    assert by["Accounts receivable"]["net"] == 100.0  # 30 + 120 - 50, 2026 receipt excluded
    assert by["(profit and loss before period start)"]["net"] == -30.0
    assert out["as_at"] == "2025-03-31"


@pytest.mark.asyncio
@respx.mock
async def test_reconstructed_profit_and_loss_period() -> None:
    _mock_ledger()
    out = await _call(
        "reconstructed_profit_and_loss", {"from_date": "2025-01-01", "to_date": "2025-12-31"}
    )
    assert out["net_profit"] == 40.0  # 100 sales - 60 expense
    assert out["authoritative"] is False


@pytest.mark.asyncio
@respx.mock
async def test_reconstructed_ageing_ties_to_control_and_is_labelled() -> None:
    _mock_ledger()
    ar = await _call("reconstructed_aged_receivables", {"as_at": "2025-03-31"})
    assert ar["authoritative"] is False and ar["official_manager_report"] is False
    assert ar["party_total"] == 100.0 == ar["control_account_total"]
    assert ar["party_total_matches_control"] is True
    parties = {p["name"]: p for p in ar["parties"]}
    assert parties["A"]["balance"] == 70.0 and parties["A"]["buckets"] == {"61-90": 70.0}
    assert parties["B"]["buckets"] == {"91-120": 30.0}
    ap = await _call("reconstructed_aged_payables", {"as_at": "2025-03-31"})
    assert ap["party_total"] == 60.0 and ap["parties"][0]["name"] == "S"


@pytest.mark.asyncio
@respx.mock
async def test_reconstructed_tools_require_as_at() -> None:
    _mock_ledger()
    with pytest.raises(Exception):
        await _call("reconstructed_aged_receivables", {})
    with pytest.raises(Exception, match="YYYY-MM-DD"):
        await _call("reconstructed_trial_balance", {"as_at": "Dec 2025"})


@pytest.mark.asyncio
@respx.mock
async def test_get_report_definition_is_get_only_settings() -> None:
    key = "00000000-0000-0000-0000-000000000001"
    respx.get(f"{BASE}/aged-receivables-form/{key}").mock(
        return_value=httpx.Response(200, json={"Name": "AR"})
    )
    out = await _call("get_report_definition", {"report_type": "aged-receivables-form", "key": key})
    assert out["definition"] == {"Name": "AR"}
    assert out["contains_calculated_rows"] is False and out["method"] == "GET"
    with pytest.raises(Exception, match="Unknown"):
        await _call("get_report_definition", {"report_type": "customer", "key": key})
    with pytest.raises(Exception, match="GUID"):
        await _call("get_report_definition", {"report_type": "trial-balance", "key": "../x"})
    _assert_only_get()


@pytest.mark.asyncio
async def test_catalogue_never_claims_authoritative_output() -> None:
    cat = await _call("manager_report_catalogue")
    names = {r["report"] for r in cat["reports"]}
    assert {"Trial Balance", "Aged Receivables", "Aged Payables"} <= names
    assert all(r["authoritative_calculated_output_available"] is False for r in cat["reports"])
    assert all(r["historical_as_at_supported_by_api"] is False for r in cat["reports"])
    assert cat["existing_tool_authority"]["aged_receivables"]["class"] == "current_state"


@pytest.mark.asyncio
@respx.mock
async def test_unsupported_period_is_rejected_for_every_undated_report() -> None:
    for name in ("aged_receivables", "aged_payables", "bank_balances", "tax_summary"):
        with pytest.raises(Exception, match="does not support date or as-at"):
            await _call(name, {"from_date": "2025-01-01", "to_date": "2025-12-31"})
    assert respx.calls.call_count == 0  # rejected before any request was made

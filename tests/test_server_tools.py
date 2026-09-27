"""MCP tool tests with respx (no live Manager)."""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp.resources import resolve
from manager_mcp.server import mcp, reset_client

BASE = "http://example.test/api2"
REPORTS = [
    "aged_receivables",
    "aged_payables",
    "bank_balances",
    "trial_balance",
    "profit_and_loss",
    "balance_sheet",
    "tax_summary",
]


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
async def test_aged_receivables_success() -> None:
    body = {"customers": [{"name": "Acme", "accountsReceivable": {"value": 12.5}}]}
    path = resolve("aged_receivables").path  # type: ignore[union-attr]
    respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json=body))
    out = await _call("aged_receivables")
    assert out["body"]["customers"] == body["customers"]
    assert out["complete"] is True
    assert out["period_applied"] is False
    assert "period_unsupported_notice" not in out


@pytest.mark.asyncio
@respx.mock
async def test_aged_receivables_auth_error() -> None:
    path = resolve("aged_receivables").path  # type: ignore[union-attr]
    respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(401, json={"e": 1}))
    with pytest.raises(Exception, match="401"):
        await _call("aged_receivables")


@pytest.mark.asyncio
@respx.mock
async def test_trial_balance_period_applied() -> None:
    path = resolve("trial_balance").path  # type: ignore[union-attr]
    route = respx.get(f"{BASE}{path}").mock(
        return_value=httpx.Response(200, json={"trialBalanceTransactions": []})
    )
    out = await _call("trial_balance", {"from_date": "2024-01-01", "to_date": "2024-12-31"})
    assert out["period_applied"] is True
    assert route.calls.last.request.url.params.get("fromDate") == "2024-01-01"
    assert route.calls.last.request.url.params.get("toDate") == "2024-12-31"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("name", REPORTS)
async def test_report_shortcuts(name: str) -> None:
    path = resolve(name).path  # type: ignore[union-attr]
    respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json={"ok": name}))
    out = await _call(name)
    assert out["report"] == name
    assert out["body"] == {"ok": name}


@pytest.mark.asyncio
async def test_list_resources_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANAGER_MCP_WRITE_SCOPES", raising=False)
    monkeypatch.delenv("MANAGER_MCP_DELETE_SCOPES", raising=False)
    from manager_mcp.server import reset_client

    reset_client()
    out = await _call("list_resources")
    assert out["read_only"] is True
    assert out["write_scopes"] == []
    assert "create" in out["boundary"].casefold() or "delete" in out["boundary"].casefold()
    names = {r["name"] for r in out["resources"]}
    assert "customers" in names
    assert "receipts" in names
    assert "aged_receivables" in names
    assert "bank_balances" in names
    assert "bank_accounts" in names


@pytest.mark.asyncio
async def test_list_resources_reports_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "banking")
    monkeypatch.setenv("MANAGER_MCP_DELETE_SCOPES", "banking")
    for name in ("MANAGER_MCP_ALLOW_WRITES", "ALLOW_WRITES", "MANAGER_MCP_WRITES"):
        monkeypatch.delenv(name, raising=False)
    from manager_mcp.server import reset_client

    reset_client()
    out = await _call("list_resources")
    assert out["read_only"] is False
    assert out["write_scopes"] == ["banking"]
    assert "record_customer_payment" in out["boundary"].casefold()
    assert "effective_write" in out["boundary"].casefold()


@pytest.mark.asyncio
@respx.mock
async def test_list_records_truncation_and_term() -> None:
    respx.get(f"{BASE}/customers").mock(
        return_value=httpx.Response(
            200,
            json={
                "totalRecords": 120,
                "customers": [{"key": str(i)} for i in range(50)],
            },
        )
    )
    out = await _call(
        "list_records",
        {"resource": "customers", "term": "acme", "skip": 0, "page_size": 50},
    )
    assert len(out["items"]) == 50
    assert out["truncated"] is True
    assert out["has_more"] is True
    assert out["term"] == "acme"
    assert respx.calls.last.request.url.params.get("term") == "acme"


@pytest.mark.asyncio
async def test_list_records_unknown_resource() -> None:
    with pytest.raises(Exception, match="Unknown collection"):
        await _call("list_records", {"resource": "nope"})


@pytest.mark.asyncio
@respx.mock
async def test_get_record_form_path() -> None:
    respx.get(f"{BASE}/customer-form/guid-1").mock(
        return_value=httpx.Response(200, json={"Name": "Acme"})
    )
    out = await _call("get_record", {"resource": "customers", "key": "guid-1"})
    assert out["body"] == {"Name": "Acme"}
    assert out["key"] == "guid-1"


@pytest.mark.asyncio
@respx.mock
async def test_get_record_404() -> None:
    respx.get(f"{BASE}/customer-form/missing").mock(
        return_value=httpx.Response(404, json={})
    )
    with pytest.raises(Exception, match="not found"):
        await _call("get_record", {"resource": "customers", "key": "missing"})


@pytest.mark.asyncio
async def test_bank_dual_tool_descriptions() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "bank_accounts" in (tools["bank_balances"].description or "")
    assert "bank_balances" in (tools["list_records"].description or "")

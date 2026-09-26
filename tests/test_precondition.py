"""PreconditionResult shape and deposit precondition behaviour."""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp.preconditions import (
    PreconditionItem,
    PreconditionResult,
    precondition_failed_response,
)
from manager_mcp.scopes import WritePolicy
from manager_mcp.server import reset_client
from manager_mcp.task_tools import record_customer_deposit

BASE = "http://example.test/api2"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_API_URL", BASE)
    monkeypatch.setenv("MANAGER_API_KEY", "k")
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "banking")
    for name in ("MANAGER_MCP_ALLOW_WRITES", "ALLOW_WRITES", "MANAGER_MCP_WRITES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("MANAGER_MCP_DELETE_SCOPES", raising=False)
    reset_client()
    yield
    reset_client()


def test_precondition_result_to_dict() -> None:
    item = PreconditionItem(
        name="deposit_bank_account",
        why="Deposits are not revenue.",
        how_to_create="Create a Customer deposits bank account in Manager.",
    )
    result = PreconditionResult(ok=False, missing=(item,))
    payload = precondition_failed_response(result)
    assert payload["status"] == "precondition_failed"
    assert payload["preconditions"]["ok"] is False
    assert len(payload["preconditions"]["missing"]) == 1
    assert "Create a Customer deposits" in payload["next_steps"][0]


@pytest.mark.asyncio
@respx.mock
async def test_record_customer_deposit_precondition_when_no_account() -> None:
    respx.get(f"{BASE}/bank-and-cash-accounts").mock(
        return_value=httpx.Response(200, json={"bankAndCashAccounts": [], "totalRecords": 0})
    )
    from manager_mcp.client import ManagerClient

    client = ManagerClient(BASE, "k", policy=WritePolicy(frozenset({"banking"}), frozenset()))
    out = await record_customer_deposit(
        client,
        client.policy,
        customer="cust-1",
        amount=100.0,
        date="2026-07-29",
    )
    await client.aclose()
    assert out["status"] == "precondition_failed"
    assert out["keys"] == {}
    assert any("deposit" in step.lower() for step in out["next_steps"])


@pytest.mark.asyncio
@respx.mock
async def test_record_customer_deposit_happy_path() -> None:
    respx.get(f"{BASE}/bank-and-cash-accounts").mock(
        return_value=httpx.Response(
            200,
            json={
                "bankAndCashAccounts": [{"Key": "dep-acct", "Name": "Customer deposits"}],
                "totalRecords": 1,
            },
        )
    )
    respx.post(f"{BASE}/receipt-form").mock(
        return_value=httpx.Response(
            201,
            json={
                "Key": "rcpt-1",
                "ReceivedIn": "dep-acct",
                "Customer": "cust-1",
                "Date": "2026-07-29",
                "Lines": [{"Amount": 100.0}],
            },
        )
    )
    respx.get(f"{BASE}/receipt-form/rcpt-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "Key": "rcpt-1",
                "ReceivedIn": "dep-acct",
                "Customer": "cust-1",
                "Date": "2026-07-29",
                "Lines": [{"Amount": 100.0}],
            },
        )
    )
    from manager_mcp.client import ManagerClient

    client = ManagerClient(BASE, "k", policy=WritePolicy(frozenset({"banking"}), frozenset()))
    out = await record_customer_deposit(
        client,
        client.policy,
        customer="cust-1",
        amount=100.0,
        date="2026-07-29",
    )
    await client.aclose()
    assert out["status"] == "ok"
    assert out["keys"]["receipt"] == "rcpt-1"

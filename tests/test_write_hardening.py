"""Banking write validation + persistence warnings (respx; no live Manager)."""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp.client import ManagerApiError, ManagerClient
from manager_mcp.scopes import WritePolicy
from manager_mcp.server import _make_create_tool, reset_client
from manager_mcp.writable import WRITABLE
from manager_mcp.write_validate import diff_persisted, validate_write_body

BASE = "http://example.test/api2"

VALID_RECEIPT = {
    "ReceivedIn": "bank-guid",
    "Customer": "cust-guid",
    "Date": "2026-07-13",
    "PaidBy": 1,
    "ExchangeRate": 16.315733,
    "Lines": [
        {
            "Amount": 35826.042,
            "AccountsReceivableCustomer": "cust-guid",
            "AccountsReceivableSalesInvoice": "inv-guid",
        }
    ],
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_API_URL", BASE)
    monkeypatch.setenv("MANAGER_API_KEY", "k")
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "banking")
    monkeypatch.setenv("MANAGER_MCP_DELETE_SCOPES", "banking")
    for name in ("MANAGER_MCP_ALLOW_WRITES", "ALLOW_WRITES", "MANAGER_MCP_WRITES"):
        monkeypatch.delenv(name, raising=False)
    reset_client()
    yield
    reset_client()


def test_empty_body_rejected() -> None:
    with pytest.raises(ValueError, match="empty body"):
        validate_write_body(WRITABLE["receipts"], {}, creating=True)


def test_unknown_header_rejected() -> None:
    body = {**VALID_RECEIPT, "BankAccount": "x"}
    with pytest.raises(ValueError, match="unknown field"):
        validate_write_body(WRITABLE["receipts"], body, creating=True)


def test_paid_by_must_be_int() -> None:
    body = {**VALID_RECEIPT, "PaidBy": "1"}
    with pytest.raises(ValueError, match="PaidBy must be int"):
        validate_write_body(WRITABLE["receipts"], body, creating=True)


def test_missing_required_on_create() -> None:
    with pytest.raises(ValueError, match="missing required"):
        validate_write_body(
            WRITABLE["receipts"],
            {"ReceivedIn": "b", "Customer": "c", "Date": "2026-07-13"},
            creating=True,
        )


def test_diff_persisted_warns_on_drop() -> None:
    warnings = diff_persisted(
        WRITABLE["receipts"],
        VALID_RECEIPT,
        {"Key": "k", "Customer": "cust-guid", "Date": "2026-07-13", "Lines": []},
    )
    assert any("ReceivedIn" in w for w in warnings)
    assert any("Lines count" in w for w in warnings)


@pytest.mark.asyncio
async def test_create_receipt_empty_skips_http(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def boom(*_a: object, **_k: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("should not POST")

    monkeypatch.setattr("manager_mcp.server.get_client", lambda: type("C", (), {"post": boom})())
    create = _make_create_tool("receipts")
    with pytest.raises(ValueError, match="empty body"):
        await create({})
    assert called is False


@pytest.mark.asyncio
@respx.mock
async def test_create_receipt_happy_path() -> None:
    key = "11111111-1111-1111-1111-111111111111"
    respx.post(f"{BASE}/receipt-form").mock(
        return_value=httpx.Response(201, json={"Key": key, **VALID_RECEIPT})
    )
    respx.get(f"{BASE}/receipt-form/{key}").mock(
        return_value=httpx.Response(200, json={"Key": key, **VALID_RECEIPT})
    )
    create = _make_create_tool("receipts")
    out = await create(dict(VALID_RECEIPT))
    assert out["body"]["Key"] == key
    assert out["warnings"] == []


@pytest.mark.asyncio
@respx.mock
async def test_create_receipt_persistence_warning() -> None:
    key = "22222222-2222-2222-2222-222222222222"
    respx.post(f"{BASE}/receipt-form").mock(
        return_value=httpx.Response(201, json={"Key": key})
    )
    respx.get(f"{BASE}/receipt-form/{key}").mock(
        return_value=httpx.Response(
            200,
            json={
                "Key": key,
                "Customer": VALID_RECEIPT["Customer"],
                "Date": VALID_RECEIPT["Date"],
                "Lines": [],
            },
        )
    )
    create = _make_create_tool("receipts")
    out = await create(dict(VALID_RECEIPT))
    assert out["warnings"]
    assert any("ReceivedIn" in w for w in out["warnings"])


@pytest.mark.asyncio
@respx.mock
async def test_client_500_is_manager_api_error() -> None:
    respx.post(f"{BASE}/receipt-form").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    client = ManagerClient(
        BASE,
        "k",
        policy=WritePolicy(frozenset({"banking"}), frozenset()),
    )
    with pytest.raises(ManagerApiError, match="PaidBy"):
        await client.post("/receipt-form", json=VALID_RECEIPT)
    await client.aclose()

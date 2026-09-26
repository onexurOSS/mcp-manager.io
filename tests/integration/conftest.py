"""Shared fixtures for live Manager sandbox integration tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from manager_mcp.client import ManagerClient
from manager_mcp.scopes import WritePolicy
from manager_mcp.server import mcp, register_task_tools, register_write_tools, reset_client
from manager_mcp.task_tools import void_document
from manager_mcp.writable import WRITABLE
from manager_mcp.write_validate import validate_write_body

_ROOT = Path(__file__).resolve().parents[2]
_ALL_SCOPES = (
    "quotes,orders,parties,items,sales,purchases,banking,payroll,ledger"
)
_BANK_CACHE: dict[str, str] = {}
_INCOME_ACCOUNT_CACHE: str | None = None


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _record_key(record: dict[str, Any]) -> str:
    key = record.get("Key") or record.get("key")
    if not key:
        raise KeyError(f"record has no Key: {record!r}")
    return str(key)


def _test_credentials() -> tuple[str, str]:
    return (
        os.getenv("TEST_MANAGER_API_URL", "").strip(),
        os.getenv("TEST_MANAGER_API_KEY", "").strip(),
    )


@pytest.fixture(scope="session", autouse=True)
def _load_env_file() -> None:
    _load_dotenv(_ROOT / ".env")


@pytest.fixture(scope="session", autouse=True)
def _require_test_api(_load_env_file: None) -> None:
    url, key = _test_credentials()
    if not url or not key:
        pytest.skip(
            "integration: set TEST_MANAGER_API_URL + TEST_MANAGER_API_KEY "
            "(repo-root .env; see .env.example)"
        )
    # MCP tools use get_client() which reads MANAGER_API_*; bind sandbox creds for this run.
    os.environ["MANAGER_API_URL"] = url
    os.environ["MANAGER_API_KEY"] = key


@pytest.fixture(scope="session", autouse=True)
def _integration_env() -> None:
    os.environ.setdefault("MANAGER_MCP_WRITE_SCOPES", _ALL_SCOPES)
    os.environ.setdefault("MANAGER_MCP_DELETE_SCOPES", _ALL_SCOPES)
    for name in ("MANAGER_MCP_ALLOW_WRITES", "ALLOW_WRITES", "MANAGER_MCP_WRITES"):
        os.environ.pop(name, None)


@pytest.fixture(scope="session", autouse=True)
def _register_mcp_tools() -> None:
    reset_client()
    register_task_tools()
    register_write_tools()


@pytest.fixture(autouse=True)
def _fresh_client_cache() -> None:
    import manager_mcp.server as srv

    srv._client = None
    srv._policy = None
    yield
    srv._client = None
    srv._policy = None


@pytest_asyncio.fixture
async def live_client() -> AsyncIterator[ManagerClient]:
    url, key = _test_credentials()
    policy = WritePolicy.from_env()
    client = ManagerClient(url, key, policy=policy)
    yield client
    await client.aclose()


async def _call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    result = await mcp.call_tool(name, arguments or {})
    assert not result.is_error, result
    assert result.structured_content is not None
    return result.structured_content


@pytest.fixture
def call_tool():
    return _call_tool


async def _list_bank_accounts(client: ManagerClient) -> list[dict[str, Any]]:
    body = await client.get("/bank-and-cash-accounts", params={"skip": 0, "pageSize": 50})
    if not isinstance(body, dict):
        return []
    items = body.get("bankAndCashAccounts") or []
    return [item for item in items if isinstance(item, dict)]


async def _ensure_bank_account(client: ManagerClient, name: str) -> str:
    cached = _BANK_CACHE.get(name.casefold())
    if cached:
        return cached
    for item in await _list_bank_accounts(client):
        item_name = str(item.get("Name") or item.get("name") or "").casefold()
        if item_name == name.casefold():
            key = _record_key(item)
            _BANK_CACHE[name.casefold()] = key
            return key
    resource = WRITABLE["bank_accounts"]
    fields = {"Name": name}
    validate_write_body(resource, fields, creating=True)
    body = await client.post(resource.form_path, json=fields)
    assert isinstance(body, dict)
    key = _record_key(body)
    _BANK_CACHE[name.casefold()] = key
    return key


async def _income_account_key(client: ManagerClient) -> str:
    global _INCOME_ACCOUNT_CACHE
    if _INCOME_ACCOUNT_CACHE:
        return _INCOME_ACCOUNT_CACHE
    override = os.getenv("MANAGER_MCP_TEST_INCOME_ACCOUNT")
    if override:
        _INCOME_ACCOUNT_CACHE = override
        return override
    coa = await client.get("/chart-of-accounts", params={"skip": 0, "pageSize": 50})
    if isinstance(coa, dict):
        for item in coa.get("chartOfAccounts") or []:
            if isinstance(item, dict) and (item.get("Key") or item.get("key")):
                _INCOME_ACCOUNT_CACHE = _record_key(item)
                return _INCOME_ACCOUNT_CACHE
    body = await client.get("/sales-invoices", params={"skip": 0, "pageSize": 1})
    if isinstance(body, dict):
        invoices = body.get("salesInvoices") or []
        if invoices and isinstance(invoices[0], dict):
            inv_key = invoices[0].get("Key") or invoices[0].get("key")
            if inv_key:
                inv = await client.get(f"/sales-invoice-form/{inv_key}")
                if isinstance(inv, dict):
                    lines = inv.get("Lines") or []
                    if lines and isinstance(lines[0], dict) and lines[0].get("Account"):
                        _INCOME_ACCOUNT_CACHE = str(lines[0]["Account"])
                        return _INCOME_ACCOUNT_CACHE
    pytest.skip("integration: no income account; set MANAGER_MCP_TEST_INCOME_ACCOUNT")


@pytest_asyncio.fixture
async def bank_account_key(live_client: ManagerClient) -> str:
    return await _ensure_bank_account(live_client, "Integration Test Bank")


@pytest_asyncio.fixture
async def second_bank_account_key(live_client: ManagerClient) -> str:
    return await _ensure_bank_account(live_client, "Integration Second Bank")


@pytest_asyncio.fixture
async def deposit_bank_account_key(live_client: ManagerClient) -> str:
    return await _ensure_bank_account(live_client, "Customer deposits")


@pytest_asyncio.fixture
async def income_account_key(live_client: ManagerClient) -> str:
    return await _income_account_key(live_client)


@pytest_asyncio.fixture
async def expense_account_key(live_client: ManagerClient) -> str:
    return await _income_account_key(live_client)


@pytest_asyncio.fixture
async def temp_customer(live_client: ManagerClient) -> AsyncIterator[str]:
    name = f"Integration Customer {uuid.uuid4().hex[:8]}"
    resource = WRITABLE["customers"]
    fields = {"Name": name}
    validate_write_body(resource, fields, creating=True)
    body = await live_client.post(resource.form_path, json=fields)
    key = _record_key(body)
    yield key
    try:
        await void_document(live_client, live_client.policy, "customers", key)
    except Exception:
        pass


@pytest_asyncio.fixture
async def temp_supplier(live_client: ManagerClient) -> AsyncIterator[str]:
    name = f"Integration Supplier {uuid.uuid4().hex[:8]}"
    resource = WRITABLE["suppliers"]
    fields = {"Name": name}
    validate_write_body(resource, fields, creating=True)
    body = await live_client.post(resource.form_path, json=fields)
    key = _record_key(body)
    yield key
    try:
        await void_document(live_client, live_client.policy, "suppliers", key)
    except Exception:
        pass

"""Live customer and supplier CRUD."""

from __future__ import annotations

import uuid

import pytest

from manager_mcp.task_tools import void_document
from manager_mcp.writable import WRITABLE
from manager_mcp.write_validate import validate_write_body

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_create_customer_returns_key(live_client) -> None:
    name = f"Party Customer {uuid.uuid4().hex[:8]}"
    resource = WRITABLE["customers"]
    fields = {"Name": name}
    validate_write_body(resource, fields, creating=True)
    body = await live_client.post(resource.form_path, json=fields)
    key = str(body.get("Key") or body.get("key"))
    try:
        assert key
        fetched = await live_client.get(f"{resource.form_path}/{key}")
        assert fetched["Name"] == name
    finally:
        await void_document(live_client, live_client.policy, "customers", key)


@pytest.mark.asyncio
async def test_delete_customer_removes_it(live_client) -> None:
    name = f"Delete Customer {uuid.uuid4().hex[:8]}"
    resource = WRITABLE["customers"]
    fields = {"Name": name}
    validate_write_body(resource, fields, creating=True)
    body = await live_client.post(resource.form_path, json=fields)
    key = str(body.get("Key") or body.get("key"))
    await void_document(live_client, live_client.policy, "customers", key)
    listed = await live_client.get(
        resource.list_path,
        params={"term": name, "skip": 0, "pageSize": 50},
    )
    items = listed.get(resource.items_key, []) if isinstance(listed, dict) else []
    keys = {str(item.get("Key") or item.get("key")) for item in items if isinstance(item, dict)}
    assert key not in keys


@pytest.mark.asyncio
async def test_create_supplier_returns_key(live_client) -> None:
    name = f"Party Supplier {uuid.uuid4().hex[:8]}"
    resource = WRITABLE["suppliers"]
    fields = {"Name": name}
    validate_write_body(resource, fields, creating=True)
    body = await live_client.post(resource.form_path, json=fields)
    key = str(body.get("Key") or body.get("key"))
    try:
        assert key
        fetched = await live_client.get(f"{resource.form_path}/{key}")
        assert fetched["Name"] == name
    finally:
        await void_document(live_client, live_client.policy, "suppliers", key)

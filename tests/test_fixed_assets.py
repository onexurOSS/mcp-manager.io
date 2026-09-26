from __future__ import annotations

import json

import httpx
import pytest
import respx

from manager_mcp.client import ManagerClient
from manager_mcp.fixed_assets import create_fixed_asset, get_fixed_asset, update_fixed_asset
from manager_mcp.paging import fetch_paginated
from manager_mcp.scopes import WritePolicy

BASE = "http://example.test/api2"
KEY = "asset-1"
FORM = {
    "ItemName": "pc1",
    "Description": "Computer",
    "DepreciationRate": 33.33,
    "CustomDepreciationExpenseAccount": True,
    "Key": KEY,
    "id": KEY,
    "text": "pc1",
}


@pytest.mark.asyncio
@respx.mock
async def test_update_fixed_asset_preserves_full_form_and_reads_back() -> None:
    respx.get(f"{BASE}/fixed-asset-form/{KEY}").mock(
        side_effect=[
            httpx.Response(200, json=FORM),
            httpx.Response(200, json={**FORM, "Description": "Laptop"}),
        ]
    )
    put = respx.put(f"{BASE}/fixed-asset-form/{KEY}").mock(
        return_value=httpx.Response(200, json={"Key": KEY})
    )
    client = ManagerClient(BASE, "k", policy=WritePolicy(frozenset({"ledger"}), frozenset()))
    out = await update_fixed_asset(client, client.policy, KEY, {"Description": "Laptop"})
    await client.aclose()
    assert out["changed"]["Description"] == {"before": "Computer", "after": "Laptop"}
    assert json.loads(put.calls.last.request.content) == {
        "ItemName": "pc1",
        "Description": "Laptop",
        "DepreciationRate": 33.33,
        "CustomDepreciationExpenseAccount": True,
    }


@pytest.mark.asyncio
@respx.mock
async def test_create_fixed_asset_posts_and_reads_back() -> None:
    new_key = "asset-2"
    respx.post(f"{BASE}/fixed-asset-form").mock(
        return_value=httpx.Response(200, json={"Key": new_key})
    )
    respx.get(f"{BASE}/fixed-asset-form/{new_key}").mock(
        return_value=httpx.Response(
            200, json={"ItemName": "Vehicle", "DepreciationRate": 20, "Key": new_key}
        )
    )
    client = ManagerClient(BASE, "k", policy=WritePolicy(frozenset({"ledger"}), frozenset()))
    out = await create_fixed_asset(
        client, client.policy, {"ItemName": "Vehicle", "DepreciationRate": 20}
    )
    await client.aclose()
    assert out["status"] == "ok"
    assert out["key"] == new_key
    assert out["after"]["ItemName"] == "Vehicle"


@pytest.mark.asyncio
async def test_create_fixed_asset_requires_item_name() -> None:
    client = ManagerClient(BASE, "k", policy=WritePolicy(frozenset({"ledger"}), frozenset()))
    with pytest.raises(ValueError, match="ItemName"):
        await create_fixed_asset(client, client.policy, {"DepreciationRate": 20})
    await client.aclose()


@pytest.mark.asyncio
async def test_create_fixed_asset_rejects_transaction_derived_cost() -> None:
    client = ManagerClient(BASE, "k", policy=WritePolicy(frozenset({"ledger"}), frozenset()))
    with pytest.raises(ValueError, match="transaction-derived"):
        await create_fixed_asset(
            client, client.policy, {"ItemName": "Vehicle", "AcquisitionCost": 15000}
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_update_fixed_asset_rejects_transaction_derived_cost() -> None:
    client = ManagerClient(BASE, "k", policy=WritePolicy(frozenset({"ledger"}), frozenset()))
    with pytest.raises(ValueError, match="transaction-derived"):
        await update_fixed_asset(client, client.policy, KEY, {"AcquisitionCost": 1350})
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_get_fixed_asset_is_read_only() -> None:
    respx.get(f"{BASE}/fixed-asset-form/{KEY}").mock(return_value=httpx.Response(200, json=FORM))
    client = ManagerClient(BASE, "k")
    out = await get_fixed_asset(client, KEY)
    await client.aclose()
    assert out["body"] == FORM
    assert not [call for call in respx.calls if call.request.method != "GET"]


@pytest.mark.asyncio
@respx.mock
async def test_fixed_assets_register_pages_to_completion_no_duplicates() -> None:
    """The full asset register must be retrievable in one reliable call:
    a capability worth pinning down explicitly,
    across multiple pages, with no items lost or duplicated."""
    respx.get(f"{BASE}/fixed-assets", params={"skip": "0", "pageSize": "2"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "totalRecords": 3,
                "fixedAssets": [
                    {"Key": "a1", "Name": "Example Asset 001"},
                    {"Key": "a2", "Name": "Asset 2"},
                ],
            },
        )
    )
    respx.get(f"{BASE}/fixed-assets", params={"skip": "2", "pageSize": "2"}).mock(
        return_value=httpx.Response(
            200, json={"totalRecords": 3, "fixedAssets": [{"Key": "a3", "Name": "Asset 3"}]}
        )
    )
    client = ManagerClient(BASE, "k")
    result = await fetch_paginated(client, "fixed_assets", page_size=2)
    await client.aclose()
    keys = [i["Key"] for i in result.items]
    assert keys == ["a1", "a2", "a3"]
    assert len(keys) == len(set(keys))
    assert result.complete is True
    assert result.completeness == "confirmed_total"
    assert result.total_records == 3


@pytest.mark.asyncio
@respx.mock
async def test_fixed_assets_register_empty_is_complete_not_an_error() -> None:
    respx.get(f"{BASE}/fixed-assets").mock(
        return_value=httpx.Response(200, json={"totalRecords": 0, "fixedAssets": []})
    )
    client = ManagerClient(BASE, "k")
    result = await fetch_paginated(client, "fixed_assets")
    await client.aclose()
    assert result.items == []
    assert result.complete is True
    assert result.total_records == 0
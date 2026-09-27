"""Safe Fixed Asset metadata updates."""

from __future__ import annotations

import sys as _sys
from typing import Any

from manager_mcp.client import ManagerClient
from manager_mcp.scopes import WritePolicy
from manager_mcp.task_tools import require_write_scopes

FIXED_ASSET_FORM = "/fixed-asset-form"
_READ_ONLY_FIELDS = frozenset({"Key", "id", "text", "UniqueName", "NameWithCode"})
_COST_FIELDS = frozenset(
    {"AcquisitionCost", "acquisitionCost", "BookValue", "bookValue", "Depreciation"}
)


def fixed_asset_path(key: str) -> str:
    if not key or not key.strip():
        raise ValueError("fixed_asset_key is required")
    return f"{FIXED_ASSET_FORM}/{key}"


async def get_fixed_asset(client: ManagerClient, key: str) -> dict[str, Any]:
    body = await client.get(fixed_asset_path(key))
    if not isinstance(body, dict):
        raise ValueError(f"Fixed Asset {key} returned no form body.")
    return {"resource": "fixed_assets", "key": key, "body": body}


async def create_fixed_asset(
    client: ManagerClient,
    policy: WritePolicy,
    fields: dict[str, Any],
) -> dict[str, Any]:
    require_write_scopes(policy, "ledger")
    if not fields:
        raise ValueError("fields must not be empty")
    if not str(fields.get("ItemName", "")).strip():
        raise ValueError("fields must include a non-empty ItemName")
    if _COST_FIELDS.intersection(fields):
        raise ValueError(
            "Acquisition cost, book value, and depreciation are transaction-derived "
            "Manager values; they are set by referencing this Fixed Asset from a "
            "Purchase Invoice or Journal Entry, not on the Fixed Asset form."
        )
    if _READ_ONLY_FIELDS.intersection(fields):
        raise ValueError("fields contains read-only Manager form fields")

    response = await client.post(FIXED_ASSET_FORM, json=fields)
    key = response.get("Key") or response.get("key") if isinstance(response, dict) else None
    if not key:
        raise ValueError(f"Fixed Asset create response had no Key: {response!r}")

    after = await client.get(fixed_asset_path(key))
    if not isinstance(after, dict):
        raise ValueError(f"Fixed Asset {key} could not be verified after create.")
    mismatches = {
        name: {"requested": value, "actual": after.get(name)}
        for name, value in fields.items()
        if after.get(name) != value
    }
    if mismatches:
        raise ValueError(f"Fixed Asset {key} read-back differs from requested fields: {mismatches}")
    return {
        "status": "ok",
        "resource": "fixed_assets",
        "key": key,
        "created": fields,
        "response": response,
        "after": after,
    }


async def update_fixed_asset(
    client: ManagerClient,
    policy: WritePolicy,
    key: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    require_write_scopes(policy, "ledger")
    if not fields:
        raise ValueError("fields must not be empty")
    if _COST_FIELDS.intersection(fields):
        raise ValueError(
            "Acquisition cost, book value, and depreciation are transaction-derived "
            "Manager values; update the underlying transaction, not the Fixed Asset form."
        )
    if _READ_ONLY_FIELDS.intersection(fields):
        raise ValueError("fields contains read-only Manager form fields")

    path = fixed_asset_path(key)
    before = await client.get(path)
    if not isinstance(before, dict):
        raise ValueError(f"Fixed Asset {key} returned no form body.")
    unknown = [name for name in fields if name not in before]
    if unknown:
        raise ValueError(f"fields contains fields absent from the current Fixed Asset: {unknown}")

    submitted = {name: value for name, value in before.items() if name not in _READ_ONLY_FIELDS}
    submitted.update(fields)
    response = await client.put(path, json=submitted)
    after = await client.get(path)
    if not isinstance(after, dict):
        raise ValueError(f"Fixed Asset {key} could not be verified after update.")
    mismatches = {
        name: {"requested": value, "actual": after.get(name)}
        for name, value in fields.items()
        if after.get(name) != value
    }
    if mismatches:
        raise ValueError(f"Fixed Asset {key} read-back differs from requested fields: {mismatches}")
    return {
        "status": "ok",
        "resource": "fixed_assets",
        "key": key,
        "changed": {
            name: {"before": before.get(name), "after": after.get(name)}
            for name in fields
        },
        "preserved_field_count": len(submitted) - len(fields),
        "response": response,
        "after": after,
    }


def register_fixed_asset_read_tool(mcp: Any, get_client: Any) -> None:
    """Register the read-only get_fixed_asset tool."""
    _fixed_assets = _sys.modules[__name__]

    @mcp.tool(description="Fetch one Fixed Asset form by key (read-only).")
    async def get_fixed_asset(fixed_asset_key: str) -> dict[str, Any]:
        return await _fixed_assets.get_fixed_asset(get_client(), fixed_asset_key)


def register_fixed_asset_write_tools(
    mcp: Any, get_client: Any, get_policy: Any, write_annotations: Any
) -> None:
    """Register create_fixed_asset and update_fixed_asset (ledger scope)."""
    _fixed_assets = _sys.modules[__name__]

    @mcp.tool(
        name="create_fixed_asset",
        description=(
            "Register a new Fixed Asset via POST /fixed-asset-form, verifying "
            "by read-back. Requires ledger scope. fields must include a "
            "non-empty ItemName; DepreciationRate and similar policy fields "
            "are accepted. Acquisition cost, book value, and depreciation are "
            "transaction-derived and are rejected here -- reference this "
            "asset's Name from a Purchase Invoice or Journal Entry to record "
            "acquisition cost and depreciation."
        ),
        annotations=write_annotations,
    )
    async def create_fixed_asset(fields: dict[str, Any]) -> dict[str, Any]:
        return await _fixed_assets.create_fixed_asset(get_client(), get_policy(), fields)

    @mcp.tool(
        name="update_fixed_asset",
        description=(
            "Update metadata on an existing Fixed Asset via PUT "
            "/fixed-asset-form/{key}, preserving the complete current form "
            "and verifying by read-back. Requires ledger scope. Acquisition "
            "cost, book value, and depreciation are transaction-derived and "
            "are rejected; this tool creates no accounting transactions."
        ),
        annotations=write_annotations,
    )
    async def update_fixed_asset(
        fixed_asset_key: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        return await _fixed_assets.update_fixed_asset(
            get_client(), get_policy(), fixed_asset_key, fields
        )

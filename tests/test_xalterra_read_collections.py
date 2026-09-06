"""Xalterra-owned: Fixed Assets / Tax Codes read-only collection additions.

Kept isolated from upstream's own test_resources.py/test_server_tools.py so
future `git fetch upstream && git merge` operations never conflict here.

Both are pure read-only additions -- never present in writable.WRITABLE --
so they can never gain a create_*/update_*/delete_* tool through
register_write_tools()/register_task_tools(), regardless of future scope
configuration. See src/manager_mcp/xalterra_read_collections.py for the
live-verified paths/items keys this pins down.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp.resources import resolve
from manager_mcp.server import mcp, register_task_tools, register_write_tools, reset_client
from manager_mcp.writable import WRITABLE
from manager_mcp.xalterra_read_collections import XALTERRA_READ_ONLY_COLLECTIONS

BASE = "http://example.test/api2"

EXPECTED = {
    "fixed_assets": ("/fixed-assets", "/fixed-asset-form/{key}", "fixedAssets"),
    "tax_codes": ("/tax-codes", "/tax-code-form/{key}", "taxCodes"),
}


@pytest.fixture(autouse=True)
def _env_and_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_API_URL", BASE)
    monkeypatch.setenv("MANAGER_API_KEY", "test-key")
    monkeypatch.delenv("MANAGER_MCP_WRITE_SCOPES", raising=False)
    monkeypatch.delenv("MANAGER_MCP_DELETE_SCOPES", raising=False)
    reset_client()
    yield
    reset_client()


async def _call(name: str, arguments: dict | None = None) -> dict:
    result = await mcp.call_tool(name, arguments or {})
    assert not result.is_error, result
    assert result.structured_content is not None
    return result.structured_content


def test_xalterra_collections_module_has_exactly_two_entries() -> None:
    names = {row[0] for row in XALTERRA_READ_ONLY_COLLECTIONS}
    assert names == {"fixed_assets", "tax_codes"}


def test_fixed_assets_and_tax_codes_never_in_writable() -> None:
    """The whole point of this addition: no write path must ever exist."""
    assert "fixed_assets" not in WRITABLE
    assert "tax_codes" not in WRITABLE


@pytest.mark.parametrize("name", ["fixed_assets", "tax_codes"])
def test_collection_resolves_with_live_verified_paths(name: str) -> None:
    list_path, form_template, items_key = EXPECTED[name]
    desc = resolve(name)
    assert desc is not None
    assert desc.kind == "collection"
    assert desc.path == list_path
    assert desc.form_path_template == form_template
    assert desc.items_key == items_key
    assert desc.supports_form is True


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("name", ["fixed_assets", "tax_codes"])
async def test_list_records_works(name: str) -> None:
    list_path, _form_template, items_key = EXPECTED[name]
    respx.get(f"{BASE}{list_path}").mock(
        return_value=httpx.Response(200, json={items_key: [{"key": "k1"}, {"key": "k2"}]})
    )
    out = await _call("list_records", {"resource": name})
    assert out["resource"] == name
    assert out["items"] == [{"key": "k1"}, {"key": "k2"}]


@pytest.mark.asyncio
@respx.mock
async def test_get_record_fixed_asset() -> None:
    respx.get(f"{BASE}/fixed-asset-form/abc-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "Key": "abc-1",
                "Name": "Test Asset",
                "AcquisitionCost": 1000,
                "BookValue": 900,
            },
        )
    )
    out = await _call("get_record", {"resource": "fixed_assets", "key": "abc-1"})
    assert out["body"]["Key"] == "abc-1"
    assert out["body"]["AcquisitionCost"] == 1000


@pytest.mark.asyncio
@respx.mock
async def test_get_record_tax_code() -> None:
    respx.get(f"{BASE}/tax-code-form/xyz-9").mock(
        return_value=httpx.Response(200, json={"Key": "xyz-9", "Name": "Standard Rate"})
    )
    out = await _call("get_record", {"resource": "tax_codes", "key": "xyz-9"})
    assert out["body"]["Key"] == "xyz-9"


@pytest.mark.asyncio
async def test_both_collections_visible_in_list_resources_read_only() -> None:
    out = await _call("list_resources")
    assert out["read_only"] is True
    names = {r["name"] for r in out["resources"]}
    assert "fixed_assets" in names
    assert "tax_codes" in names


@pytest.mark.asyncio
async def test_total_tool_count_unchanged_at_ten() -> None:
    register_write_tools()
    register_task_tools()
    tools = await mcp.list_tools()
    assert len(tools) == 10


@pytest.mark.asyncio
async def test_no_mutation_tools_for_fixed_assets_or_tax_codes() -> None:
    register_write_tools()
    register_task_tools()
    names = {t.name for t in await mcp.list_tools()}
    for stem in ("fixed_asset", "tax_code"):
        for verb in ("create_", "update_", "delete_"):
            assert f"{verb}{stem}" not in names

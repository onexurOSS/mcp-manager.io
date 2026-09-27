# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Fixed Assets / Tax Codes read-only collection additions.

Both are pure read-only additions, never present in writable.WRITABLE, so they can
never gain a create_*/update_*/delete_* tool through
register_write_tools()/register_task_tools(), regardless of future scope
configuration. See src/manager_mcp/extra_read_collections.py for the
verified paths and items keys this pins down.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp.extra_read_collections import EXTRA_READ_ONLY_COLLECTIONS
from manager_mcp.resources import resolve
from manager_mcp.server import mcp, register_task_tools, register_write_tools, reset_client
from manager_mcp.writable import WRITABLE

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


def test_extra_collections_module_has_exactly_two_entries() -> None:
    names = {row[0] for row in EXTRA_READ_ONLY_COLLECTIONS}
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
async def test_total_tool_count_includes_explicit_fixed_asset_update() -> None:
    # 10 original read tools + 10 read-only reconciliation/diagnostic tools
    # added in Phase 2 (find_records, find_broken_invoice_references,
    # find_unallocated_transactions, find_duplicate_transactions,
    # verify_invoice_balance, account_ledger, bank_activity,
    # find_suspense_candidate_accounts, general_ledger_summary,
    # reconcile_period) + get_server_info (server-identity diagnostic).
    # Fixed Assets add one explicit metadata-only update tool; tax_codes add
    # no tools. The read-only reporting layer adds 7 GET-only tools
    # (manager_report_catalogue, get_report_definition, ledger_transactions,
    # reconstructed_trial_balance, reconstructed_profit_and_loss,
    # reconstructed_aged_receivables, reconstructed_aged_payables).
    register_write_tools()
    register_task_tools()
    tools = await mcp.list_tools()
    assert len(tools) == 29


@pytest.mark.asyncio
async def test_fixed_assets_have_no_write_tool_without_ledger_scope() -> None:
    register_write_tools()
    register_task_tools()
    names = {t.name for t in await mcp.list_tools()}
    assert "fixed_assets" not in WRITABLE
    assert "update_fixed_asset" not in names
    assert "create_fixed_asset" not in names
    assert "delete_fixed_asset" not in names
    assert "create_tax_code" not in names
    assert "update_tax_code" not in names
    assert "delete_tax_code" not in names

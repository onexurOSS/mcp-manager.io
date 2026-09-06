"""Xalterra Manager.io -> ERPNext migration: read collection coverage.

This file is a Xalterra-owned addition, kept isolated from upstream's own
test files (test_resources.py, test_server_tools.py, test_readonly_guard.py)
so future `git fetch upstream && git merge` operations never conflict here.

No upstream file (resources.py, writable.py, server.py) needed any change
for this task. Investigation showed that `resources._merge_writable_collections`
already merges *every* entry in `writable.WRITABLE` into the resolvable
collection registry unconditionally -- read access via list_records/get_record
does not check MANAGER_MCP_WRITE_SCOPES/MANAGER_MCP_DELETE_SCOPES at all (see
client.py: "GET always; writes scope-gated", and scopes.WritePolicy.authorize()
which no-ops for any method outside WRITE_METHODS). So 12 of the 14 migration
collections requested were already fully readable in unmodified v0.2.6:

    receipts, payments, inter_account_transfers, credit_notes, debit_notes,
    employees, payslips, expense_claims, journal_entries,
    depreciation_entries, inventory_items, non_inventory_items

This file pins that set down as an explicit regression guard: if a future
upstream change ever removed one of these from WRITABLE (or gated the merge
by scope), a migration-critical read would silently disappear, and this
test file is what would catch it.

Two requested collections are deliberately NOT added: `fixed_assets` and
`tax_codes`. Neither appears anywhere in this codebase (not in
writable.WRITABLE, not in resources._BASE_RESOURCES, not in the bundled
partial OpenAPI fragment at src/manager_mcp/spec/api2.json), and Manager's
public API documentation was unreachable when this was written (moved/
paywalled). Per instruction, no endpoint path is invented here. Adding these
two requires either a live Manager API2 response confirming the exact path/
items-key, or authoritative documentation -- whichever arrives first should
extend `MIGRATION_COLLECTIONS` below and add a matching resolve()/list_records
test, the same shape as the ones already here.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp.resources import resolve
from manager_mcp.server import mcp, register_task_tools, register_write_tools, reset_client
from manager_mcp.writable import WRITABLE

BASE = "http://example.test/api2"

# The 12 requested collections confirmed already present and read-only-safe.
# Deliberately excludes fixed_assets / tax_codes -- see module docstring.
MIGRATION_COLLECTIONS = (
    "receipts",
    "payments",
    "inter_account_transfers",
    "credit_notes",
    "debit_notes",
    "employees",
    "payslips",
    "expense_claims",
    "journal_entries",
    "depreciation_entries",
    "inventory_items",
    "non_inventory_items",
)

NOT_YET_VERIFIED = ("fixed_assets", "tax_codes")


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


def test_all_migration_collections_are_registered_in_writable() -> None:
    """Every migration collection must come from WRITABLE, matching the
    project's existing convention -- nothing here is a bespoke addition."""
    for name in MIGRATION_COLLECTIONS:
        assert name in WRITABLE, f"{name} missing from writable.WRITABLE"


def test_all_migration_collections_resolve() -> None:
    for name in MIGRATION_COLLECTIONS:
        desc = resolve(name)
        assert desc is not None, f"{name} does not resolve as a collection"
        assert desc.kind == "collection"
        assert desc.path.startswith("/")


def test_not_yet_verified_collections_are_absent_not_invented() -> None:
    """fixed_assets/tax_codes must stay unresolved until a real path is
    confirmed -- this test fails loudly the day someone is tempted to guess."""
    for name in NOT_YET_VERIFIED:
        assert resolve(name) is None, (
            f"{name} now resolves -- update this test and the module docstring "
            "once its path has actually been verified against live Manager API2"
        )


@pytest.mark.asyncio
async def test_migration_collections_visible_in_list_resources_read_only() -> None:
    out = await _call("list_resources")
    assert out["read_only"] is True
    names = {r["name"] for r in out["resources"]}
    for name in MIGRATION_COLLECTIONS:
        assert name in names


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("name", MIGRATION_COLLECTIONS)
async def test_list_records_works_for_each_migration_collection(name: str) -> None:
    desc = resolve(name)
    assert desc is not None
    respx.get(f"{BASE}{desc.path}").mock(
        return_value=httpx.Response(200, json={desc.items_key: [{"Key": "abc-1"}]})
    )
    out = await _call("list_records", {"resource": name})
    assert out["resource"] == name
    assert out["items"] == [{"Key": "abc-1"}]


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("name", MIGRATION_COLLECTIONS)
async def test_get_record_works_for_each_migration_collection_with_a_form(name: str) -> None:
    desc = resolve(name)
    assert desc is not None
    if not desc.supports_form:
        pytest.skip(f"{name} has no single-record form endpoint")
    path = desc.form_path_template.replace("{key}", "abc-1")  # type: ignore[union-attr]
    respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json={"Key": "abc-1"}))
    out = await _call("get_record", {"resource": name, "key": "abc-1"})
    assert out["body"] == {"Key": "abc-1"}


@pytest.mark.asyncio
async def test_no_mutation_tools_for_migration_collections_with_scopes_unset() -> None:
    """The core Xalterra safety requirement: reads work (proven above), but
    with MANAGER_MCP_WRITE_SCOPES/MANAGER_MCP_DELETE_SCOPES unset (this
    file's autouse fixture guarantees that) and `raw` never referenced,
    create_*/update_*/delete_* for these resources must not exist at all --
    not merely fail when called."""
    register_write_tools()
    register_task_tools()
    names = {t.name for t in await mcp.list_tools()}
    for collection in MIGRATION_COLLECTIONS:
        stem = WRITABLE[collection].tool_stem
        for verb in ("create_", "update_", "delete_"):
            tool_name = f"{verb}{stem}"
            assert tool_name not in names, (
                f"{tool_name} must not be registered with write/delete scopes unset"
            )


def test_raw_scope_never_referenced_in_this_file() -> None:
    """Guards against a future edit accidentally reintroducing the raw escape
    hatch into Xalterra's own test setup."""
    import inspect

    source = inspect.getsource(_env_and_client)
    assert "raw" not in source.casefold()

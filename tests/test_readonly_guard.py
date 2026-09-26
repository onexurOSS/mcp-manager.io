"""Read-only default and legacy/scope startup guards."""

from __future__ import annotations

import pytest

from manager_mcp.scopes import ScopeConfigError, WritePolicy
from manager_mcp.server import mcp, register_write_tools, reset_client

MUTATING_PREFIXES = ("create_", "update_", "delete_")


@pytest.mark.asyncio
async def test_default_tools_have_no_mutating_verbs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANAGER_MCP_WRITE_SCOPES", raising=False)
    monkeypatch.delenv("MANAGER_MCP_DELETE_SCOPES", raising=False)
    for name in ("MANAGER_MCP_ALLOW_WRITES", "ALLOW_WRITES", "MANAGER_MCP_WRITES"):
        monkeypatch.delenv(name, raising=False)
    reset_client()
    register_write_tools()
    from manager_mcp.server import register_task_tools

    register_task_tools()
    names = [t.name for t in await mcp.list_tools()]
    assert names
    assert "api_write" not in names
    for name in names:
        assert not name.startswith(MUTATING_PREFIXES)


def test_legacy_allow_writes_hard_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_MCP_ALLOW_WRITES", "true")
    with pytest.raises(ScopeConfigError, match="no longer supported"):
        WritePolicy.from_env()


def test_unknown_scope_hard_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "invoices")
    with pytest.raises(ScopeConfigError, match="unknown scope"):
        WritePolicy.from_env()

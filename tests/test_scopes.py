"""Scope parsing, denylist, and authorize matrix (no live Manager)."""

from __future__ import annotations

import pytest

from manager_mcp.scopes import (
    ScopeConfigError,
    WritePolicy,
    WritesDeniedError,
    is_denylisted,
    parse_scope_csv,
    reject_legacy_write_envs,
)


def test_parse_happy() -> None:
    assert parse_scope_csv("quotes, orders", env_name="X") == frozenset({"quotes", "orders"})


def test_parse_empty() -> None:
    assert parse_scope_csv("", env_name="X") == frozenset()
    assert parse_scope_csv(None, env_name="X") == frozenset()


@pytest.mark.parametrize("bad", ["*", "all", "invoices", "quote*", "sales invoices"])
def test_parse_rejects_unknown_and_wildcards(bad: str) -> None:
    with pytest.raises(ScopeConfigError):
        parse_scope_csv(bad, env_name="MANAGER_MCP_WRITE_SCOPES")


def test_legacy_envs_hard_fail() -> None:
    with pytest.raises(ScopeConfigError, match="no longer supported"):
        reject_legacy_write_envs({"MANAGER_MCP_ALLOW_WRITES": "true"})
    with pytest.raises(ScopeConfigError, match="no longer supported"):
        reject_legacy_write_envs({"ALLOW_WRITES": "1"})


def test_delete_not_implied_by_write() -> None:
    policy = WritePolicy(write_scopes=frozenset({"quotes"}), delete_scopes=frozenset())
    with pytest.raises(WritesDeniedError, match="DELETE_SCOPES"):
        policy.authorize("DELETE", "/sales-quote-form/abc")
    policy.authorize("POST", "/sales-quote-form")


def test_denylist_access_token() -> None:
    assert is_denylisted("/access-token-form")
    policy = WritePolicy(write_scopes=frozenset({"parties"}), delete_scopes=frozenset())
    with pytest.raises(WritesDeniedError, match="denylist"):
        policy.authorize("POST", "/access-token-form")


def test_denylist_account_forms() -> None:
    assert is_denylisted("/balance-sheet-cash-at-bank-account-form")
    assert is_denylisted("/control-account-for-customers-form")
    # Bank/cash accounts are writable under banking scope (deposit setup).
    assert not is_denylisted("/bank-or-cash-account-form")
    assert not is_denylisted("/bank-or-cash-account-form/abc")


def test_unmapped_path_denied() -> None:
    policy = WritePolicy(write_scopes=frozenset({"quotes"}), delete_scopes=frozenset())
    with pytest.raises(WritesDeniedError, match="not a scoped"):
        policy.authorize("POST", "/random-form")


def test_from_env_unknown_scope() -> None:
    with pytest.raises(ScopeConfigError, match="unknown scope"):
        WritePolicy.from_env({"MANAGER_MCP_WRITE_SCOPES": "invoices"})


def test_raw_scope_expands_effective_write() -> None:
    policy = WritePolicy(write_scopes=frozenset({"raw"}), delete_scopes=frozenset())
    assert "banking" in policy.effective_write_scopes
    assert "quotes" in policy.effective_write_scopes
    policy.authorize("POST", "/receipt-form")


def test_raw_parses() -> None:
    assert parse_scope_csv("raw,banking", env_name="X") == frozenset({"raw", "banking"})

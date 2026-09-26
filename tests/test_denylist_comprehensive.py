"""Denylist blocks chart-of-accounts writes under every scope combination."""

from __future__ import annotations

import pytest

from manager_mcp.scopes import VALID_SCOPES, WritePolicy, WritesDeniedError, is_denylisted

_DENYLIST_PATHS = [
    "/access-token-form",
    "/chart-of-accounts",
    "/balance-sheet-cash-at-bank-account-form",
    "/control-account-for-customers-form",
    "/profit-and-loss-statement-account-sales-form",
    "/tax-code-form",
    "/bank-reconciliation-form",
]

_SCOPE_COMBOS = [
    frozenset(),
    frozenset({"banking"}),
    frozenset({"ledger"}),
    frozenset({"raw"}),
    VALID_SCOPES - {"raw"},
]


@pytest.mark.parametrize("path", _DENYLIST_PATHS)
def test_paths_are_denylisted(path: str) -> None:
    assert is_denylisted(path)


@pytest.mark.parametrize("scopes", _SCOPE_COMBOS)
@pytest.mark.parametrize("path", _DENYLIST_PATHS)
def test_denylist_blocks_post_under_all_scopes(scopes: frozenset[str], path: str) -> None:
    policy = WritePolicy(write_scopes=scopes, delete_scopes=scopes)
    with pytest.raises(WritesDeniedError, match="denylist"):
        policy.authorize("POST", path)


def test_bank_or_cash_not_denylisted() -> None:
    assert not is_denylisted("/bank-or-cash-account-form")


@pytest.mark.parametrize("scopes", _SCOPE_COMBOS)
def test_bank_or_cash_allowed_when_banking_or_raw(scopes: frozenset[str]) -> None:
    policy = WritePolicy(write_scopes=scopes, delete_scopes=frozenset())
    effective = policy.effective_write_scopes
    if "banking" in effective:
        policy.authorize("POST", "/bank-or-cash-account-form")
    elif scopes == frozenset({"raw"}):
        policy.authorize("POST", "/bank-or-cash-account-form")
    else:
        with pytest.raises(WritesDeniedError):
            policy.authorize("POST", "/bank-or-cash-account-form")

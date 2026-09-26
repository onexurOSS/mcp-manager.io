"""Reusable precondition checks for task tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PreconditionItem:
    name: str
    why: str
    how_to_create: str


@dataclass(frozen=True)
class PreconditionResult:
    ok: bool
    missing: tuple[PreconditionItem, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "missing": [asdict(item) for item in self.missing],
        }


def precondition_failed_response(result: PreconditionResult) -> dict[str, Any]:
    return {
        "status": "precondition_failed",
        "preconditions": result.to_dict(),
        "keys": {},
        "warnings": [],
        "next_steps": [
            item.how_to_create for item in result.missing if item.how_to_create
        ],
    }


_DEPOSIT_ACCOUNT_GUIDE = (
    "In Manager: Settings → Bank and Cash Accounts → New Account. "
    "Name it e.g. 'Customer deposits' (liability-style holding account). "
    "Alternatively, with banking scope enabled, call create_bank_account "
    "with {\"Name\": \"Customer deposits\"} in a separate explicit step."
)

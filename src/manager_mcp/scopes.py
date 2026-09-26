"""Write/delete scope parsing, domain maps, and path denylist."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

WRITE_SCOPES_ENV = "MANAGER_MCP_WRITE_SCOPES"
DELETE_SCOPES_ENV = "MANAGER_MCP_DELETE_SCOPES"
LEGACY_WRITE_ENVS = (
    "MANAGER_MCP_ALLOW_WRITES",
    "ALLOW_WRITES",
    "MANAGER_MCP_WRITES",
)

VALID_SCOPES = frozenset(
    {
        "quotes",
        "orders",
        "parties",
        "items",
        "sales",
        "purchases",
        "banking",
        "payroll",
        "ledger",
        # Escape hatch: registers full CRUD set; effective_* expands to all domains.
        "raw",
    }
)

DOMAIN_SCOPES = VALID_SCOPES - {"raw"}

# resource_key -> scope
RESOURCE_SCOPE: dict[str, str] = {
    # quotes
    "sales_quotes": "quotes",
    "purchase_quotes": "quotes",
    # orders
    "sales_orders": "orders",
    "purchase_orders": "orders",
    # parties
    "customers": "parties",
    "suppliers": "parties",
    # items
    "inventory_items": "items",
    "non_inventory_items": "items",
    # sales
    "sales_invoices": "sales",
    "credit_notes": "sales",
    "delivery_notes": "sales",
    # purchases
    "purchase_invoices": "purchases",
    "debit_notes": "purchases",
    "goods_receipts": "purchases",
    # banking
    "receipts": "banking",
    "payments": "banking",
    "inter_account_transfers": "banking",
    "bank_accounts": "banking",
    # payroll
    "employees": "payroll",
    "payslips": "payroll",
    "expense_claims": "payroll",
    # ledger
    "journal_entries": "ledger",
    "depreciation_entries": "ledger",
    "amortization_entries": "ledger",
    "fixed_assets": "ledger",
}

# Normalized path (no trailing slash, no {key}) -> resource_key
# Form POST uses path without key; PUT/DELETE use .../form/{key}
PATH_TO_RESOURCE: dict[str, str] = {
    "/sales-quote-form": "sales_quotes",
    "/purchase-quote-form": "purchase_quotes",
    "/sales-order-form": "sales_orders",
    "/purchase-order-form": "purchase_orders",
    "/customer-form": "customers",
    "/supplier-form": "suppliers",
    "/inventory-item-form": "inventory_items",
    "/non-inventory-item-form": "non_inventory_items",
    "/sales-invoice-form": "sales_invoices",
    "/credit-note-form": "credit_notes",
    "/delivery-note-form": "delivery_notes",
    "/purchase-invoice-form": "purchase_invoices",
    "/debit-note-form": "debit_notes",
    "/goods-receipt-form": "goods_receipts",
    "/receipt-form": "receipts",
    "/payment-form": "payments",
    "/inter-account-transfer-form": "inter_account_transfers",
    "/bank-or-cash-account-form": "bank_accounts",
    "/employee-form": "employees",
    "/payslip-form": "payslips",
    "/expense-claim-form": "expense_claims",
    "/journal-entry-form": "journal_entries",
    "/depreciation-entry-form": "depreciation_entries",
    "/amortization-entry-form": "amortization_entries",
    "/fixed-asset-form": "fixed_assets",
}

_DENY_EXACT = frozenset(
    {
        "/access-token-form",
        "/chart-of-accounts",
        "/bank-reconciliation-form",
        "/customer-portal-form",
    }
)

_DENY_SUBSTRINGS = (
    "starting-balance",
    "tax-code",
    "exchange-rate",
    "base-currency",
    "foreign-currency",
    "custom-field",
    "custom-button",
    "theme-form",
    "email-template",
    "email-settings",
    "bank-reconciliation",
    "customer-portal",
    "access-token",
)

_DENY_REGEX = tuple(
    re.compile(p)
    for p in (
        r"^/balance-sheet-.*-account-form",
        r"^/control-account-for-.*-form",
        r"^/profit-and-loss-statement-account-.*-form",
        # COA-style account forms; bank-or-cash is a bank account (allowed when scoped)
        r"^/(?!bank-or-cash-)[^/]*-account-form$",
    )
)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class ScopeConfigError(ValueError):
    """Invalid scope configuration (unknown names, wildcards, legacy envs)."""


class WritesDeniedError(RuntimeError):
    """Client refused a mutating request (denylist or missing scope)."""


def _normalize_path(path: str) -> str:
    p = path if path.startswith("/") else f"/{path}"
    p = p.split("?", 1)[0]
    # Strip /{key} after *-form (GUID or opaque id) for authorize lookup.
    parts = p.rstrip("/").split("/")
    if len(parts) >= 3 and parts[-2].endswith("-form"):
        parts = parts[:-1]
    elif len(parts) >= 2 and re.fullmatch(
        r"[0-9a-fA-F-]{36}|\{key\}", parts[-1]
    ):
        parts = parts[:-1]
    return "/".join(parts) if parts[0] == "" else "/" + "/".join(parts)


def is_denylisted(path: str) -> bool:
    norm = _normalize_path(path).casefold()
    if norm in {p.casefold() for p in _DENY_EXACT}:
        return True
    for sub in _DENY_SUBSTRINGS:
        if sub in norm:
            return True
    for rx in _DENY_REGEX:
        if rx.search(norm):
            return True
    return False


def path_to_resource(path: str) -> str | None:
    return PATH_TO_RESOURCE.get(_normalize_path(path))


def parse_scope_csv(raw: str | None, *, env_name: str) -> frozenset[str]:
    if raw is None or raw.strip() == "":
        return frozenset()
    tokens = [t.strip().casefold() for t in raw.split(",")]
    tokens = [t for t in tokens if t]
    if not tokens:
        return frozenset()
    for t in tokens:
        if t in {"*", "all"} or any(c in t for c in "*?"):
            raise ScopeConfigError(
                f"{env_name}: wildcards are not allowed ({t!r}). "
                f"Valid scopes: {', '.join(sorted(VALID_SCOPES))}"
            )
        if t not in VALID_SCOPES:
            raise ScopeConfigError(
                f"{env_name}: unknown scope {t!r}. "
                f"Valid scopes: {', '.join(sorted(VALID_SCOPES))}"
            )
    return frozenset(tokens)


def reject_legacy_write_envs(environ: dict[str, str] | None = None) -> None:
    env = environ if environ is not None else os.environ
    for name in LEGACY_WRITE_ENVS:
        raw = env.get(name)
        if raw is not None and raw.strip() != "":
            raise ScopeConfigError(
                f"{name} is no longer supported (was set to {raw.strip()!r}). "
                f"Use {WRITE_SCOPES_ENV} and {DELETE_SCOPES_ENV} "
                f"(comma-separated scopes, e.g. quotes,parties)."
            )


@dataclass(frozen=True)
class WritePolicy:
    write_scopes: frozenset[str]
    delete_scopes: frozenset[str]

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> WritePolicy:
        env = environ if environ is not None else os.environ
        reject_legacy_write_envs(env)
        return cls(
            write_scopes=parse_scope_csv(env.get(WRITE_SCOPES_ENV), env_name=WRITE_SCOPES_ENV),
            delete_scopes=parse_scope_csv(
                env.get(DELETE_SCOPES_ENV), env_name=DELETE_SCOPES_ENV
            ),
        )

    @property
    def any_enabled(self) -> bool:
        return bool(self.write_scopes or self.delete_scopes)

    @property
    def effective_write_scopes(self) -> frozenset[str]:
        if "raw" in self.write_scopes:
            return DOMAIN_SCOPES
        return self.write_scopes - {"raw"}

    @property
    def effective_delete_scopes(self) -> frozenset[str]:
        if "raw" in self.delete_scopes:
            return DOMAIN_SCOPES
        return self.delete_scopes - {"raw"}

    def authorize(self, method: str, path: str) -> None:
        method = method.upper()
        if method not in WRITE_METHODS:
            return
        if is_denylisted(path):
            raise WritesDeniedError(
                f"{method} {path} is permanently denied (denylist)."
            )
        resource = path_to_resource(path)
        if resource is None:
            raise WritesDeniedError(
                f"{method} {path} is not a scoped writable resource."
            )
        scope = RESOURCE_SCOPE[resource]
        if method == "DELETE":
            if scope not in self.effective_delete_scopes:
                raise WritesDeniedError(
                    f"DELETE {path} requires scope {scope!r} in {DELETE_SCOPES_ENV}."
                )
            return
        if scope not in self.effective_write_scopes:
            raise WritesDeniedError(
                f"{method} {path} requires scope {scope!r} in {WRITE_SCOPES_ENV}."
            )

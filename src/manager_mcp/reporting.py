"""Read-only reporting layer for Manager.io (GET only, no writes).

Authority model (never blur these):

- ``current_state``: live balances (customers, suppliers, bank). Never historical.
- ``raw_transaction_feed``: rows Manager returns from ``*-transactions`` feeds.
  Not a finished report.
- ``historical_transaction_data``: filtered rows from the ``/transactions``
  ledger feed (real account per row). Dates are filtered client side because
  Manager ignores ``fromDate``/``toDate`` on that endpoint.
- ``reconstructed``: calculations made here from the ledger feed. Always
  ``authoritative: false``, even when they match an official Manager report.
- ``application_only``: finished report views exist only inside the Manager
  application (API key gets HTTP 401); the MCP never scrapes them.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from collections.abc import Callable
from typing import Any

LEDGER_PATH = "/transactions"
# Page size 3000 verified accepted live (Manager 26.8.4.3664); true maximum unknown.
LEDGER_PAGE_SIZE = 3000
LEDGER_MAX_PAGES = 200
LEDGER_FIELDS = [
    "Date",
    "Transaction",
    "Reference",
    "BankOrCashAccount",
    "Customer",
    "Supplier",
    "Employee",
    "Description",
    "Item",
    "Account",
    "BalanceSheetAccount",
    "ProfitAndLossStatementAccount",
    "LineDescription",
    "Qty",
    "UnitPrice",
    "Project",
    "Division",
    "TaxCode",
    "TaxAmount",
    "Debit",
    "Credit",
    "Amount",
    "Timestamp",
]
AGE_BUCKETS = (
    ("0-30", 0, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("91-120", 91, 120),
    ("120+", 121, 10**9),
)
_KEY_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")

# Report form types whose stored settings may be read (GET /{type}-form/{key}).
# Settings only: Manager does not return calculated rows for these.
REPORT_FORM_TYPES = frozenset(
    {
        "aged-payables",
        "aged-receivables",
        "balance-sheet",
        "balance-sheet-by-group",
        "bank-account-summary",
        "bank-reconciliation",
        "billable-time-summary",
        "capital-accounts-summary",
        "cash-flow-statement",
        "custom-report",
        "customer-statements-transactions",
        "customer-statements-unpaid-invoices",
        "customer-summary",
        "depreciation-calculation-worksheet",
        "division-exception-report",
        "employee-statements-transactions",
        "employee-summary",
        "expense-claims-summary",
        "fixed-asset-summary",
        "forecast-profit-and-loss-statement",
        "general-ledger-summary",
        "general-ledger-transactions",
        "intangible-asset-summary",
        "inventory-costing-calculation-worksheet",
        "inventory-quantity-summary",
        "inventory-value-summary",
        "payslip-summary",
        "profit-and-loss-statement",
        "profit-and-loss-statement-actual-vs-budget",
        "profit-and-loss-statement-by-group",
        "realized-investment-gains-summary",
        "receipts-and-payments-summary",
        "report-transformation-report",
        "statement-of-changes-in-equity",
        "summary",
        "supplier-statements-transactions",
        "supplier-statements-unpaid-invoices",
        "supplier-summary",
        "tax-reconciliation",
        "tax-summary",
        "trial-balance",
        "amortization-calculation-worksheet",
    }
)

# Semantics attached to the legacy report tools so names cannot imply authority.
REPORT_SEMANTICS: dict[str, dict[str, Any]] = {
    "aged_receivables": {
        "data_class": "current_state",
        "historical": False,
        "authoritative": False,
        "is_aged_report": False,
        "note": "Current outstanding customer balances from /customers. NOT an aged "
        "receivables report and NOT as at any past date. For a labelled "
        "as-at reconstruction use reconstructed_aged_receivables.",
    },
    "aged_payables": {
        "data_class": "current_state",
        "historical": False,
        "authoritative": False,
        "is_aged_report": False,
        "note": "Current outstanding supplier balances from /suppliers. NOT an aged "
        "payables report and NOT as at any past date. For a labelled as-at "
        "reconstruction use reconstructed_aged_payables.",
    },
    "bank_balances": {
        "data_class": "current_state",
        "historical": False,
        "authoritative": True,
        "note": "Current bank and cash balances as Manager reports them today. "
        "Not as at a past date; use ledger_transactions for history.",
    },
    "trial_balance": {
        "data_class": "raw_transaction_feed",
        "historical": True,
        "authoritative": False,
        "is_official_report": False,
        "note": "Raw /trial-balance-transactions rows (account fields are null). Not "
        "Manager's Trial Balance report. Use reconstructed_trial_balance.",
    },
    "profit_and_loss": {
        "data_class": "raw_transaction_feed",
        "historical": True,
        "authoritative": False,
        "is_official_report": False,
        "note": "Raw /profit-and-loss-statement-transactions rows (account fields are "
        "null). Not Manager's Profit and Loss Statement. Use "
        "reconstructed_profit_and_loss.",
    },
    "balance_sheet": {
        "data_class": "raw_transaction_feed",
        "historical": True,
        "authoritative": False,
        "is_official_report": False,
        "note": "Raw /balance-sheet-transactions rows (account fields are null). Not "
        "Manager's Balance Sheet.",
    },
    "tax_summary": {
        "data_class": "raw_transaction_feed",
        "historical": False,
        "authoritative": False,
        "is_official_report": False,
        "note": "Raw /tax-summary-transactions rows. Accepts no date parameters and is "
        "not a VAT return. For dated tax rows use ledger_transactions "
        "with tax_only=true.",
    },
}

# Review of existing tools (Phase 7): what each one really is.
EXISTING_TOOL_AUTHORITY: dict[str, dict[str, str]] = {
    "aged_receivables": {"class": "current_state", "note": "customer current balances, not ageing"},
    "aged_payables": {"class": "current_state", "note": "supplier current balances, not ageing"},
    "bank_balances": {"class": "current_state", "note": "bank/cash balances today"},
    "trial_balance": {"class": "raw_transaction_feed", "note": "not a report; no account per row"},
    "profit_and_loss": {
        "class": "raw_transaction_feed",
        "note": "not a report; no account per row",
    },
    "balance_sheet": {"class": "raw_transaction_feed", "note": "not a report; no account per row"},
    "tax_summary": {"class": "raw_transaction_feed", "note": "no dates; not a VAT return"},
    "general_ledger_summary": {
        "class": "partial_calculation",
        "note": "sums receipt/payment/journal lines only; excludes invoices; not a trial balance",
    },
    "account_ledger": {
        "class": "historical_transaction_data",
        "note": "excludes invoice amounts; ledger_transactions is more complete",
    },
    "bank_activity": {"class": "historical_transaction_data", "note": "bank transactions"},
    "reconcile_period": {
        "class": "reconstructed",
        "note": "diagnostic reconciliation, not an official report",
    },
    "list_records / get_record": {
        "class": "current_state",
        "note": "collection records as stored today",
    },
}

# (label, form type, feed endpoint or None, feed accepts dates, current-state collection or None)
_CATALOGUE_ROWS: list[tuple[str, str, str | None, bool, str | None]] = [
    ("Trial Balance", "trial-balance", "/trial-balance-transactions", True, None),
    ("Balance Sheet", "balance-sheet", "/balance-sheet-transactions", True, None),
    (
        "Profit and Loss Statement",
        "profit-and-loss-statement",
        "/profit-and-loss-statement-transactions",
        True,
        None,
    ),
    (
        "Profit and Loss actual vs budget",
        "profit-and-loss-statement-actual-vs-budget",
        "/profit-and-loss-statement-actual-vs-budget-transactions",
        True,
        None,
    ),
    ("Cash Flow Statement", "cash-flow-statement", None, False, None),
    ("Statement of Changes in Equity", "statement-of-changes-in-equity", None, False, None),
    ("Aged Receivables", "aged-receivables", None, False, "/customers"),
    ("Aged Payables", "aged-payables", None, False, "/suppliers"),
    ("Customer Summary", "customer-summary", None, False, "/customers"),
    ("Supplier Summary", "supplier-summary", None, False, "/suppliers"),
    ("Customer Statements", "customer-statements-transactions", None, False, None),
    ("Supplier Statements", "supplier-statements-transactions", None, False, None),
    ("General Ledger Summary", "general-ledger-summary", "/transactions", False, None),
    ("General Ledger Transactions", "general-ledger-transactions", "/transactions", False, None),
    ("Tax Summary", "tax-summary", "/tax-summary-transactions", False, None),
    ("Tax Reconciliation", "tax-reconciliation", "/tax-totals-transactions", False, None),
    ("Fixed Asset Summary", "fixed-asset-summary", None, False, "/fixed-assets"),
    ("Bank Account Summary", "bank-account-summary", None, False, "/bank-and-cash-accounts"),
    ("Capital Accounts Summary", "capital-accounts-summary", None, False, "/capital-accounts"),
    ("Expense Claims Summary", "expense-claims-summary", None, False, "/expense-claims"),
    ("Receipts and Payments Summary", "receipts-and-payments-summary", None, False, None),
    ("Summary (custom)", "summary", "/summary-transactions", True, None),
    ("Custom Report", "custom-report", None, False, None),
    (
        "Inventory Value Summary",
        "inventory-value-summary",
        "/inventory-item-transactions",
        True,
        "/inventory-items",
    ),
    (
        "Investments",
        "realized-investment-gains-summary",
        "/investment-transactions",
        True,
        "/investments",
    ),
]


def report_catalogue() -> dict[str, Any]:
    """Static, GET-derived catalogue of Manager report types and how they can be read."""
    entries = []
    for label, form, feed, feed_dates, current in _CATALOGUE_ROWS:
        reps: list[dict[str, Any]] = [
            {
                "kind": "settings_only",
                "method": "GET",
                "endpoint": f"/{form}-form/{{key}}",
                "note": "Stored report settings. Requires a known key; Manager has no "
                "list endpoint for report definitions. No calculated rows.",
            }
        ]
        if feed:
            reps.append(
                {
                    "kind": "raw_transaction_feed" if feed != LEDGER_PATH else "ledger_feed",
                    "method": "GET",
                    "endpoint": feed,
                    "date_params": ["fromDate", "toDate"] if feed_dates else [],
                    "as_at_param": None,
                    "note": "Rows, not a finished report."
                    if feed != LEDGER_PATH
                    else "Account-level double-entry rows. Manager ignores date params here; "
                    "the MCP filters client side.",
                }
            )
        if current:
            reps.append(
                {
                    "kind": "current_state",
                    "method": "GET",
                    "endpoint": current,
                    "note": "Balances as they are today. Never historical.",
                }
            )
        tools = ["get_report_definition"]
        if form == "trial-balance":
            tools += ["trial_balance", "reconstructed_trial_balance"]
        elif form == "profit-and-loss-statement":
            tools += ["profit_and_loss", "reconstructed_profit_and_loss"]
        elif form == "balance-sheet":
            tools += ["balance_sheet", "reconstructed_trial_balance"]
        elif form == "aged-receivables":
            tools += ["aged_receivables", "reconstructed_aged_receivables"]
        elif form == "aged-payables":
            tools += ["aged_payables", "reconstructed_aged_payables"]
        elif feed == LEDGER_PATH:
            tools += ["ledger_transactions"]
        elif form == "tax-summary":
            tools += ["tax_summary", "ledger_transactions (tax_only=true)"]
        if current and form in ("bank-account-summary",):
            tools += ["bank_balances"]
        entries.append(
            {
                "report": label,
                "manager_report_type": form,
                "representations": reps,
                "application_only_view": f"/{form}-view",
                "finished_output_via_api": False,
                "authoritative_calculated_output_available": False,
                "historical_as_at_supported_by_api": False,
                "mcp_tools": tools,
            }
        )
    return {
        "manager_version_validated": "26.8.4.3664",
        "summary": (
            "Manager's API does not return finished calculated reports. The "
            "*-view routes are application-only (HTTP 401 for API keys) and "
            "are deliberately not used. Only settings, raw feeds and current "
            "state are exposed; reconstructions are labelled authoritative: false."
        ),
        "reports": entries,
        "existing_tool_authority": EXISTING_TOOL_AUTHORITY,
    }


def report_semantics(name: str, *, period_applied: bool) -> dict[str, Any] | None:
    sem = REPORT_SEMANTICS.get(name)
    if sem is None:
        return None
    out = dict(sem)
    out["period_applied"] = period_applied
    return out


# helpers


def parse_date(value: str | None, name: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD, got {value!r}") from exc


def _pence(value: Any) -> int:
    return int(round(float(value or 0) * 100))


def _money(pence: int) -> float:
    return round(pence / 100, 2)


def meta(
    kind: str,
    *,
    method: str,
    as_at: str | None = None,
    period: dict[str, str | None] | None = None,
    ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "authoritative": False,
        "official_manager_report": False,
        "data_class": kind,
        "source": "Manager API GET /transactions",
        "calculation_method": method,
        "read_only": True,
    }
    if as_at:
        out["as_at"] = as_at
    if period:
        out["period"] = period
    if ledger:
        out["ledger"] = {
            k: ledger[k] for k in ("total_records", "rows_loaded", "pages", "complete")
        }
    return out


async def load_ledger(client: Any) -> dict[str, Any]:
    """Fetch every /transactions row (GET only) and report completeness."""
    rows: list[dict[str, Any]] = []
    skip, pages, total = 0, 0, None
    while pages < LEDGER_MAX_PAGES:
        body = await client.get(
            LEDGER_PATH,
            params={"pageSize": LEDGER_PAGE_SIZE, "skip": skip, "fields": LEDGER_FIELDS},
        )
        page = (body or {}).get("transactions") or []
        total = (body or {}).get("totalRecords", total)
        rows.extend(page)
        pages += 1
        skip += len(page)
        if not page or (total is not None and skip >= total):
            break
    return {
        "rows": rows,
        "total_records": total,
        "rows_loaded": len(rows),
        "pages": pages,
        "complete": total is not None and len(rows) == total,
    }


def filter_rows(
    rows: list[dict[str, Any]],
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    account: str | None = None,
    transaction_type: str | None = None,
    customer: str | None = None,
    supplier: str | None = None,
    bank_account: str | None = None,
    reference: str | None = None,
    tax_only: bool = False,
) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        d = r.get("date") or ""
        if from_date and d < from_date:
            continue
        if to_date and d > to_date:
            continue
        if account and account not in (
            r.get("account"),
            r.get("balanceSheetAccount"),
            r.get("profitAndLossStatementAccount"),
        ):
            continue
        if transaction_type and r.get("transaction") != transaction_type:
            continue
        if customer and r.get("customer") != customer:
            continue
        if supplier and r.get("supplier") != supplier:
            continue
        if bank_account and r.get("bankOrCashAccount") != bank_account:
            continue
        if reference and r.get("reference") != reference:
            continue
        if tax_only and not (r.get("taxCode") or r.get("taxAmount")):
            continue
        out.append(r)
    return out


# reconstructions


def build_trial_balance(
    rows: list[dict[str, Any]], as_at: str, period_start: str
) -> dict[str, Any]:
    bs: dict[str, int] = defaultdict(int)
    pl: dict[str, int] = defaultdict(int)
    other: dict[str, int] = defaultdict(int)
    prior_pl = 0
    used = 0
    for r in rows:
        d = r.get("date") or ""
        if d > as_at:
            continue
        used += 1
        amt = _pence(r.get("amount"))
        if r.get("balanceSheetAccount"):
            bs[r["balanceSheetAccount"]] += amt
        elif r.get("profitAndLossStatementAccount"):
            if d >= period_start:
                pl[r["profitAndLossStatementAccount"]] += amt
            else:
                prior_pl += amt
        else:
            other[r.get("account") or "(none)"] += amt
    lines = []
    for cls, book in (("balance_sheet", bs), ("profit_and_loss", pl), ("unclassified", other)):
        for acc, v in sorted(book.items()):
            if v:
                lines.append(
                    {
                        "account": acc,
                        "class": cls,
                        "debit": _money(v) if v > 0 else 0.0,
                        "credit": _money(-v) if v < 0 else 0.0,
                        "net": _money(v),
                    }
                )
    if prior_pl:
        lines.append(
            {
                "account": "(profit and loss before period start)",
                "class": "prior_periods",
                "debit": _money(prior_pl) if prior_pl > 0 else 0.0,
                "credit": _money(-prior_pl) if prior_pl < 0 else 0.0,
                "net": _money(prior_pl),
            }
        )
    dr = sum(_pence(x["debit"]) for x in lines)
    cr = sum(_pence(x["credit"]) for x in lines)
    return {
        "lines": lines,
        "total_debit": _money(dr),
        "total_credit": _money(cr),
        "difference": _money(dr - cr),
        "balanced": dr == cr,
        "rows_used": used,
    }


def build_profit_and_loss(
    rows: list[dict[str, Any]], from_date: str, to_date: str
) -> dict[str, Any]:
    acc: dict[str, int] = defaultdict(int)
    for r in rows:
        d = r.get("date") or ""
        pa = r.get("profitAndLossStatementAccount")
        if pa and from_date <= d <= to_date:
            acc[pa] += _pence(r.get("amount"))
    lines = [
        {"account": a, "net_debit": _money(v), "profit_effect": _money(-v)}
        for a, v in sorted(acc.items())
        if v
    ]
    total = sum(acc.values())
    return {
        "lines": lines,
        "net_profit": _money(-total),
        "note": "Profit = credits minus debits on P&L accounts. Manager's group "
        "and subtotal layout is not available from the API.",
    }


def _bucket(days: int) -> str:
    for label, lo, hi in AGE_BUCKETS:
        if lo <= days <= hi:
            return label
    return AGE_BUCKETS[-1][0]


def build_ageing(
    rows: list[dict[str, Any]], kind: str, as_at: str, include_zero: bool = False
) -> dict[str, Any]:
    customers = kind == "customers"
    control_account = "Accounts receivable" if customers else "Accounts payable"
    party_key = "customer" if customers else "supplier"
    sign = 1 if customers else -1  # positive always means "owed"
    per: dict[str, list[tuple[str, int, Any]]] = defaultdict(list)
    control = 0
    for r in rows:
        if r.get("balanceSheetAccount") != control_account or (r.get("date") or "") > as_at:
            continue
        amt = sign * _pence(r.get("amount"))
        control += amt
        per[r.get(party_key) or "(no party)"].append((r["date"], amt, r.get("reference")))
    asd = dt.date.fromisoformat(as_at)
    parties = []
    totals: dict[str, int] = defaultdict(int)
    for name, items in per.items():
        items.sort(key=lambda t: t[0])
        open_items: list[list[Any]] = []  # [date, remaining]
        credit = 0
        for d, amt, _ref in items:
            if amt > 0:
                use = min(credit, amt)
                credit -= use
                if amt - use:
                    open_items.append([d, amt - use])
            else:
                need = -amt
                while need and open_items:
                    take = min(open_items[0][1], need)
                    open_items[0][1] -= take
                    need -= take
                    if open_items[0][1] == 0:
                        open_items.pop(0)
                credit += need
        balance = sum(o[1] for o in open_items) - credit
        if balance == 0 and not include_zero:
            continue
        buckets: dict[str, int] = defaultdict(int)
        for d, rem in open_items:
            buckets[_bucket((asd - dt.date.fromisoformat(d)).days)] += rem
        if credit:
            buckets["unallocated_credit"] -= credit
        for k, v in buckets.items():
            totals[k] += v
        parties.append(
            {
                "name": name,
                "balance": _money(balance),
                "buckets": {k: _money(v) for k, v in buckets.items()},
                "oldest_open_date": open_items[0][0] if open_items else None,
            }
        )
    parties.sort(key=lambda p: -abs(p["balance"]))
    party_total = (
        sum(_pence(p["balance"]) for p in parties)
        if not include_zero
        else sum(_pence(p["balance"]) for p in parties)
    )
    return {
        "parties": parties,
        "party_count": len(parties),
        "bucket_totals": {k: _money(v) for k, v in totals.items()},
        "party_total": _money(party_total),
        "control_account": control_account,
        "control_account_total": _money(control),
        "party_total_matches_control": party_total == control,
        "ageing_basis": "days since transaction date (Manager's own report uses due dates)",
        "allocation_basis": "oldest-first per party; Manager's report uses actual allocations",
    }


# tools


def register_reporting_tools(mcp: Any, get_client: Callable[[], Any]) -> None:
    """Register GET-only reporting tools on the FastMCP instance."""

    @mcp.tool(
        description=(
            "Read-only catalogue of Manager report types: which API representation exists "
            "(settings only, raw feed, current state), which date parameters exist, which "
            "views are application-only, and which MCP tool covers each. Also classifies the "
            "legacy report tools (current state vs raw feed vs reconstruction)."
        )
    )
    async def manager_report_catalogue() -> dict[str, Any]:
        return report_catalogue()

    @mcp.tool(
        description=(
            "Read-only GET of a stored Manager report definition (settings only, never "
            "calculated rows). report_type is e.g. 'aged-receivables' or 'trial-balance' "
            "(without '-form'); key is the definition GUID. Manager has no list endpoint "
            "for definitions, so the key must be known. Never creates or edits definitions."
        )
    )
    async def get_report_definition(report_type: str, key: str) -> dict[str, Any]:
        rtype = report_type.strip().lower().removesuffix("-form")
        if rtype not in REPORT_FORM_TYPES:
            raise ValueError(
                f"Unknown or unsupported report type {report_type!r}. "
                "Use manager_report_catalogue for valid types."
            )
        if not _KEY_RE.match(key or ""):
            raise ValueError("key must be a Manager GUID")
        path = f"/{rtype}-form/{key}"
        body = await get_client().get(path)
        return {
            "report_type": rtype,
            "key": key,
            "endpoint": path,
            "method": "GET",
            "data_class": "report_settings",
            "authoritative": False,
            "contains_calculated_rows": False,
            "read_only": True,
            "definition": body,
        }

    @mcp.tool(
        description=(
            "Historical transaction data from Manager's account-level /transactions ledger "
            "(GET only). Every row names its account, type, counterparty, tax fields, debit, "
            "credit and signed amount (debit positive). Manager ignores dates on this endpoint, "
            "so from_date/to_date (YYYY-MM-DD) are filtered here; the full ledger is fetched "
            "and completeness reported. Results page with skip/limit (default 500, max 5000). "
            "Not a finished report; not an as-at balance."
        )
    )
    async def ledger_transactions(
        from_date: str | None = None,
        to_date: str | None = None,
        account: str | None = None,
        transaction_type: str | None = None,
        customer: str | None = None,
        supplier: str | None = None,
        bank_account: str | None = None,
        reference: str | None = None,
        tax_only: bool = False,
        skip: int = 0,
        limit: int = 500,
    ) -> dict[str, Any]:
        fd, td = parse_date(from_date, "from_date"), parse_date(to_date, "to_date")
        if not 1 <= limit <= 5000 or skip < 0:
            raise ValueError("limit must be 1..5000 and skip >= 0")
        led = await load_ledger(get_client())
        rows = filter_rows(
            led["rows"],
            from_date=fd,
            to_date=td,
            account=account,
            transaction_type=transaction_type,
            customer=customer,
            supplier=supplier,
            bank_account=bank_account,
            reference=reference,
            tax_only=tax_only,
        )
        page = rows[skip : skip + limit]
        end = skip + len(page)
        return {
            "data_class": "historical_transaction_data",
            "authoritative": False,
            "official_manager_report": False,
            "source": "Manager API GET /transactions",
            "read_only": True,
            "date_filter": "client_side",
            "period": {"from": fd, "to": td},
            "ledger": {k: led[k] for k in ("total_records", "rows_loaded", "pages", "complete")},
            "total_matching": len(rows),
            "returned": len(page),
            "skip": skip,
            "next_skip": end if end < len(rows) else None,
            "net_amount_matching": _money(sum(_pence(r.get("amount")) for r in rows)),
            "transactions": page,
        }

    @mcp.tool(
        description=(
            "RECONSTRUCTED trial balance from the /transactions ledger as at a date "
            "(authoritative: false; not Manager's Trial Balance). Balance sheet accounts are "
            "cumulative to as_at; P&L accounts cover period_start..as_at (default 1 Jan of "
            "the as_at year, an assumption); earlier P&L is one prior-periods line. Reports "
            "whether debits equal credits."
        )
    )
    async def reconstructed_trial_balance(
        as_at: str, period_start: str | None = None
    ) -> dict[str, Any]:
        a = parse_date(as_at, "as_at")
        if a is None:
            raise ValueError("as_at is required (YYYY-MM-DD)")
        ps = parse_date(period_start, "period_start") or f"{a[:4]}-01-01"
        led = await load_ledger(get_client())
        out = meta(
            "reconstructed",
            method="cumulative sums of signed amounts by account from ledger rows dated <= as_at",
            as_at=a,
            period={"pl_from": ps, "pl_to": a},
            ledger=led,
        )
        out.update(build_trial_balance(led["rows"], a, ps))
        return out

    @mcp.tool(
        description=(
            "RECONSTRUCTED profit and loss by account for a period from the /transactions "
            "ledger (authoritative: false; not Manager's Profit and Loss Statement, no "
            "group layout)."
        )
    )
    async def reconstructed_profit_and_loss(from_date: str, to_date: str) -> dict[str, Any]:
        fd, td = parse_date(from_date, "from_date"), parse_date(to_date, "to_date")
        if fd is None or td is None:
            raise ValueError("from_date and to_date are required (YYYY-MM-DD)")
        led = await load_ledger(get_client())
        out = meta(
            "reconstructed",
            method="sum of P&L account rows in the period",
            period={"from": fd, "to": td},
            ledger=led,
        )
        out.update(build_profit_and_loss(led["rows"], fd, td))
        return out

    async def _aged(kind: str, as_at: str, include_zero: bool) -> dict[str, Any]:
        a = parse_date(as_at, "as_at")
        if a is None:
            raise ValueError("as_at is required (YYYY-MM-DD)")
        led = await load_ledger(get_client())
        out = meta(
            "reconstructed",
            as_at=a,
            ledger=led,
            method=(
                f"per-{kind[:-1]} balances from Accounts {'receivable' if kind == 'customers' else 'payable'} "  # noqa: E501
                "ledger rows dated <= as_at; oldest-first allocation; NOT Manager's ageing report"
            ),
        )
        out.update(build_ageing(led["rows"], kind, a, include_zero))
        return out

    @mcp.tool(
        description=(
            "RECONSTRUCTED aged receivables as at a date from the /transactions ledger "
            "(authoritative: false; NOT Manager's Aged Receivables report). Ages by "
            "transaction date with oldest-first allocation; returns the control account "
            "total and whether party balances sum to it. Never treat as official."
        )
    )
    async def reconstructed_aged_receivables(
        as_at: str, include_zero: bool = False
    ) -> dict[str, Any]:
        return await _aged("customers", as_at, include_zero)

    @mcp.tool(
        description=(
            "RECONSTRUCTED aged payables as at a date from the /transactions ledger "
            "(authoritative: false; NOT Manager's Aged Payables report). Same method and "
            "caveats as reconstructed_aged_receivables."
        )
    )
    async def reconstructed_aged_payables(as_at: str, include_zero: bool = False) -> dict[str, Any]:
        return await _aged("suppliers", as_at, include_zero)


__all__ = [
    "REPORT_FORM_TYPES",
    "REPORT_SEMANTICS",
    "build_ageing",
    "build_profit_and_loss",
    "build_trial_balance",
    "filter_rows",
    "load_ledger",
    "parse_date",
    "register_reporting_tools",
    "report_catalogue",
    "report_semantics",
]

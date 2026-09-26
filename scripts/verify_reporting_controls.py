"""Live validation of the read-only reporting layer against YOUR Manager instance (GET only).

Run (credentials come from the environment, for example an env file you keep private):

    uv run --env-file .env python scripts/verify_reporting_controls.py --as-at 2025-12-31 \\
        --expected-receivables 1000.00 --expected-payables 500.00 \\
        --control "Cash & cash equivalents=250.00"

Nothing about any particular business is built in. Control figures are optional and come from
you, normally read off Manager's own reports for the same date. Reconstructions are compared
with them but are never treated as authoritative, even when they match.

Without control figures the script still checks structure: the ledger fetch is complete, the
reconstructed trial balance balances, party balances tie to the receivables/payables control
accounts, tools that cannot honour a date reject it, and every HTTP request made was a GET.
Control amounts follow the ledger sign convention (debit positive, so a payables balance is
negative for --control); --expected-payables takes the positive amount owed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from manager_mcp import server
from manager_mcp.client import ManagerClient

CALLS: list[tuple[str, str]] = []


def _record_requests() -> None:
    original = ManagerClient._send

    async def spy(self, method, path, **kw):
        CALLS.append((method.upper(), path))
        return await original(self, method, path, **kw)

    ManagerClient._send = spy  # type: ignore[method-assign]


async def call(name: str, args: dict | None = None):
    try:
        res = await server.mcp.call_tool(name, args or {})
        return res.structured_content, None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:160]


def _parse_controls(items: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in items:
        name, sep, amount = item.rpartition("=")
        if not sep or not name.strip():
            raise SystemExit(f"--control must look like 'Account name=amount', got {item!r}")
        out[name.strip()] = float(amount)
    return out


def _matches(label: str, got: float | None, expected: float) -> bool:
    ok = got is not None and abs(got - expected) < 0.005
    verdict = "MATCH" if ok else "DIFFERENT"
    print(f"control  {label:32} reconstructed={got}  expected={expected}  {verdict}")
    return ok


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate the reporting tools against your Manager.")
    p.add_argument("--as-at", required=True, help="date to reconstruct as at (YYYY-MM-DD)")
    p.add_argument("--period-from", help="P&L period start (default: 1 January of the as-at year)")
    p.add_argument("--expected-profit", type=float, help="official profit for the period")
    p.add_argument("--expected-receivables", type=float, help="official receivables total")
    p.add_argument("--expected-payables", type=float, help="official payables total (positive)")
    p.add_argument(
        "--control",
        action="append",
        default=[],
        help="'Account name=amount' in ledger sign; repeatable",
    )
    return p.parse_args()


async def main() -> int:
    a = _args()
    controls = _parse_controls(a.control)
    start = a.period_from or f"{a.as_at[:4]}-01-01"
    _record_requests()
    server.register_write_tools()
    server.register_task_tools()
    rows: list[tuple[str, str, str, str, str]] = []
    failures: list[str] = []

    def rec(tool: str, params: str, result: str, authoritative: str, historical: str) -> None:
        rows.append((tool, params, result, authoritative, historical))

    out, _ = await call("manager_report_catalogue")
    rec("manager_report_catalogue", "-", f"{len(out['reports'])} report types", "n/a", "n/a")

    out, _ = await call(
        "ledger_transactions", {"from_date": start, "to_date": a.as_at, "limit": 5}
    )
    led = out["ledger"]
    if not (led["complete"] and led["rows_loaded"] == led["total_records"]):
        failures.append("ledger fetch incomplete")
    rec(
        "ledger_transactions",
        f"{start}..{a.as_at}",
        f"{out['total_matching']} rows, complete={led['complete']} "
        f"({led['rows_loaded']}/{led['total_records']})",
        "raw rows",
        "yes",
    )

    tb, _ = await call("reconstructed_trial_balance", {"as_at": a.as_at})
    by = {ln["account"]: ln["net"] for ln in tb["lines"]}
    if not tb["balanced"]:
        failures.append("trial balance not balanced")
    rec(
        "reconstructed_trial_balance",
        f"as_at={a.as_at}",
        f"balanced={tb['balanced']} diff={tb['difference']}",
        "no",
        "yes",
    )
    for acct, expected in controls.items():
        if not _matches(acct, by.get(acct), expected):
            failures.append(f"control differs: {acct}")

    pl, _ = await call("reconstructed_profit_and_loss", {"from_date": start, "to_date": a.as_at})
    rec(
        "reconstructed_profit_and_loss",
        f"{start}..{a.as_at}",
        f"net_profit={pl['net_profit']}",
        "no",
        "yes",
    )
    if a.expected_profit is not None:
        if not _matches("profit", pl["net_profit"], a.expected_profit):
            failures.append("profit differs")

    for label, tool, expected in (
        ("receivables", "reconstructed_aged_receivables", a.expected_receivables),
        ("payables", "reconstructed_aged_payables", a.expected_payables),
    ):
        res, _ = await call(tool, {"as_at": a.as_at})
        rec(
            tool,
            f"as_at={a.as_at}",
            f"total={res['party_total']} control_account={res['control_account_total']} "
            f"ties={res['party_total_matches_control']}",
            "no",
            "yes",
        )
        if not res["party_total_matches_control"]:
            failures.append(f"{tool}: party balances do not tie to the control account")
        if expected is not None and not _matches(label, res["party_total"], expected):
            failures.append(f"{label} total differs")

    for name in ("aged_receivables", "aged_payables", "bank_balances", "tax_summary"):
        _, err = await call(name, {"to_date": a.as_at})
        rejected = bool(err and "does not support" in err)
        rec(name, f"to_date={a.as_at}", f"rejected={rejected}", "-", "current state only")
        if not rejected:
            failures.append(f"{name} accepted an as-at date")

    bad = [c for c in CALLS if c[0] != "GET"]
    print(f"HTTP requests made: {len(CALLS)}; non-GET: {len(bad)}")
    if bad:
        failures.append(f"non-GET requests: {bad}")

    print("\n| Tool | Params | Result | Authoritative? | Historical? |")
    print("|---|---|---|---|---|")
    for r in rows:
        print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
    print("\nFAILURES:", json.dumps(failures) if failures else "none")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Build the Manager capability matrix from an OpenAPI /api2 document (offline, read-only).

Usage: python scripts/build_capability_matrix.py LIVE_SPEC.json OUT.json
"""

from __future__ import annotations

import json
import sys

from manager_mcp.resources import all_resources

BASE_QUERY = {"fields", "pageSize", "skip", "sortBy", "sortByDesc", "term"}
DEDICATED = {
    "/transactions": [
        "ledger_transactions",
        "reconstructed_trial_balance",
        "reconstructed_profit_and_loss",
        "reconstructed_aged_receivables",
        "reconstructed_aged_payables",
    ],
    "/trial-balance-transactions": ["trial_balance"],
    "/profit-and-loss-statement-transactions": ["profit_and_loss"],
    "/balance-sheet-transactions": ["balance_sheet"],
    "/tax-summary-transactions": ["tax_summary"],
}


def kind(path: str, method: str) -> str:
    if path.endswith("-transactions"):
        return "transaction_feed"
    if path == "/transactions":
        return "ledger_feed"
    if path.endswith("-form/{key}"):
        return {"get": "form_read", "put": "form_update", "delete": "form_delete"}.get(
            method, "form"
        )
    if path.endswith("-form"):
        return "form_create" if method == "post" else "form"
    return "collection_or_special"


def main(spec_path: str, out_path: str) -> None:
    spec = json.load(open(spec_path))
    tools = {r.path: ["list_records/get_record:" + r.name] for r in all_resources()}
    entries = []
    for path, ops in sorted(spec["paths"].items()):
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            q = {p["name"]: p for p in op.get("parameters", []) if p.get("in") == "query"}
            sort_enum = (q.get("sortBy", {}).get("schema", {}) or {}).get("enum")
            entries.append(
                {
                    "endpoint": path,
                    "method": method.upper(),
                    "operation": op.get("operationId"),
                    "kind": kind(path, method),
                    "read_only": method == "get",
                    "query_params": sorted(q),
                    "fromDate": "fromDate" in q,
                    "toDate": "toDate" in q,
                    "asAtDate": any(n.lower().replace("_", "") in ("asat", "asatdate") for n in q),
                    "fields": "fields" in q,
                    "paginated": "pageSize" in q and "skip" in q,
                    "filter": "term" if "term" in q else None,
                    "sortable": bool(sort_enum) or "sortBy" in q,
                    "sort_fields": sort_enum,
                    "extra_filters": sorted(set(q) - BASE_QUERY),
                    "data_nature": (
                        "current_state_or_stored_record"
                        if method == "get"
                        and kind(path, method) != "transaction_feed"
                        and kind(path, method) != "ledger_feed"
                        else "historical_rows"
                        if method == "get"
                        else "write"
                    ),
                    "mcp_tools": (DEDICATED.get(path) or tools.get(path) or [])
                    if method == "get"
                    else [],
                }
            )
    doc = {
        "manager_version": spec["info"]["version"],
        "path_count": len(spec["paths"]),
        "endpoint_method_count": len(entries),
        "notes": [
            "Generated offline from the live /api2 description.",
            "Report *-view routes are not in the API description (application only).",
            "Only 8 GET endpoints accept fromDate/toDate; none accepts an as-at date.",
            "Maximum page size is not declared by Manager; 3000 verified accepted on /transactions.",  # noqa: E501
        ],
        "entries": entries,
    }
    json.dump(doc, open(out_path, "w"), separators=(",", ":"))
    g = [e for e in entries if e["method"] == "GET"]
    print(
        "entries",
        len(entries),
        "GET",
        len(g),
        "with fromDate",
        sum(e["fromDate"] for e in g),
        "with asAt",
        sum(e["asAtDate"] for e in g),
        "paginated GET",
        sum(e["paginated"] for e in g),
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""End to end checks that write and delete access cannot be exposed by accident.

Each case starts a fresh interpreter, because tools register once per process, sets the
scope environment variables, registers tools exactly as the server does and then inspects
the real tool registry and calls tools through the MCP layer. No Manager instance is used.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

DOMAIN = "quotes,orders,parties,items,sales,purchases,banking,payroll,ledger"

_CHILD = textwrap.dedent(
    """
    import asyncio, json, os
    from manager_mcp import server as s

    async def main():
        s.reset_client()
        s.register_task_tools()
        s.register_write_tools()
        tools = await s.mcp.list_tools()
        out = {
            "tools": [
                {
                    "name": t.name,
                    "destructive": bool(t.annotations and t.annotations.destructiveHint),
                    "read_only": bool(t.annotations and t.annotations.readOnlyHint),
                }
                for t in tools
            ],
            "calls": {},
        }
        for label, (name, args) in json.loads(os.environ["CHILD_CALLS"]).items():
            try:
                await s.mcp.call_tool(name, args)
                out["calls"][label] = "ok"
            except Exception as exc:
                out["calls"][label] = str(exc)
        print("RESULT:" + json.dumps(out))

    asyncio.run(main())
    """
)


def _run(write: str = "", delete: str = "", calls: dict | None = None) -> dict:
    env = {
        **os.environ,
        "MANAGER_API_URL": "http://127.0.0.1:9/api2",
        "MANAGER_API_KEY": "test-key",
        "MANAGER_MCP_WRITE_SCOPES": write,
        "MANAGER_MCP_DELETE_SCOPES": delete,
        "CHILD_CALLS": json.dumps(calls or {}),
    }
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], env=env, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = next(x for x in proc.stdout.splitlines() if x.startswith("RESULT:"))
    return json.loads(line[len("RESULT:") :])


def _delete_like(tools: list[dict]) -> list[str]:
    return [
        t["name"]
        for t in tools
        if t["destructive"]
        or t["name"].startswith("delete_")
        or t["name"] in {"snapshot_and_void", "void_document"}
    ]


@pytest.mark.parametrize("write", [DOMAIN, "raw"])
def test_write_scopes_never_expose_delete_or_destructive_tools(write: str) -> None:
    result = _run(write=write)
    assert _delete_like(result["tools"]) == []


def test_delete_scope_tools_appear_only_with_a_delete_scope() -> None:
    with_delete = _run(write=DOMAIN, delete="ledger")
    assert _delete_like(with_delete["tools"]) != []
    assert {t["name"] for t in with_delete["tools"] if t["destructive"]} <= {
        "delete_journal_entry",
        "delete_depreciation_entry",
        "delete_amortization_entry",
        "snapshot_and_void",
        "void_document",
    }


def test_default_configuration_registers_only_the_30_read_tools() -> None:
    result = _run()
    names = {t["name"] for t in result["tools"]}
    assert len(names) == 30
    assert not [t for t in result["tools"] if t["destructive"]]
    assert not {"apply_correction", "propose_correction", "snapshot_and_void"} & names


def test_correction_tools_cannot_cross_into_a_scope_that_is_not_enabled() -> None:
    base = {"proposal_token": "x", "fields": {}}
    result = _run(
        write="parties",
        calls={
            "sales": ["apply_correction", {**base, "resource": "sales_invoices"}],
            "banking": ["apply_correction", {**base, "resource": "payments"}],
            "propose": ["propose_correction", {"resource": "sales_invoices", "fields": {}}],
        },
    )
    names = {t["name"] for t in result["tools"]}
    assert "create_sales_invoice" not in names
    assert "snapshot_and_void" not in names
    for label in ("sales", "banking", "propose"):
        message = result["calls"][label]
        assert message != "ok"
        assert "MANAGER_MCP_WRITE_SCOPES" in message or "requires scope" in message
        assert "Manager.io is not reachable" not in message


def test_a_delete_only_configuration_cannot_write() -> None:
    result = _run(
        delete="ledger",
        calls={
            "create": [
                "apply_correction",
                {"proposal_token": "x", "resource": "journal_entries", "fields": {}},
            ]
        },
    )
    message = result["calls"]["create"]
    assert message != "ok"
    assert "MANAGER_MCP_WRITE_SCOPES" in message
    assert "Manager.io is not reachable" not in message


def test_read_tools_are_marked_read_only_and_no_other_tool_is() -> None:
    default = _run()
    everything = _run(write=DOMAIN, delete=DOMAIN)
    default_names = {t["name"] for t in default["tools"]}
    assert len(default_names) == 30
    assert all(t["read_only"] and not t["destructive"] for t in default["tools"])
    read_only_names = {t["name"] for t in everything["tools"] if t["read_only"]}
    assert read_only_names == default_names
    writers = [t for t in everything["tools"] if t["name"] not in default_names]
    assert len(writers) == 126 - 30
    assert not any(t["read_only"] for t in writers)


def test_read_only_registrar_delegates_other_attributes() -> None:
    from manager_mcp.tool_annotations import ReadOnlyTools

    class Fake:
        marker = "delegated"

        def tool(self, *args: object, **kwargs: object) -> dict:
            return kwargs

    wrapped = ReadOnlyTools(Fake())
    assert wrapped.marker == "delegated"
    kwargs = wrapped.tool(description="x")
    assert kwargs["annotations"]["readOnlyHint"] is True
    explicit = wrapped.tool(description="x", annotations={"readOnlyHint": False})
    assert explicit["annotations"] == {"readOnlyHint": False}

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Audit log append/read round-trip (no live Manager, no network)."""

from __future__ import annotations

from pathlib import Path

from manager_mcp.audit_log import AuditEntry, new_correlation_id, read_events, record_event


def test_record_and_read_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "audit.jsonl"
    correlation_id = new_correlation_id()
    entry = AuditEntry(
        operation="update",
        resource="purchase_invoices",
        key="inv-1",
        before={"Reference": "old"},
        submitted={"Reference": "new"},
        after={"Reference": "new"},
        warnings=[],
        correlation_id=correlation_id,
        status="ok",
    )
    written_path = record_event(entry, path=path)
    assert written_path == path
    events = read_events(path)
    assert len(events) == 1
    assert events[0]["correlation_id"] == correlation_id
    assert events[0]["before"] == {"Reference": "old"}
    assert events[0]["after"] == {"Reference": "new"}


def test_read_events_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_events(tmp_path / "does-not-exist.jsonl") == []


def test_multiple_entries_append(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    for i in range(3):
        record_event(
            AuditEntry(
                operation="void",
                resource="payments",
                key=f"p{i}",
                before={},
                submitted={},
                after=None,
                warnings=[],
                correlation_id=new_correlation_id(),
                status="ok",
            ),
            path=path,
        )
    assert len(read_events(path)) == 3

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Append-only audit trail for corrective writes (before/after state).

Every corrective tool in `corrections.py` writes one entry here per
operation: what was submitted, what the record looked like before and
after, and a correlation id linking a propose/apply pair. This is the
"audit/logging information" the write-safety model requires and it never
touches Manager's own data -- it is purely local, additive record-keeping.

Never logs the Manager API key or MCP host secrets; entries only ever
contain Manager record bodies (which may still be business-sensitive --
callers control where the log file lives via MANAGER_MCP_AUDIT_LOG_PATH).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AUDIT_LOG_PATH_ENV = "MANAGER_MCP_AUDIT_LOG_PATH"
_DEFAULT_PATH = Path.home() / ".manager_mcp" / "audit_log.jsonl"


def audit_log_path() -> Path:
    raw = os.environ.get(AUDIT_LOG_PATH_ENV, "").strip()
    return Path(raw) if raw else _DEFAULT_PATH


@dataclass(frozen=True)
class AuditEntry:
    operation: str
    resource: str
    key: str | None
    before: Any
    submitted: Any
    after: Any
    warnings: list[str]
    correlation_id: str
    status: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "operation": self.operation,
            "resource": self.resource,
            "key": self.key,
            "status": self.status,
            "before": self.before,
            "submitted": self.submitted,
            "after": self.after,
            "warnings": self.warnings,
        }


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def record_event(entry: AuditEntry, *, path: Path | None = None) -> Path:
    """Append one audit entry as a JSON line. Creates parent dirs as needed."""
    target = path or audit_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry.to_dict(), default=str) + "\n")
    return target


def read_events(path: Path | None = None) -> list[dict[str, Any]]:
    """Test/inspection helper: read back every entry as a list of dicts."""
    target = path or audit_log_path()
    if not target.is_file():
        return []
    events = []
    with target.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""MCP annotations for read-only tools.

Clients use the readOnlyHint annotation to treat tools that cannot change anything more
permissively than tools that can. Write and delete tools carry their own annotations in
server.py. Tools registered through ReadOnlyTools default to the read-only annotations
unless a tool passes its own.
"""

from __future__ import annotations

from typing import Any

READ_ONLY_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

# For read-only tools that never contact Manager (they describe this server itself).
LOCAL_READ_ONLY_ANNOTATIONS: dict[str, bool] = {**READ_ONLY_ANNOTATIONS, "openWorldHint": False}


class ReadOnlyTools:
    """Wraps a FastMCP instance so that `.tool(...)` defaults to read-only annotations."""

    def __init__(self, mcp: Any, annotations: dict[str, bool] | None = None) -> None:
        self._mcp = mcp
        self._annotations = dict(annotations or READ_ONLY_ANNOTATIONS)

    def tool(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("annotations", dict(self._annotations))
        return self._mcp.tool(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        """Everything except tool() goes to the wrapped instance (for example list_tools)."""
        return getattr(self._mcp, name)


__all__ = ["LOCAL_READ_ONLY_ANNOTATIONS", "READ_ONLY_ANNOTATIONS", "ReadOnlyTools"]

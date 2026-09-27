# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Read-only server-identity diagnostics (`get_server_info`).

Exists so an agent can answer "which manager-mcp process am I actually
talking to" without guessing from response shapes: that is the exact question
that took an entire session to answer by hand once (stale `uvx`-launched package
vs. this local source tree). Every field here is either non-sensitive local
metadata (package version, source path, git commit) or already exposed
elsewhere (scopes, via `list_resources`); nothing here reaches Manager.
"""

from __future__ import annotations

import os
import subprocess
import sys as _sys
from datetime import datetime, timezone
from importlib import metadata as _importlib_metadata
from pathlib import Path
from typing import Any

from manager_mcp.scopes import WritePolicy

PACKAGE_NAME = "manager-mcp"  # MCP server and command name
DISTRIBUTION_NAME = "mcp-manager.io"  # PyPI distribution name
_PYPROJECT_VERSION_PREFIX = "version = "

# Captured once, at import time -- i.e. once per actual process, which is
# exactly what a dev-supervisor restart needs this to change on.
_PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat()
_PROCESS_PID = os.getpid()


def _version_from_pyproject(repo_root: Path) -> str | None:
    """Fallback for an unpublished/non-metadata dev checkout: read the
    `[project] version = "..."` line straight out of pyproject.toml."""
    pyproject = repo_root / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(_PYPROJECT_VERSION_PREFIX):
            value = stripped[len(_PYPROJECT_VERSION_PREFIX) :].strip()
            return value.strip('"').strip("'") or None
    return None


def resolve_version(repo_root: Path) -> str | None:
    """Canonical version, preferring installed package metadata (matches
    whatever was actually `pip`/`uv`-installed) over a raw pyproject.toml
    parse (matches an editable/dev checkout that metadata may not reflect
    yet). Never fabricates a version if neither source is available."""
    for dist in (DISTRIBUTION_NAME, PACKAGE_NAME):
        try:
            return _importlib_metadata.version(dist)
        except _importlib_metadata.PackageNotFoundError:
            continue
    return _version_from_pyproject(repo_root)


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    # Deliberately NOT `.strip() or None`: empty-but-successful output (e.g.
    # `git status --porcelain` on a clean tree) is meaningful data, not an
    # absence of it. Collapsing "" to None here would make a clean repo
    # indistinguishable from "git failed," which is exactly the ambiguity
    # this module exists to avoid.
    return result.stdout.strip()


def resolve_git_info(repo_root: Path) -> dict[str, Any]:
    """`git_sha`/`git_dirty` when this checkout is a git repo with git
    available; both keys are simply omitted otherwise (never an error;
    per-field, best-effort, matching the tool's "omit, don't fail" contract)."""
    info: dict[str, Any] = {}
    sha = _git(repo_root, "rev-parse", "HEAD")
    if sha:
        info["git_sha"] = sha
        short = _git(repo_root, "rev-parse", "--short", "HEAD")
        if short:
            info["git_sha_short"] = short
        status = _git(repo_root, "status", "--porcelain")
        # status == "" (empty, not None) means clean; None means git itself
        # failed/unavailable, in which case we say nothing rather than guess.
        if status is not None:
            info["git_dirty"] = bool(status)
    return info


def build_server_info(
    *,
    source_path: Path,
    policy: WritePolicy,
    transport: str = "stdio",
    tool_names: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble the `get_server_info` payload. Pure function (no MCP/HTTP
    dependency) so it's directly unit-testable without mocking FastMCP.

    `pid`/`process_started_at` identify the actual OS process answering this
    call -- the thing a dev-supervisor restart changes -- as distinct from
    `git_sha`/`git_dirty`, which only describe the checkout on disk and don't
    prove the running process has picked it up.
    """
    repo_root = source_path.parent.parent  # src/manager_mcp -> repo root
    info: dict[str, Any] = {
        "name": PACKAGE_NAME,
        "version": resolve_version(repo_root),
        "source_path": str(source_path),
        "transport": transport,
        "pid": _PROCESS_PID,
        "process_started_at": _PROCESS_STARTED_AT,
        "write_scopes_configured": sorted(policy.write_scopes),
        "delete_scopes_configured": sorted(policy.delete_scopes),
        "effective_write_scopes": sorted(policy.effective_write_scopes),
        "effective_delete_scopes": sorted(policy.effective_delete_scopes),
    }
    if tool_names is not None:
        info["registered_tool_count"] = len(tool_names)
        info["registered_tools"] = sorted(tool_names)
    info.update(resolve_git_info(repo_root))
    return info


def register_server_info_tool(mcp: Any, get_policy: Any, source_path: Path) -> None:
    """Register the get_server_info tool.

    source_path must be the server entrypoint file (server.py), so the reported
    source path, version and git state describe the running server checkout.
    """
    _server_info = _sys.modules[__name__]

    @mcp.tool(
        description=(
            "Identify exactly which manager-mcp process you're connected to: "
            "package version, local source path, git commit (+ dirty flag), pid, "
            "process start time, registered tool count/names, transport, and "
            "effective scopes. Read-only, no Manager API call. Run this after any "
            "source edit + reload to confirm the new code is actually live -- pid "
            "and process_started_at change on a real restart even when git_sha "
            "does not (uncommitted edits), which is the reliable signal."
        )
    )
    async def get_server_info() -> dict[str, Any]:
        tools = await mcp.list_tools()
        return _server_info.build_server_info(
            source_path=source_path,
            policy=get_policy(),
            tool_names=[t.name for t in tools],
        )

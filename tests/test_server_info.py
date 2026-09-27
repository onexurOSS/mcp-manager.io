# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Server-identity diagnostics: version resolution, git metadata, and the
`get_server_info` MCP tool. No live git SHA is asserted anywhere: fixtures
build throwaway repos/checkouts so results are machine-independent."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import respx

from manager_mcp.scopes import WritePolicy
from manager_mcp.server_info import (
    build_server_info,
    resolve_git_info,
    resolve_version,
)


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "f.txt").write_text("hello\n")
    _run_git(repo, "add", "f.txt")
    _run_git(repo, "commit", "-q", "-m", "init")
    return repo


# ── resolve_version ──────────────────────────────────────────────────────


def test_resolve_version_prefers_installed_package_metadata() -> None:
    # This project IS installed (editable) in the active venv, so the
    # canonical source is importlib.metadata, matching pyproject.toml's
    # own [project] version, not a value this test invents.
    import re

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    match = re.search(r'^version = "([^"]+)"', pyproject.read_text(), re.MULTILINE)
    assert match is not None
    expected = match.group(1)
    assert resolve_version(pyproject.parent) == expected


def test_resolve_version_falls_back_to_pyproject_when_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from importlib import metadata as importlib_metadata

    def _raise(_name: str) -> str:
        raise importlib_metadata.PackageNotFoundError()

    monkeypatch.setattr(importlib_metadata, "version", _raise)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "manager-mcp"\nversion = "9.9.9-test"\n'
    )
    assert resolve_version(tmp_path) == "9.9.9-test"


def test_resolve_version_none_when_neither_source_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from importlib import metadata as importlib_metadata

    def _raise(_name: str) -> str:
        raise importlib_metadata.PackageNotFoundError()

    monkeypatch.setattr(importlib_metadata, "version", _raise)
    assert resolve_version(tmp_path) is None  # no pyproject.toml in tmp_path either


# ── resolve_git_info ─────────────────────────────────────────────────────


def test_resolve_git_info_clean_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    info = resolve_git_info(repo)
    assert "git_sha" in info
    assert len(info["git_sha"]) == 40
    assert info["git_sha_short"] == info["git_sha"][: len(info["git_sha_short"])]
    assert info["git_dirty"] is False


def test_resolve_git_info_dirty_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "f.txt").write_text("changed\n")
    info = resolve_git_info(repo)
    assert info["git_dirty"] is True


def test_resolve_git_info_omits_fields_when_not_a_repo(tmp_path: Path) -> None:
    # Empty dir, no `git init`: must not raise, must not fabricate a SHA.
    info = resolve_git_info(tmp_path)
    assert info == {}


# ── build_server_info ────────────────────────────────────────────────────


def test_build_server_info_shape_and_no_secrets(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    source_path = repo / "src" / "manager_mcp" / "server.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("# placeholder\n")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "manager-mcp"\nversion = "1.2.3-test"\n'
    )
    policy = WritePolicy(
        write_scopes=frozenset({"sales", "banking"}),
        delete_scopes=frozenset(),
    )
    info = build_server_info(source_path=source_path, policy=policy)

    assert info["name"] == "manager-mcp"
    assert info["source_path"] == str(source_path)
    assert info["transport"] == "stdio"
    assert info["effective_write_scopes"] == ["banking", "sales"]
    assert info["effective_delete_scopes"] == []
    assert "git_sha" in info  # repo fixture always has a commit

    secret_markers = ("key", "secret", "token", "password", "cookie", "auth")
    # source_path is legitimately long (it's a filesystem path); every other
    # field is expected to be short and structured, so a long value there
    # would be a real red flag, e.g. an accidentally-embedded credential.
    for field_name, value in info.items():
        lowered = field_name.lower()
        assert not any(marker in lowered for marker in secret_markers), field_name
        if isinstance(value, str) and field_name != "source_path":
            assert len(value) < 80, f"{field_name} suspiciously long: {value!r}"


def test_build_server_info_identifies_the_actual_process(tmp_path: Path) -> None:
    """pid/process_started_at answer "is this actually a new process", which
    git_sha/git_dirty cannot: they describe the checkout on disk, not the
    running process, so they're identical before and after a restart when
    there's no new commit."""
    import os

    source_path = tmp_path / "src" / "manager_mcp" / "server.py"
    source_path.parent.mkdir(parents=True)
    policy = WritePolicy(write_scopes=frozenset(), delete_scopes=frozenset())
    info = build_server_info(source_path=source_path, policy=policy)
    assert info["pid"] == os.getpid()
    assert info["process_started_at"]


def test_build_server_info_includes_tool_inventory_when_given(tmp_path: Path) -> None:
    source_path = tmp_path / "src" / "manager_mcp" / "server.py"
    source_path.parent.mkdir(parents=True)
    policy = WritePolicy(write_scopes=frozenset(), delete_scopes=frozenset())
    info = build_server_info(
        source_path=source_path, policy=policy, tool_names=["b_tool", "a_tool"]
    )
    assert info["registered_tool_count"] == 2
    assert info["registered_tools"] == ["a_tool", "b_tool"]


def test_build_server_info_omits_tool_inventory_when_not_given(tmp_path: Path) -> None:
    source_path = tmp_path / "src" / "manager_mcp" / "server.py"
    source_path.parent.mkdir(parents=True)
    policy = WritePolicy(write_scopes=frozenset(), delete_scopes=frozenset())
    info = build_server_info(source_path=source_path, policy=policy)
    assert "registered_tool_count" not in info
    assert "registered_tools" not in info


def test_build_server_info_never_raises_without_git(tmp_path: Path) -> None:
    source_path = tmp_path / "src" / "manager_mcp" / "server.py"
    source_path.parent.mkdir(parents=True)
    policy = WritePolicy(write_scopes=frozenset(), delete_scopes=frozenset())
    info = build_server_info(source_path=source_path, policy=policy)  # no git repo here
    assert "git_sha" not in info
    assert "git_dirty" not in info
    assert info["name"] == "manager-mcp"


# ── get_server_info MCP tool ─────────────────────────────────────────────

BASE = "http://example.test/api2"


@pytest.fixture(autouse=True)
def _env_and_client(monkeypatch: pytest.MonkeyPatch):
    from manager_mcp.server import reset_client

    monkeypatch.setenv("MANAGER_API_URL", BASE)
    monkeypatch.setenv("MANAGER_API_KEY", "test-key")
    reset_client()
    yield
    reset_client()


@pytest.mark.asyncio
@respx.mock
async def test_get_server_info_tool_is_read_only_and_shape() -> None:
    """No Manager route is mocked at all: if get_server_info made any
    Manager API call, respx would raise on the unmocked request and this
    test would fail, proving the tool never touches Manager."""
    from manager_mcp.server import mcp

    result = await mcp.call_tool("get_server_info", {})
    assert not result.is_error, result
    out = result.structured_content
    assert out is not None
    assert out["name"] == "manager-mcp"
    assert out["transport"] == "stdio"
    assert "effective_write_scopes" in out
    assert "effective_delete_scopes" in out
    assert "version" in out
    assert "source_path" in out
    assert "pid" in out
    assert "process_started_at" in out
    assert out["registered_tool_count"] >= 1
    assert "get_server_info" in out["registered_tools"]


@pytest.mark.asyncio
async def test_get_server_info_reports_configured_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from manager_mcp.server import mcp, reset_client

    monkeypatch.setenv("MANAGER_MCP_WRITE_SCOPES", "sales,banking")
    monkeypatch.setenv("MANAGER_MCP_DELETE_SCOPES", "")
    reset_client()
    try:
        result = await mcp.call_tool("get_server_info", {})
        out = result.structured_content
        assert out["write_scopes_configured"] == ["banking", "sales"]
        assert out["effective_write_scopes"] == ["banking", "sales"]
    finally:
        reset_client()


def test_server_declares_name_and_version() -> None:
    from manager_mcp.server import mcp

    assert mcp.name == "manager-mcp"
    assert mcp.version  # non-empty; exact value already covered above

"""sdist must not ship dev-only paths."""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PREFIXES = (
    "specs/",
    "docs/",
    "skills/",
    "skills-lock.json",
    "uv.lock",
    ".env.example",
    ".github/",
    "mcpb/",
)


@pytest.mark.parametrize("target", ["sdist"])
def test_sdist_excludes_dev_paths(target: str) -> None:
    subprocess.run(
        ["uv", "run", "hatch", "build", f"--target={target}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    dist = ROOT / "dist"
    archives = sorted(dist.glob("mcp_manager_io-*.tar.gz"))
    assert archives, "no sdist produced"
    archive = archives[-1]
    with tarfile.open(archive, "r:gz") as tf:
        names = tf.getnames()
    for forbidden in FORBIDDEN_PREFIXES:
        hits = [n for n in names if forbidden in n or n.endswith(forbidden.rstrip("/"))]
        assert not hits, f"sdist contains forbidden path {forbidden!r}: {hits[:5]}"

    assert any("src/manager_mcp" in n for n in names)
    assert any("tests/" in n for n in names)

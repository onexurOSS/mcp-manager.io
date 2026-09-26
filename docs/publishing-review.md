# Publishing workflow review (no publication performed)

`.github/workflows/publish.yml` is prepared for PyPI Trusted Publishing as `mcp-manager.io`.
It has NOT been run and no GitHub Release exists. A git tag alone does not trigger it.

## How it works

- Trigger: `release: published` and manual `workflow_dispatch`. A tag push does not start it.
- `build` job: builds the sdist and wheel and runs `twine check`.
- `publish` job: GitHub environment `pypi`, `id-token: write`, uses `pypa/gh-action-pypi-publish`
  with no token (Trusted Publishing). Project URL `https://pypi.org/p/mcp-manager.io`.
- `registry` job: runs after `publish` succeeds and publishes `server.json` to the MCP registry.

## Setup needed before the first publish (outside the repository)

1. On PyPI add a pending trusted publisher: project `mcp-manager.io`, owner `onexurOSS`,
   repository `mcp-manager.io`, workflow `publish.yml`, environment `pypi`. A pending publisher does
   not reserve the name until the first upload. `mcp-manager.io` was unregistered when last checked.
2. Create the GitHub environment `pypi` (required reviewers recommended, since any
   `workflow_dispatch` run publishes).
3. The registry login uses GitHub OIDC and must come from the `onexurOSS` account or organisation
   for the `io.github.onexurOSS/manager-mcp` namespace. The README carries the required `mcp-name` line.
4. Rebuild `mcpb.mcpb` for this version (it is not in the repository) and attach it to the release.
5. The tag, `pyproject.toml`, `server.json` and manifests must agree (all are 1.0.0).

## Naming

- PyPI distribution: `mcp-manager.io` (normalised `mcp-manager-io`; files are `mcp_manager_io-*`).
- Python module: `manager_mcp`. Commands: `manager-mcp`, `manager-mcp-dev`.
- Because the command differs from the distribution name, install with
  `uvx --from mcp-manager.io manager-mcp`.

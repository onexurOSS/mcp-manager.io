# Contributing to Onexur Manager MCP

Thank you for considering a contribution. This project has a mixed licensing structure, please read the section below before opening a pull request, it affects what you're agreeing to when you contribute.

## Licensing and the CLA

This repository contains two kinds of material:

- **Upstream-derived files**, originating from [manager-mcp](https://github.com/flumpiey/manager-mcp) 0.2.6, licensed under MIT.
- **Original files**, authored by Xalterra Ltd, trading as Onexur, licensed under AGPL-3.0-or-later and available separately under a commercial licence.

`PROVENANCE.json` in the repository root records which category each file falls into. Check it before you start if you're unsure.

Before your first pull request can be merged, you must sign the project's Contributor License Agreement (CLA). See `CLA.md` for the full text. In short:

- If you contribute to an **Onexur-original file** (or add a new file), the CLA grants Xalterra Ltd, trading as Onexur, the rights needed to license your contribution under AGPL-3.0-or-later and, where applicable, under a commercial licence.
- If you contribute to an **upstream-derived file**, your contribution remains subject to the upstream MIT licence. The CLA does not, and cannot, grant Xalterra Ltd ownership of upstream copyright, and your contribution to that file isn't relicensed by signing it.

You will be asked to sign the CLA before your first pull request is merged.

## Development setup

```bash
git clone https://github.com/onexurOSS/mcp-manager.io.git
cd mcp-manager.io
uv sync
```

Run the test suite:

```bash
uv run pytest
```

Run lint checks:

```bash
uv run ruff check src tests
```

Both must pass before a pull request will be reviewed.

## Making a change

1. **Check `PROVENANCE.json` first.** If your change touches an upstream-derived or mixed file, say so in your pull request description. If you're adding new functionality, prefer adding it to a new or existing Onexur-original module (see the module layout in `README.md`) rather than embedding it in an upstream-derived file, this keeps the provenance boundary clean.
2. **Write tests.** New tools or behaviour need test coverage in the appropriate test file. If you're extending Onexur-original functionality, add tests to the matching Onexur-original test file rather than the upstream-derived test file.
3. **Keep write-tier changes explicit.** Anything that writes to a Manager instance must go through the existing scope and policy checks; do not bypass `WritePolicy.authorize` or add a write path that isn't gated by an explicit scope.
4. **No secrets, no real data.** Test fixtures must be synthetic. Do not commit real Manager API keys, account data, or references to a live instance.
5. **Describe the change clearly.** In your PR description, state what changed and why, and flag anything that touches licensing-sensitive files (see `PROVENANCE.json`).

## Reporting a security issue

Do not open a public issue for a security vulnerability. See `SECURITY.md` for the disclosure process.

## Questions

For anything not covered here, including licensing questions about a specific contribution, contact licensing@xalterra.com.

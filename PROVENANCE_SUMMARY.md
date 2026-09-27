# Provenance summary (Gate 1A, analysis only)

Nothing was decided legally here. This is evidence for solicitor review. Committed tree at HEAD afe1c6819380; commit author identity rewritten to Onexur <github@xalterra.com>; working tree clean apart from PROVENANCE.json and PROVENANCE_SUMMARY.md.

## History rewrite

This regeneration follows a history rewrite, which is why every commit hash differs from those quoted in earlier manifests. Local history was rewritten with `git filter-branch` so that the nine removed paths (`docs/favicon.ico`, `docs/icon-512.png`, `docs/manager-icon.svg`, `mcpb/icon.png`, `src/manager_mcp/assets/icon.png`, `src/manager_mcp/spec/api2.json`, `src/manager_mcp/spec/api2.live-26.8.4.3664.json.gz`, `src/manager_mcp/spec/api2.live-capabilities.json`, `src/manager_mcp/spec/README.md`) never appear in any commit. The initial commit was `3e458b5` in the first version of this history. Later rewrites replaced the commit author and committer identity on all commits (from the GitHub namespace `onexurOSS <github@onexur.org>` to `Onexur <github@xalterra.com>`), so the initial commit is now `0f95e08`. Both rewrites preserved commit count, messages, author and committer dates and every tree (verified on the final rewrite: 41 commits before and after, identical trees, dates and full messages, except that one commit message line quoting an interim address was corrected). Verified: `git rev-list --objects --all` lists none of those paths, and none of the eight distinct blobs they contained (two paths shared identical content) exists in the object store after garbage collection. The remote origin still holds the old initial commit until a force push replaces it. Old commit hashes quoted in commit messages or documents written before the rewrite no longer resolve.

## Basis

- **Upstream reference:** github.com/flumpiey/manager-mcp tag `v0.2.6`, commit `c50c4d71f2aff962a8c9dbb1cce2ef14f602a51b`. Cross-check: the PyPI sdist `manager_mcp-0.2.6.tar.gz` (sha256 `f9a1069913f5126ed78686396177cdf3d7e720674facaae718f41147adb7971f`) has a `src/` tree byte-identical to the tag's `src/`.
- **Method:** line-level `difflib` similarity per file. `changed_from_upstream_pct` is the share of upstream lines replaced or deleted; `new_in_current_pct` is the share of the current file that is added. A file is flagged as a substantial-rewrite candidate when it adds at least 60 non-blank lines or at least 30% of it is new. These are triage heuristics, not a measure of copyrightable expression. Two files carry a manual classification (below).

## `server.py`: new-content share after the split

| Measure | Before the split (first manifest) | Now (committed) |
|---|---|---|
| Similarity to upstream v0.2.6 | 71.5% | **87.6%** |
| Share of the file that is new | 43.4% | **17.7%** |
| Upstream lines changed | 2.9% | 6.5% |
| Lines in the file | 1197 | 792 |
| Approximate lines added | 519 | 140 |

The new-content share fell from 43.4% to 17.7% (25.7 percentage points, a 59% relative reduction). The Onexur tool wrappers that were in `server.py` now live in `diagnostics.py`, `reconciliation.py`, `corrections.py`, `fixed_assets.py` and `server_info.py` (all `Onexur/original`). Tool names, schemas, order and behaviour are unchanged (snapshot-verified). The embedded-icon code was also removed.

What remains new in `server.py`, and why it still trips the 60-added-line heuristic: the thin `register_*` wiring calls, the date-rejection and `semantics` block in `_fetch_report` (a modified upstream function), reworded descriptions on the upstream report tools, and the dev-supervisor branch in `main()`. Whether these residual additions are separable from the upstream-derived remainder is a question for review.

## Counts per category

| Category | Files now | First manifest |
|---|---|---|
| upstream/unmodified | 51 | 53 |
| upstream/modified | 18 | 16 |
| Onexur/original | 33 | 27 |
| third-party | 1 | 7 |
| generated/artifact | 3 | 3 |
| **Total** | **106** | **106** |

The total is +0 net against the first manifest: 7 third-party logo and API description files, the generated capability matrix and `spec/README.md` were removed, and `.cursor-plugin/plugin.json`, `tests/test_report_semantics.py`, `docs/upstream-history.md` and `NOTICE` were added. `upstream/modified` splits into 3 substantial-rewrite candidates and 15 minor modifications (after the manual classifications below).

## Test split: `test_server_tools.py`

| Measure | First manifest | Now (committed) |
|---|---|---|
| `tests/test_server_tools.py` similarity to upstream | 77.3% | **96.4%** |
| `tests/test_server_tools.py` share that is new | 35.8% | 1.1% |
| `tests/test_report_semantics.py` | absent | `Onexur/original`, 134 lines |

Five Onexur tests (three profit and loss pagination tests, two `aged_receivables` tests) moved verbatim into `tests/test_report_semantics.py`. The suite has 299 tests before and after, with identical test names and function bodies.

## Substantial-rewrite candidates (flagged for solicitor review)

| File | Similarity | Upstream lines changed | Share of file that is new | What changed |
|---|---|---|---|---|
| `CHANGELOG.md` | 20.0% | 80.0% | 80.0% | documentation rewritten/extended |
| `README.md` | 19.4% | 87.5% | 56.2% | documentation rewritten/extended |
| `src/manager_mcp/server.py` | 87.6% | 6.5% | 17.7% | edits to existing logic/strings; removes server_icons |

### Manual classification of five files

These five changed after the first manifest was written and were read directly against upstream v0.2.6. The rewrites (`CHANGELOG.md`, `README.md`) are flagged for the eventual solicitor follow-up.

- **`tests/test_server_icons.py`:** minor modification - likely remains upstream-derived (manual override of the automatic flag). 12-line upstream test whose icon assertions were inverted. The module skeleton and two assertion lines (mcp.name and website_url) are verbatim upstream. The automatic substantial flag is a small-file percentage artefact. Automatic figures: similarity 38.9%, 70.8% of upstream lines changed, 41.7% of the file new.
- **`.cursor-plugin/plugin.json`:** minor modification - likely remains upstream-derived (manual override of the automatic flag). Upstream plugin manifest restored from the v0.2.6 tag and adapted: author, repository URLs, version, and the license field (now AGPL-3.0-or-later to describe the distributed package). The file is a small JSON document (about 20 lines), so a handful of changed lines crosses the automatic 30 percent new-content threshold. Structure and most keys are upstream's. Automatic figures: similarity 74.7%, 19.0% of upstream lines changed, 30.6% of the file new.
- **`CHANGELOG.md`:** substantial rewrite - candidate for Onexur/original. Since the changelog split the file holds only the Onexur 1.0.0 entry. The only upstream text retained is the two line title and introduction. The upstream release entries moved verbatim to docs/upstream-history.md. The path match to upstream CHANGELOG.md is a comparison artefact. Automatic figures: similarity 20.0%, 80.0% of upstream lines changed, 80.0% of the file new.
- **`README.md`:** substantial rewrite - candidate for Onexur/original. Rewritten from scratch in the solicitor's structure and checked against the code. Similarity to the upstream README is 19.4%. Of 199 non-trivial upstream lines, only one repeated line survives (the example MANAGER_API_URL value, Manager's default local address, a configuration fact). No upstream prose is retained. Automatic figures: similarity 19.4%, 87.5% of upstream lines changed, 56.2% of the file new.
- **`docs/upstream-history.md`:** upstream-derived, reproduced verbatim (minor modification). Byte for byte copy of the upstream v0.2.6 changelog entries for 0.1.0 to 0.2.6, with an added attribution and MIT notice paragraph. The tool matched it to upstream CHANGELOG.md by rename detection. Automatic figures: similarity 93.0%, 2.7% of upstream lines changed, 11.0% of the file new.

### Minor modifications (likely remain upstream-derived)

| File | Similarity | Note |
|---|---|---|
| `.cursor-plugin/plugin.json` | 74.7% | configuration/metadata edits |
| `.github/workflows/publish.yml` | 88.3% | configuration/metadata edits |
| `.gitignore` | 97.3% | configuration/metadata edits |
| `LICENSE-MIT` | 97.7% | configuration/metadata edits; renamed from LICENSE |
| `docs/upstream-history.md` | 93.0% | documentation rewritten/extended; renamed from CHANGELOG.md |
| `mcp.json` | 92.9% | configuration/metadata edits |
| `mcpb/manifest.json` | 86.2% | configuration/metadata edits |
| `mcpb/pyproject.toml` | 80.0% | configuration/metadata edits |
| `pyproject.toml` | 86.4% | configuration/metadata edits |
| `server.json` | 80.0% | configuration/metadata edits |
| `src/manager_mcp/resources.py` | 95.6% | new logic: adds _merge_extra_read_only_collections |
| `tests/test_resources.py` | 96.2% | edits to existing logic/strings |
| `tests/test_sdist_contents.py` | 97.7% | edits to existing logic/strings |
| `tests/test_server_icons.py` | 38.9% | new logic: adds test_fastmcp_advertises_website_and_no_embedded_icons; removes test_fastmcp_configured_with_icons_and_website, test_server_icons_include_data_uri_and_https |
| `tests/test_server_tools.py` | 96.4% | edits to existing logic/strings; removes test_aged_receivables_period_unsupported_notice |

## Third-party concerns

### Files

One: `LICENSE-AGPL`, the Free Software Foundation text of the GNU Affero General Public License version 3, unaltered apart from one added copyright line at the top; the licence text itself may be copied verbatim but not altered. It is the licence itself, not third-party code. Apart from it, none remain. The 7 Manager.io logo and API description files flagged in the first manifest were removed (commit `995e8af`). The generated capability matrix (`src/manager_mcp/spec/api2.live-capabilities.json`, kept in the first pass) and `spec/README.md` were also removed; a matrix is now built by users from their own instance with `scripts/build_capability_matrix.py`. All nine files are also absent from history since the rewrite described above.

### Dependencies (104 distributions in `uv.lock`, 75 runtime, 29 dev/build only; unchanged since the first manifest)

- **Copyleft in the closure (corrected):** no package is declared GPL, LGPL or AGPL, but `pywin32` (Windows-only, required by `mcp` when `sys_platform == 'win32'`, not installed on Linux) declares PSF-2.0 while bundling licence files for several components, including `adodbapi` under the GNU Lesser General Public License 2.1. Earlier manifests said the closure had no GPL-family licence; that missed this bundled component. All of pywin32's bundled licence files are reproduced in `THIRD-PARTY-NOTICES`. **Flagged for the solicitor package (open, not resolved):** transitive Windows-only dependency (`pywin32`, via `mcp`) bundles an LGPL-2.1 component (`adodbapi`); not installed or vendored in this build; confirm no action is needed. Two MPL-2.0 packages (`certifi`, `pathspec`) are file-level copyleft, used unmodified. **Unlicensed or undeclared:** none after checking installed metadata, licence files and PyPI.
- **Full notices:** `THIRD-PARTY-NOTICES` (committed) reproduces the licence text shipped by each of the 106 name and version entries, generated from package metadata by `pip-licenses` plus the dist-info licence files, and from PyPI wheel metadata for the 11 entries not installable on the audit platform. Distinct packages by family: MIT 54, BSD 20, Apache-2.0 14, PSF 5, ISC 5, MPL-2.0 2, Dual-licensed (SPDX OR expression) 3, Other 1. Dual-licensed (SPDX OR): `cryptography`, `packaging`, `uv`. Other: `email-validator` (Unlicense).
- **Worth a solicitor's glance** (none is incompatible with MIT or AGPLv3 distribution on its declared terms):
  - `backports-asyncio-runner` 1.2.0 (dev/build only): PSF-2.0
  - `backports-zstd` 1.6.0 (dev/build only): PSF-2.0
  - `certifi` 2026.7.22 (runtime): MPL-2.0
  - `cryptography` 49.0.0 (runtime): Apache-2.0 OR BSD-3-Clause
  - `distlib` 0.4.3 (dev/build only): PSF-2.0
  - `packaging` 26.2 (runtime): Apache-2.0 OR BSD-2-Clause
  - `pathspec` 1.1.1 (dev/build only): Mozilla Public License 2.0 (MPL 2.0). identified from licence text as MPL-2.0; MPL-2.0 is file-level copyleft, compatible with AGPLv3 and fine as an unmodified dependency
  - `ptyprocess` 0.7.0 (dev/build only): UNKNOWN. identified from licence text as ISC
  - `pywin32` 312 (runtime): PSF
  - `typing-extensions` 4.16.0 (runtime): PSF-2.0
  - `uv` 0.12.0 (dev/build only): MIT OR Apache-2.0
- `ptyprocess` (dev/build only) declares `UNKNOWN` in package metadata; its licence file text is ISC. Apache-2.0 packages are compatible with AGPLv3 one way (not GPLv2-only). MPL-2.0 packages (`certifi` runtime, `pathspec` dev/build) are file-level copyleft and used unmodified. No dependency code is vendored.

## Upstream MIT notices

The upstream MIT licence and copyright notice **are present** in `LICENSE-MIT`: the MIT text with `Copyright (c) 2026 manager-mcp contributors` (as in upstream's `LICENSE`) and one added line `Copyright (c) 2026 Xalterra Ltd, trading as Onexur`. The former single `LICENSE` file was deleted. `LICENSE-AGPL` holds the unaltered FSF AGPL-3.0 text preceded by the same Xalterra copyright line. `NOTICE` repeats the upstream copyright line and source URL and names the same holder for material added for this project. **Resolved:** all three licence documents now name one holder, Xalterra Ltd, trading as Onexur (an earlier mismatch with `onexurOSS`, the GitHub namespace, was fixed). Prefacing the AGPL text with a project copyright line is standard practice and is not an edit to the licence text, so it is not flagged. `pyproject.toml` now declares `AGPL-3.0-or-later`; built artifacts carry `License-Expression: AGPL-3.0-or-later` and `License-File` entries for `LICENSE-AGPL`, `LICENSE-MIT` and `NOTICE`. Author metadata (`authors` in `pyproject.toml`, and the author fields in `mcpb/manifest.json` and `.cursor-plugin/plugin.json`) now names Xalterra Ltd, trading as Onexur, and `pyproject.toml` also keeps the upstream author, Dru Connold. `.cursor-plugin/plugin.json` now declares `AGPL-3.0-or-later`, matching `pyproject.toml` (it declared `MIT` before; the file itself remains upstream-derived). Other attribution: `README.md:5:This project began as a fork of [manager-mcp](https://github.com/flu`; `README.md:131:This project incorporates material from [manager-mcp](https://gith`; `pyproject.toml:12:authors = [{ name = "Xalterra Ltd, trading as Onexur" }, { nam`. `THIRD-PARTY-NOTICES` (dependency licence texts) is now present and is packaged in the sdist and wheel. Per-file headers: see the next section. `CLA.md` (version 1.0, company number recorded) is committed. The CLA bot workflow `.github/workflows/cla.yml` is active and was confirmed on a test pull request (signature recorded at `signatures/v1.0/cla.json` on the `cla-signatures` branch).

## Per-file licence headers

26 of 65 Python files carry the two line AGPL-3.0-or-later header (`SPDX-License-Identifier: AGPL-3.0-or-later` and `Copyright (c) 2026 Xalterra Ltd, trading as Onexur`). They are exactly the files classified `Onexur/original`: ten modules under `src/manager_mcp`, two scripts and fourteen test files. The header pass added two lines to each file and changed nothing else (unchanged ASTs, identical tool snapshot, tests passing).

Deliberately without headers: every `upstream/unmodified` file, every `upstream/modified` Python file including the mixed files `src/manager_mcp/server.py` and `tests/test_server_tools.py` (per the second solicitor opinion: mixed, no further splitting or headers), and non-Python files.

## Parallel checks (report only)

- **"Malva dev":** 7 occurrences, all also present in the upstream v0.2.6 tag (so upstream-inherited): `specs/001-manager-readonly-mcp/research-v02-deposits.md:3:**Source**: `; `specs/001-manager-readonly-mcp/research-v02-deposits.md:17:## 3. Recei`; `specs/001-manager-readonly-mcp/research-writes-banking.md:3:**Instance`; `specs/001-manager-readonly-mcp/research-writes-banking.md:29:| `Amount`; `specs/001-manager-readonly-mcp/research-writes-quotes.md:3:**Instance*`; `specs/001-manager-readonly-mcp/research-writes-scopes.md:3:**Instance*`; `src/manager_mcp/writable.py:162:# Paths confirmed via live OpenAPI (Ma`. Real or not is unconfirmed.
- **`mcpb.mcpb`:** on disk **False** (deleted), tracked in HEAD **False**, referenced by a CI workflow **False**. Upstream tracked it at v0.2.6.
- **Editor files:** `.cursor-plugin/plugin.json` present; `.cursor/mcp.json` missing; `.vscode/mcp.json` missing. `.cursor-plugin/plugin.json` was restored from the upstream tag (adapted, no logo). The other two were never repo files: they are paths users create in their own projects, and are now plain-text mentions.

## Limits

- Similarity is line-based; additive documentation scores as "new".
- Renames were checked at a 60% threshold against same-type upstream files; none matched.
- Files added in upstream versions later than 0.2.6 were not considered.
- No legal conclusion is drawn on which files may be relicensed.

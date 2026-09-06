"""Xalterra-owned read-only collection additions: Fixed Assets, Tax Codes.

Kept isolated from upstream's resources.py deliberately -- these two
collections were requested for the Xalterra Manager.io -> ERPNext migration
but are NOT part of upstream's own resource set. They are pure data here
(no dependency on resources.py's internals) so resources.py's own merge
hook stays a single, tiny, easily-reviewable addition, and this file
survives future `git fetch upstream && git merge` unchanged.

Deliberately NOT added to writable.WRITABLE: that dict is also what
register_write_tools()/register_task_tools() use to decide which
create_*/update_*/delete_* tools to register once a scope is enabled.
Fixed Assets and Tax Codes must never gain a write path through this
project regardless of future scope configuration -- they are read-only
collections, full stop, resolved only through list_records/get_record.

Both list paths and list-envelope (items) keys were confirmed against the
live Xalterra Manager business (not guessed):

    GET /fixed-assets   -> {"fixedAssets": [...], "totalRecords": ..., ...}
    GET /tax-codes      -> {"taxCodes": [...], "totalRecords": ..., ...}

Individual-record form paths were given as already-verified by the task
that requested this addition:

    GET /fixed-asset-form/{key}
    GET /tax-code-form/{key}

@module manager_mcp.xalterra_read_collections
"""

from __future__ import annotations

# (name, list_path, description, form_path_template, items_key)
XALTERRA_READ_ONLY_COLLECTIONS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "fixed_assets",
        "/fixed-assets",
        "Fixed assets collection (read-only; Xalterra migration addition). "
        "Items include acquisitionCost, bookValue, and depreciation summary fields.",
        "/fixed-asset-form/{key}",
        "fixedAssets",
    ),
    (
        "tax_codes",
        "/tax-codes",
        "Tax codes collection (read-only; Xalterra migration addition).",
        "/tax-code-form/{key}",
        "taxCodes",
    ),
)

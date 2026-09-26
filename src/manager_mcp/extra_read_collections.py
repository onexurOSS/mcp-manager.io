"""Read-only collection additions: Fixed Assets and Tax Codes.

These two collections are not part of the base resource set. They are pure data
here (no dependency on resources.py's internals) so resources.py needs only one
small merge hook.

Deliberately NOT added to writable.WRITABLE: that dict is also what
register_write_tools()/register_task_tools() use to decide which
create_*/update_*/delete_* tools to register once a scope is enabled.
Fixed Assets and Tax Codes must never gain a write path through this
project regardless of future scope configuration: they are read-only
collections, resolved only through list_records/get_record.

Both list paths and list-envelope (items) keys were confirmed against a live
Manager instance (not guessed):

    GET /fixed-assets   -> {"fixedAssets": [...], "totalRecords": ..., ...}
    GET /tax-codes      -> {"taxCodes": [...], "totalRecords": ..., ...}

Individual-record form paths:

    GET /fixed-asset-form/{key}
    GET /tax-code-form/{key}

@module manager_mcp.extra_read_collections
"""

from __future__ import annotations

# (name, list_path, description, form_path_template, items_key)
EXTRA_READ_ONLY_COLLECTIONS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "fixed_assets",
        "/fixed-assets",
        "Fixed assets collection (read-only). "
        "Items include acquisitionCost, bookValue, and depreciation summary fields.",
        "/fixed-asset-form/{key}",
        "fixedAssets",
    ),
    (
        "tax_codes",
        "/tax-codes",
        "Tax codes collection (read-only).",
        "/tax-code-form/{key}",
        "taxCodes",
    ),
)

"""Pagination helper for looping through Manager list collections.

Manager's list query surface is `term`/`sortBy`/`sortByDesc`/`skip`/`pageSize`
only -- there is no server-side field-value filter. Diagnostics that need
"every row where X" must page through the full collection and filter
client-side; this module is the one place that loop lives.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from manager_mcp.client import ManagerClient
from manager_mcp.resources import extract_items, form_path, resolve


async def fetch_all(
    client: ManagerClient,
    resource: str,
    *,
    params: dict[str, Any] | None = None,
    page_size: int = 200,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    """Page through a curated collection until exhausted or max_pages hit.

    Returns the flat list of item dicts (non-dict rows are dropped -- Manager
    list envelopes are dict rows in practice). Raises ValueError for a name
    that isn't a known collection.
    """
    desc = resolve(resource)
    if desc is None or desc.kind != "collection":
        raise ValueError(f"Unknown collection '{resource}'.")
    result = await fetch_paginated(
        client, resource, params=params, page_size=page_size, max_pages=max_pages
    )
    return result.items


@dataclass(frozen=True)
class PaginatedResult:
    """Outcome of paging a collection or report to exhaustion (or a cap).

    `complete` is False only when `max_pages` was hit before Manager's own
    `totalRecords` (or a short/empty page) signalled the end. Callers MUST
    check this before treating `items`/`first_body` as the whole dataset.

    `completeness` gives the *reason*, not just the boolean, distinguishing
    four states a caller (or a report response) should never blur together:

    - "confirmed_total": Manager's own `totalRecords` was reached exactly.
      The strongest signal; trust this fully.
    - "final_page_reached": the last page came back empty or shorter than
      `page_size`, which is the standard REST-pagination end-of-data signal,
      but no `totalRecords` was available to double-check against. Trusted
      by convention, not confirmed by count.
    - "max_pages_reached": the page cap was hit while `totalRecords` says
      more rows remain. Definitely incomplete.
    - "unable_to_determine": the page cap was hit, the last page fetched was
      still full-sized, and Manager never provided `totalRecords` either.
      There is no basis to call this complete or to say how much is missing.
    - "single_object": the endpoint returned one flat object rather than a
      list under `items_key` (e.g. some non-list report shapes). One
      fetch is definitionally the whole answer, so this counts as complete.
    """

    items: list[dict[str, Any]]
    total_records: int | None
    pages_fetched: int
    complete: bool
    completeness: str
    first_body: dict[str, Any] | None
    saw_list: bool


async def fetch_paginated(
    client: ManagerClient,
    resource: str,
    *,
    params: dict[str, Any] | None = None,
    page_size: int = 200,
    max_pages: int = 50,
) -> PaginatedResult:
    """Page through any collection OR report (both share Manager's
    skip/pageSize/totalRecords list contract) until exhausted or max_pages.

    Unlike `fetch_all`, this never silently truncates without saying so:
    `complete=False` means max_pages was hit while more rows remained, and
    callers must surface that rather than presenting `items` as the full set.

    `saw_list` is False when the endpoint's own `items_key` never actually
    held a list (e.g. a report shape Manager returns as one flat dict). In
    that case `items` is meaningless and callers should fall back to
    `first_body` as the (single-page) result, matching pre-pagination
    behaviour for that shape.
    """
    desc = resolve(resource)
    if desc is None or desc.kind not in ("collection", "report"):
        raise ValueError(f"Unknown collection/report '{resource}'.")
    items: list[dict[str, Any]] = []
    first_body: dict[str, Any] | None = None
    total: int | None = None
    saw_list = False
    skip = 0
    pages_fetched = 0
    completeness = "max_pages_reached"  # overwritten by every break below
    for _ in range(max_pages):
        page_params: dict[str, Any] = {"skip": skip, "pageSize": page_size, **(params or {})}
        body = await client.get(desc.path, params=page_params)
        pages_fetched += 1
        if first_body is None and isinstance(body, dict):
            first_body = body
        if isinstance(body, dict) and desc.items_key and isinstance(body.get(desc.items_key), list):
            saw_list = True
        page_items = [i for i in extract_items(desc, body) if isinstance(i, dict)]
        if not saw_list:
            # Non-list report shape (e.g. a single-object body): one page
            # is the whole answer; don't keep looping over the same object.
            items = page_items
            completeness = "single_object"
            break
        items.extend(page_items)
        page_total = body.get("totalRecords") if isinstance(body, dict) else None
        if isinstance(page_total, int):
            total = page_total
        skip += len(page_items)
        if isinstance(total, int) and skip >= total:
            completeness = "confirmed_total"
            break
        if not page_items or len(page_items) < page_size:
            completeness = "final_page_reached"
            break
    else:
        # Loop exhausted max_pages without a break: more may remain.
        completeness = "max_pages_reached" if isinstance(total, int) else "unable_to_determine"
    complete = completeness in ("confirmed_total", "final_page_reached", "single_object")
    return PaginatedResult(
        items=items,
        total_records=total,
        pages_fetched=pages_fetched,
        complete=complete,
        completeness=completeness,
        first_body=first_body,
        saw_list=saw_list,
    )


async def fetch_all_forms(
    client: ManagerClient,
    resource: str,
    *,
    max_pages: int = 50,
    page_size: int = 200,
    concurrency: int = 10,
) -> list[dict[str, Any]]:
    """List a collection (cheap) then fetch each record's full form.

    Manager's list envelopes are a flattened, camelCase, display-oriented
    summary (verified live: `key`, `date`/`issueDate`, `reference`,
    nested `{value, currency}` money objects) -- structured `Lines[]` and
    AR/AP allocation fields exist ONLY on the per-record form endpoint
    (PascalCase). Anything that needs Lines, Account, Supplier/Customer
    Keys, or invoice allocation must use this, not fetch_all() alone.

    Each returned form dict carries the originating list-row under the
    `_list` key, since some monetary facts (e.g. a purchase/sales
    invoice's computed total and outstanding balance) are ONLY available
    on the list row -- invoice Lines use Qty x UnitPrice + TaxCode with no
    verified tax-computation path here, so the list's own computed
    `invoiceAmount`/`balanceDue` is the authoritative total, not a
    from-scratch sum of Lines.
    """
    summaries = await fetch_all(client, resource, max_pages=max_pages, page_size=page_size)
    sem = asyncio.Semaphore(concurrency)
    forms: list[dict[str, Any]] = []

    async def _one(summary: dict[str, Any]) -> None:
        key = summary.get("key") or summary.get("Key")
        if not key:
            return
        path = form_path(resource, str(key))
        if path is None:
            return
        async with sem:
            body = await client.get(path)
        if isinstance(body, dict):
            enriched = dict(body)
            enriched["_list"] = summary
            forms.append(enriched)

    await asyncio.gather(*(_one(s) for s in summaries))
    return forms

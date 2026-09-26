"""fetch_all pagination helper (respx; no live Manager)."""

from __future__ import annotations

import httpx
import pytest
import respx

from manager_mcp.paging import fetch_all, fetch_paginated

BASE = "http://example.test/api2"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_all_stops_at_total_records() -> None:
    from manager_mcp.client import ManagerClient

    respx.get(f"{BASE}/customers", params={"skip": "0", "pageSize": "2"}).mock(
        return_value=httpx.Response(
            200, json={"totalRecords": 3, "customers": [{"Key": "1"}, {"Key": "2"}]}
        )
    )
    respx.get(f"{BASE}/customers", params={"skip": "2", "pageSize": "2"}).mock(
        return_value=httpx.Response(200, json={"totalRecords": 3, "customers": [{"Key": "3"}]})
    )
    client = ManagerClient(BASE, "k")
    items = await fetch_all(client, "customers", page_size=2)
    await client.aclose()
    assert [i["Key"] for i in items] == ["1", "2", "3"]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_all_stops_on_short_page_without_total() -> None:
    from manager_mcp.client import ManagerClient

    respx.get(f"{BASE}/customers", params={"skip": "0", "pageSize": "200"}).mock(
        return_value=httpx.Response(200, json={"customers": [{"Key": "1"}]})
    )
    client = ManagerClient(BASE, "k")
    items = await fetch_all(client, "customers")
    await client.aclose()
    assert [i["Key"] for i in items] == ["1"]


@pytest.mark.asyncio
async def test_fetch_all_unknown_collection() -> None:
    from manager_mcp.client import ManagerClient

    client = ManagerClient(BASE, "k")
    with pytest.raises(ValueError, match="Unknown collection"):
        await fetch_all(client, "nope")
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_fetch_paginated_report_pages_to_completion_no_duplicates() -> None:
    """A report (kind='report', not 'collection') must page fully, with no
    duplicate or missing rows, and report complete=True once exhausted."""
    from manager_mcp.client import ManagerClient

    respx.get(
        f"{BASE}/profit-and-loss-statement-transactions",
        params={"skip": "0", "pageSize": "2"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "totalRecords": 5,
                "profitAndLossStatementTransactions": [{"Key": "1"}, {"Key": "2"}],
            },
        )
    )
    respx.get(
        f"{BASE}/profit-and-loss-statement-transactions",
        params={"skip": "2", "pageSize": "2"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "totalRecords": 5,
                "profitAndLossStatementTransactions": [{"Key": "3"}, {"Key": "4"}],
            },
        )
    )
    respx.get(
        f"{BASE}/profit-and-loss-statement-transactions",
        params={"skip": "4", "pageSize": "2"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={"totalRecords": 5, "profitAndLossStatementTransactions": [{"Key": "5"}]},
        )
    )
    client = ManagerClient(BASE, "k")
    result = await fetch_paginated(client, "profit_and_loss", page_size=2)
    await client.aclose()
    keys = [i["Key"] for i in result.items]
    assert keys == ["1", "2", "3", "4", "5"]
    assert len(keys) == len(set(keys))  # no duplicates
    assert result.total_records == 5
    assert result.complete is True
    assert result.completeness == "confirmed_total"
    assert result.pages_fetched == 3


@pytest.mark.asyncio
@respx.mock
async def test_fetch_paginated_reports_incomplete_when_max_pages_hit() -> None:
    """Known total, but max_pages hit before reaching it -> definitely incomplete."""
    from manager_mcp.client import ManagerClient

    respx.get(f"{BASE}/profit-and-loss-statement-transactions").mock(
        return_value=httpx.Response(
            200,
            json={
                "totalRecords": 100,
                "profitAndLossStatementTransactions": [{"Key": "x"}, {"Key": "y"}],
            },
        )
    )
    client = ManagerClient(BASE, "k")
    result = await fetch_paginated(client, "profit_and_loss", page_size=2, max_pages=3)
    await client.aclose()
    assert result.complete is False
    assert result.completeness == "max_pages_reached"
    assert result.pages_fetched == 3
    assert len(result.items) == 6  # honest partial count, not silently presented as all 100


@pytest.mark.asyncio
@respx.mock
async def test_fetch_paginated_unable_to_determine_without_total() -> None:
    """Max_pages hit, every page full-sized, and Manager never sends
    totalRecords at all: there's no basis to call this complete OR to
    know how much is missing. Must not be conflated with either
    confirmed-complete or the "we know we're short" incomplete case."""
    from manager_mcp.client import ManagerClient

    respx.get(f"{BASE}/profit-and-loss-statement-transactions").mock(
        return_value=httpx.Response(
            200,
            json={"profitAndLossStatementTransactions": [{"Key": "x"}, {"Key": "y"}]},
        )
    )
    client = ManagerClient(BASE, "k")
    result = await fetch_paginated(client, "profit_and_loss", page_size=2, max_pages=3)
    await client.aclose()
    assert result.complete is False
    assert result.completeness == "unable_to_determine"
    assert result.total_records is None
    assert result.pages_fetched == 3


@pytest.mark.asyncio
@respx.mock
async def test_fetch_paginated_single_short_page_is_final_page_reached() -> None:
    """One page shorter than page_size, no totalRecords: trusted as the
    end by REST-pagination convention, but distinguished from a
    Manager-confirmed total."""
    from manager_mcp.client import ManagerClient

    respx.get(f"{BASE}/profit-and-loss-statement-transactions").mock(
        return_value=httpx.Response(
            200, json={"profitAndLossStatementTransactions": [{"Key": "only"}]}
        )
    )
    client = ManagerClient(BASE, "k")
    result = await fetch_paginated(client, "profit_and_loss", page_size=200)
    await client.aclose()
    assert result.complete is True
    assert result.completeness == "final_page_reached"
    assert result.pages_fetched == 1
    assert [i["Key"] for i in result.items] == ["only"]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_paginated_non_list_report_shape_is_single_object() -> None:
    """A report whose items_key never holds a list (a flat single-object
    body) is definitionally one page, and must be labelled as such, not
    silently treated like an ordinary (possibly truncated) list result."""
    from manager_mcp.client import ManagerClient

    respx.get(f"{BASE}/customers", params={"skip": "0", "pageSize": "200"}).mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = ManagerClient(BASE, "k")
    result = await fetch_paginated(client, "aged_receivables")
    await client.aclose()
    assert result.saw_list is False
    assert result.complete is True
    assert result.completeness == "single_object"
    assert result.pages_fetched == 1


@pytest.mark.asyncio
async def test_fetch_paginated_unknown_resource() -> None:
    from manager_mcp.client import ManagerClient

    client = ManagerClient(BASE, "k")
    with pytest.raises(ValueError, match="Unknown collection/report"):
        await fetch_paginated(client, "nope")
    await client.aclose()

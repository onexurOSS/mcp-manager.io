"""Smoke: quotes-only scopes from .env — tool set + reversible create/delete."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


async def main() -> None:
    load_dotenv(ROOT / ".env")
    from manager_mcp.scopes import WritePolicy, WritesDeniedError
    from manager_mcp.server import mcp, register_write_tools, reset_client
    from manager_mcp.client import ManagerClient

    reset_client()
    policy = WritePolicy.from_env()
    print("write_scopes=", sorted(policy.write_scopes))
    print("delete_scopes=", sorted(policy.delete_scopes))
    assert policy.write_scopes == frozenset({"quotes"})
    assert policy.delete_scopes == frozenset({"quotes"})

    captured: list[str] = []

    def fake_tool(*_a: object, **kwargs: object):
        def deco(fn: object) -> object:
            captured.append(str(kwargs.get("name") or getattr(fn, "__name__", "")))
            return fn

        return deco

    # Capture what register_write_tools would add without polluting global mcp forever
    original = mcp.tool
    mcp.tool = fake_tool  # type: ignore[method-assign]
    try:
        register_write_tools()
    finally:
        mcp.tool = original  # type: ignore[method-assign]
        reset_client()

    print("registered_write_tools=", sorted(captured))
    expected = {
        "create_sales_quote",
        "update_sales_quote",
        "delete_sales_quote",
        "create_purchase_quote",
        "update_purchase_quote",
        "delete_purchase_quote",
    }
    assert set(captured) == expected, set(captured)

    client = ManagerClient.from_env(policy=policy)
    try:
        # Cross-scope must fail before HTTP
        try:
            await client.post("/customer-form", json={"Name": "SHOULD_NOT_CREATE"})
            raise SystemExit("FAIL: customer create should be denied")
        except WritesDeniedError as exc:
            print("cross_scope_denied_ok=", str(exc))

        # Reversible sales quote smoke
        created = await client.post(
            "/sales-quote-form",
            json={"Description": "MCP quotes-scope smoke (DELETE ME)"},
        )
        key = created.get("Key") if isinstance(created, dict) else None
        print("create_status=ok key=", key)
        assert key

        got = await client.get(f"/sales-quote-form/{key}")
        print("get_description=", (got or {}).get("Description"))

        updated_body = dict(got)
        updated_body["Description"] = "MCP quotes-scope smoke UPDATED (DELETE ME)"
        await client.put(f"/sales-quote-form/{key}", json=updated_body)
        after = await client.get(f"/sales-quote-form/{key}")
        print("update_description=", (after or {}).get("Description"))

        await client.delete(f"/sales-quote-form/{key}")
        try:
            await client.get(f"/sales-quote-form/{key}")
            print("delete_check=UNEXPECTED_STILL_EXISTS")
        except httpx.HTTPStatusError as exc:
            print("delete_check=", exc.response.status_code)
            assert exc.response.status_code == 404

        print("SMOKE_OK")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())

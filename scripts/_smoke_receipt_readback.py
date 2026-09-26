"""Confirm receipts list/get_record path works with banking scopes (read-only probe)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def main() -> None:
    load_dotenv(ROOT / ".env")
    # Simulate Cursor MCP all-scopes for this smoke
    os.environ["MANAGER_MCP_WRITE_SCOPES"] = (
        "quotes,orders,parties,items,sales,purchases,banking,payroll,ledger"
    )
    os.environ["MANAGER_MCP_DELETE_SCOPES"] = os.environ["MANAGER_MCP_WRITE_SCOPES"]

    from manager_mcp.resources import form_path, resolve
    from manager_mcp.server import list_resources, reset_client
    from manager_mcp.client import ManagerClient
    from manager_mcp.scopes import WritePolicy

    reset_client()
    desc = resolve("receipts")
    assert desc is not None, "receipts missing from read allowlist"
    print("receipts_path=", desc.path, "form=", form_path("receipts", "{key}"))

    discovery = await list_resources()
    print("read_only=", discovery["read_only"])
    print("write_scopes=", discovery["write_scopes"])
    assert discovery["read_only"] is False
    assert "receipts" in {r["name"] for r in discovery["resources"]}

    policy = WritePolicy.from_env()
    client = ManagerClient.from_env(policy=policy)
    try:
        page = await client.get("/receipts", params={"pageSize": 1})
        items = (page or {}).get("receipts") or []
        print("list_total=", (page or {}).get("totalRecords"), "sample=", bool(items))
        if items:
            key = items[0]["key"]
            form = await client.get(f"/receipt-form/{key}")
            keys = sorted(form.keys()) if isinstance(form, dict) else []
            print("sample_form_keys=", keys)
            # Show currency-ish fields without dumping PII-heavy lines
            if isinstance(form, dict):
                interesting = {
                    k: form.get(k)
                    for k in keys
                    if any(
                        t in k.lower()
                        for t in (
                            "bank",
                            "cash",
                            "customer",
                            "date",
                            "amount",
                            "exchange",
                            "currency",
                            "line",
                            "account",
                            "description",
                            "key",
                        )
                    )
                }
                # truncate long values
                for k, v in list(interesting.items()):
                    if isinstance(v, (list, dict)):
                        interesting[k] = f"<{type(v).__name__} len={len(v)}>"
                    elif isinstance(v, str) and len(v) > 80:
                        interesting[k] = v[:80] + "..."
                print("sample_form_subset=", interesting)
        print("READBACK_OK")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())

"""Probe minimal sales/purchase quote create bodies; always delete."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "specs" / "001-manager-readonly-mcp" / "_quote-minimal-create.json"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> None:
    env = load_env(ROOT / ".env")
    base = env["MANAGER_API_URL"].rstrip("/")
    headers = {
        "X-API-KEY": env["MANAGER_API_KEY"],
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    results: list[dict[str, object]] = []

    with httpx.Client(timeout=60.0, headers=headers) as client:
        customer = client.get(base + "/customers", params={"pageSize": 1}).json()[
            "customers"
        ][0]["key"]
        supplier = client.get(base + "/suppliers", params={"pageSize": 1}).json()[
            "suppliers"
        ][0]["key"]

        sales_attempts = [
            {},
            {"Customer": customer},
            {"Customer": customer, "IssueDate": "2026-07-28"},
            {
                "Customer": customer,
                "IssueDate": "2026-07-28",
                "Description": "MCP minimal sales quote DELETE ME",
            },
            {
                "Customer": customer,
                "IssueDate": "2026-07-28",
                "Description": "MCP minimal sales quote DELETE ME",
                "Lines": [],
            },
            {
                "Customer": customer,
                "IssueDate": "2026-07-28",
                "Description": "MCP minimal sales quote DELETE ME",
                "Lines": [{"LineDescription": "probe line", "Qty": 1, "SalesUnitPrice": 1}],
            },
        ]

        for index, body in enumerate(sales_attempts):
            response = client.post(base + "/sales-quote-form", json=body)
            payload: object
            try:
                payload = response.json()
            except Exception:
                payload = response.text
            key = None
            if isinstance(payload, dict):
                key = payload.get("Key") or payload.get("key")
            entry = {
                "kind": "sales",
                "attempt": index,
                "status": response.status_code,
                "location": response.headers.get("Location"),
                "request": body,
                "response_key": key,
                "response_keys": sorted(payload.keys())
                if isinstance(payload, dict)
                else None,
                "error": payload.get("error")
                if isinstance(payload, dict) and response.status_code >= 400
                else None,
                "preview": str(payload)[:400],
            }
            results.append(entry)
            if response.status_code in {200, 201} and key:
                delete = client.delete(f"{base}/sales-quote-form/{key}")
                entry["deleted_status"] = delete.status_code
                break

        purchase_attempts = [
            {
                "Supplier": supplier,
                "Date": "2026-07-28",
                "Description": "MCP minimal purchase quote DELETE ME",
            },
            {
                "Supplier": supplier,
                "Date": "2026-07-28",
                "Description": "MCP minimal purchase quote DELETE ME",
                "Lines": [{"LineDescription": "probe line", "Qty": 1, "UnitPrice": 1}],
            },
        ]
        for index, body in enumerate(purchase_attempts):
            response = client.post(base + "/purchase-quote-form", json=body)
            try:
                payload = response.json()
            except Exception:
                payload = response.text
            key = None
            if isinstance(payload, dict):
                key = payload.get("Key") or payload.get("key")
            entry = {
                "kind": "purchase",
                "attempt": index,
                "status": response.status_code,
                "location": response.headers.get("Location"),
                "request": body,
                "response_key": key,
                "response_keys": sorted(payload.keys())
                if isinstance(payload, dict)
                else None,
                "error": payload.get("error")
                if isinstance(payload, dict) and response.status_code >= 400
                else None,
                "preview": str(payload)[:400],
            }
            results.append(entry)
            if response.status_code in {200, 201} and key:
                delete = client.delete(f"{base}/purchase-quote-form/{key}")
                entry["deleted_status"] = delete.status_code
                break

    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2)[:4000])


if __name__ == "__main__":
    main()

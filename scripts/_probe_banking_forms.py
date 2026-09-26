"""Sanitize receipt/payment form field shapes for research notes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "specs" / "001-manager-readonly-mcp" / "_banking-form-shapes.json"


def load_dotenv(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def shape(obj: object, depth: int = 0) -> object:
    if depth > 4:
        return type(obj).__name__
    if isinstance(obj, dict):
        return {k: shape(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        if not obj:
            return []
        return [shape(obj[0], depth + 1)]
    if isinstance(obj, str):
        return f"str(len={len(obj)})"
    if obj is None:
        return None
    return type(obj).__name__


def main() -> None:
    load_dotenv(ROOT / ".env")
    base = os.environ["MANAGER_API_URL"].rstrip("/")
    headers = {
        "X-API-KEY": os.environ["MANAGER_API_KEY"],
        "Accept": "application/json",
    }
    report: dict[str, object] = {}
    with httpx.Client(timeout=60.0, headers=headers) as client:
        for list_path, items_key, form_path, label in (
            ("/receipts", "receipts", "/receipt-form", "receipts"),
            ("/payments", "payments", "/payment-form", "payments"),
        ):
            page = client.get(base + list_path, params={"pageSize": 3}).json()
            items = page.get(items_key) or []
            entry: dict[str, object] = {
                "totalRecords": page.get("totalRecords"),
                "samples": [],
            }
            for item in items[:2]:
                key = item.get("key")
                form = client.get(f"{base}{form_path}/{key}").json()
                entry["samples"].append(
                    {
                        "top_keys": sorted(form.keys()) if isinstance(form, dict) else [],
                        "shape": shape(form),
                        "line0_keys": (
                            sorted(form["Lines"][0].keys())
                            if isinstance(form, dict)
                            and isinstance(form.get("Lines"), list)
                            and form["Lines"]
                            and isinstance(form["Lines"][0], dict)
                            else []
                        ),
                    }
                )
            report[label] = entry
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2)[:6000])


if __name__ == "__main__":
    main()

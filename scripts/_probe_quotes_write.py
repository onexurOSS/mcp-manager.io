"""Reversible quote write RE on TEST books. Creates then deletes one quote."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "specs" / "001-manager-readonly-mcp"
REPORT = OUT_DIR / "_quote-write-probe.json"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def redact(obj: object) -> object:
    """Drop bulky/binary fields; keep structure for research notes."""
    if isinstance(obj, dict):
        out: dict[str, object] = {}
        for key, value in obj.items():
            if key.lower() in {"image", "attachments", "pdf", "content"}:
                out[key] = "<redacted>"
            else:
                out[key] = redact(value)
        return out
    if isinstance(obj, list):
        return [redact(item) for item in obj[:20]]
    return obj


def main() -> None:
    env = load_env(ROOT / ".env")
    base = env["MANAGER_API_URL"].rstrip("/")
    headers = {
        "X-API-KEY": env["MANAGER_API_KEY"],
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    findings: dict[str, object] = {"business_probe": {}, "steps": []}

    with httpx.Client(timeout=60.0, headers=headers) as client:
        list_resp = client.get(base + "/sales-quotes", params={"pageSize": 1})
        list_body = list_resp.json()
        findings["business_probe"]["name"] = (
            (list_body.get("business") or {}).get("name")
        )
        findings["business_probe"]["sales_quotes_total"] = list_body.get("totalRecords")
        findings["steps"].append(
            {"op": "LIST /sales-quotes", "status": list_resp.status_code}
        )

        quotes = list_body.get("salesQuotes") or []
        sample_key = quotes[0]["key"] if quotes else None
        sample_form = None
        if sample_key:
            form_resp = client.get(f"{base}/sales-quote-form/{sample_key}")
            sample_form = form_resp.json() if form_resp.status_code == 200 else form_resp.text
            findings["steps"].append(
                {
                    "op": f"GET /sales-quote-form/{{key}}",
                    "status": form_resp.status_code,
                    "sample_keys": sorted(sample_form.keys())
                    if isinstance(sample_form, dict)
                    else None,
                    "sample_redacted": redact(sample_form)
                    if isinstance(sample_form, dict)
                    else str(sample_form)[:500],
                }
            )

        # PATCH support?
        if sample_key:
            patch_resp = client.patch(
                f"{base}/sales-quote-form/{sample_key}",
                json={},
            )
            findings["steps"].append(
                {
                    "op": "PATCH /sales-quote-form/{key}",
                    "status": patch_resp.status_code,
                    "body_preview": patch_resp.text[:300],
                }
            )

        cust = client.get(base + "/customers", params={"pageSize": 1})
        customers = (cust.json().get("customers") or []) if cust.status_code == 200 else []
        customer_key = customers[0]["key"] if customers else None
        findings["steps"].append(
            {
                "op": "LIST /customers",
                "status": cust.status_code,
                "has_customer": bool(customer_key),
            }
        )

        # Minimal create attempts
        create_bodies = [
            {"Description": "MCP RE probe quote (DELETE ME)", "QuoteDate": "2026-07-28"},
            {
                "Description": "MCP RE probe quote (DELETE ME)",
                "QuoteDate": "2026-07-28",
                "Customer": customer_key,
            },
            {
                "Description": "MCP RE probe quote (DELETE ME)",
                "Date": "2026-07-28",
                "Customer": customer_key,
            },
        ]
        # Prefer cloning a known-good form with a marker description
        if isinstance(sample_form, dict):
            clone = deepcopy(sample_form)
            for drop in ("Key", "key", "Name", "Reference", "QuoteNumber", "Number"):
                clone.pop(drop, None)
            clone["Description"] = "MCP RE probe quote (DELETE ME)"
            create_bodies.insert(0, clone)

        created_key = None
        create_meta: dict[str, object] | None = None
        for index, body in enumerate(create_bodies):
            response = client.post(base + "/sales-quote-form", json=body)
            location = response.headers.get("Location") or response.headers.get("location")
            preview = response.text[:800]
            step = {
                "op": f"POST /sales-quote-form attempt[{index}]",
                "status": response.status_code,
                "location": location,
                "response_headers_selected": {
                    k: response.headers.get(k)
                    for k in ("Location", "location", "Content-Type")
                },
                "body_preview": preview,
                "request_top_keys": sorted(body.keys())[:40]
                if isinstance(body, dict)
                else None,
            }
            findings["steps"].append(step)
            if response.status_code in {200, 201, 204}:
                create_meta = step
                # locate key
                if location and "/" in location:
                    created_key = location.rstrip("/").split("/")[-1]
                elif response.text:
                    try:
                        payload = response.json()
                        if isinstance(payload, dict):
                            created_key = payload.get("Key") or payload.get("key")
                        elif isinstance(payload, str):
                            created_key = payload
                    except Exception:
                        created_key = response.text.strip().strip('"')
                if not created_key:
                    # find via term search
                    search = client.get(
                        base + "/sales-quotes",
                        params={"term": "MCP RE probe quote", "pageSize": 5},
                    )
                    findings["steps"].append(
                        {
                            "op": "LIST term search after create",
                            "status": search.status_code,
                            "preview": search.text[:500],
                        }
                    )
                    for item in (search.json().get("salesQuotes") or []):
                        if "MCP RE probe" in str(item):
                            created_key = item.get("key")
                            break
                break

        findings["created_key"] = created_key
        findings["create_meta"] = create_meta

        if created_key:
            got = client.get(f"{base}/sales-quote-form/{created_key}")
            got_body = got.json() if got.status_code == 200 else None
            findings["steps"].append(
                {
                    "op": "GET created form",
                    "status": got.status_code,
                    "keys": sorted(got_body.keys()) if isinstance(got_body, dict) else None,
                    "redacted": redact(got_body) if isinstance(got_body, dict) else got.text[:400],
                }
            )

            # PUT: change description; then PUT omitting Description to see clear vs merge
            if isinstance(got_body, dict):
                updated = deepcopy(got_body)
                updated["Description"] = "MCP RE probe quote UPDATED (DELETE ME)"
                put1 = client.put(
                    f"{base}/sales-quote-form/{created_key}",
                    json=updated,
                )
                findings["steps"].append(
                    {
                        "op": "PUT full document",
                        "status": put1.status_code,
                        "preview": put1.text[:300],
                    }
                )
                after = client.get(f"{base}/sales-quote-form/{created_key}").json()
                findings["steps"].append(
                    {
                        "op": "GET after PUT",
                        "Description": after.get("Description")
                        if isinstance(after, dict)
                        else None,
                    }
                )

                omit = deepcopy(after) if isinstance(after, dict) else {}
                if isinstance(omit, dict):
                    had_desc = "Description" in omit
                    omit.pop("Description", None)
                    put2 = client.put(
                        f"{base}/sales-quote-form/{created_key}",
                        json=omit,
                    )
                    after2 = client.get(f"{base}/sales-quote-form/{created_key}").json()
                    findings["steps"].append(
                        {
                            "op": "PUT omit Description",
                            "status": put2.status_code,
                            "had_Description_before_omit": had_desc,
                            "Description_after": after2.get("Description")
                            if isinstance(after2, dict)
                            else None,
                            "put_semantics": (
                                "clears_or_null"
                                if isinstance(after2, dict)
                                and not after2.get("Description")
                                else "merge_or_keeps"
                            ),
                        }
                    )

            delete_resp = client.delete(f"{base}/sales-quote-form/{created_key}")
            findings["steps"].append(
                {
                    "op": "DELETE created",
                    "status": delete_resp.status_code,
                    "preview": delete_resp.text[:300],
                }
            )
            gone = client.get(f"{base}/sales-quote-form/{created_key}")
            findings["steps"].append(
                {
                    "op": "GET after DELETE",
                    "status": gone.status_code,
                    "preview": gone.text[:300],
                }
            )

            # recycle / undo hints
            for path in (
                "/deleted-sales-quotes",
                "/recycle-bin",
                "/trash",
                "/sales-quotes",
            ):
                response = client.get(base + path, params={"pageSize": 1, "term": created_key})
                findings["steps"].append(
                    {
                        "op": f"recycle probe {path}",
                        "status": response.status_code,
                        "preview": response.text[:200].replace("\n", " "),
                    }
                )

        # purchase quote symmetry (list + form get only; no create unless sales worked)
        pq = client.get(base + "/purchase-quotes", params={"pageSize": 1})
        pq_body = pq.json() if pq.status_code == 200 else {}
        findings["purchase"] = {
            "list_status": pq.status_code,
            "total": pq_body.get("totalRecords"),
            "items_key": "purchaseQuotes" if "purchaseQuotes" in pq_body else None,
        }
        pquotes = pq_body.get("purchaseQuotes") or []
        if pquotes:
            pk = pquotes[0]["key"]
            pf = client.get(f"{base}/purchase-quote-form/{pk}")
            body = pf.json() if pf.status_code == 200 else {}
            findings["purchase"]["form_status"] = pf.status_code
            findings["purchase"]["form_keys"] = (
                sorted(body.keys()) if isinstance(body, dict) else None
            )
            findings["purchase"]["form_redacted"] = (
                redact(body) if isinstance(body, dict) else None
            )

    REPORT.write_text(json.dumps(findings, indent=2, default=str), encoding="utf-8")
    print(f"wrote {REPORT}")
    print(json.dumps({"created_key": findings.get("created_key"), "business": findings["business_probe"]}, indent=2))


if __name__ == "__main__":
    main()

"""Live RE probe for Manager quote paths. TEST business only. Do not commit secrets."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "specs" / "001-manager-readonly-mcp"


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
    headers = {"X-API-KEY": env["MANAGER_API_KEY"], "Accept": "application/json"}
    lines: list[str] = []

    with httpx.Client(timeout=60.0, headers=headers) as client:
        openapi = None
        for suffix in ("", "/openapi.json", "/swagger.json"):
            response = client.get(base + suffix)
            content_type = response.headers.get("content-type", "")
            lines.append(
                f"GET {suffix or '/'} -> {response.status_code} "
                f"ct={content_type} len={len(response.content)}"
            )
            if response.status_code == 200 and "json" in content_type:
                data = response.json()
                if isinstance(data, dict) and "paths" in data:
                    openapi = data
                    break

        if openapi is None:
            lines.append("NO_OPENAPI")
        else:
            quote_paths = sorted(p for p in openapi["paths"] if "quote" in p.lower())
            lines.append(
                f"openapi_paths_total={len(openapi['paths'])} "
                f"quote_paths={len(quote_paths)}"
            )
            slim: dict[str, dict[str, object]] = {}
            for path in quote_paths:
                methods = {
                    method: {"summary": (openapi["paths"][path][method] or {}).get("summary")}
                    for method in openapi["paths"][path]
                    if method.lower() in {"get", "post", "put", "patch", "delete"}
                }
                slim[path] = methods
                lines.append(f"  {path}: {','.join(sorted(methods))}")
            (OUT_DIR / "_quote-openapi-snippet.json").write_text(
                json.dumps(slim, indent=2),
                encoding="utf-8",
            )
            lines.append("wrote _quote-openapi-snippet.json")

        candidates = [
            "/sales-quotes",
            "/sales-quote",
            "/quotes",
            "/sales-quotations",
            "/purchase-quotes",
            "/purchase-quote",
            "/purchase-quotations",
            "/sales-quote-form",
            "/purchase-quote-form",
        ]
        for path in candidates:
            response = client.get(base + path, params={"pageSize": 1})
            preview = response.text[:220].replace("\n", " ")
            lines.append(f"LIST {path} -> {response.status_code} {preview}")

    report = "\n".join(lines)
    (OUT_DIR / "_quote-probe-list.txt").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()

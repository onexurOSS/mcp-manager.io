"""Fake MCP-shaped child for dev_supervisor tests.

Speaks the same newline-delimited-JSON framing as the real manager-mcp
child, without any real FastMCP/Manager dependency, so supervisor tests are
fast and self-contained. Behavior is driven by `method` in each request:

- "initialize"          -> normal result, tagged with CHILD_MARKER env var
- "notifications/initialized" (no id) -> no response
- "ping"                -> normal result, tagged with CHILD_MARKER
- "slow_ping"           -> sleeps params.delay seconds, then responds
- "crash"               -> exits immediately, no response (simulates a bug)
- "emit_junk_then_ping" -> writes one non-JSON line to stdout first, then
                           responds to the ping normally (exercises the
                           supervisor's non-JSON line filter)
- "large_echo"          -> responds with a big padding string (params.size
                           bytes), simulating a real report payload -- a
                           single JSON-RPC line well over asyncio's default
                           64 KiB StreamReader limit
- anything else         -> generic ok result, tagged with CHILD_MARKER
"""

from __future__ import annotations

import json
import os
import sys
import time


def _write(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    marker = os.environ.get("CHILD_MARKER", "unknown")
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        msg_id = msg.get("id")

        if method == "notifications/initialized":
            continue
        if method == "crash":
            os._exit(1)
        if method == "slow_ping":
            time.sleep(msg.get("params", {}).get("delay", 0.3))
            _write({"jsonrpc": "2.0", "id": msg_id, "result": {"marker": marker}})
            continue
        if method == "emit_junk_then_ping":
            sys.stdout.write("not-valid-json-garbage\n")
            sys.stdout.flush()
            _write({"jsonrpc": "2.0", "id": msg_id, "result": {"marker": marker}})
            continue
        if method == "large_echo":
            size = msg.get("params", {}).get("size", 200_000)
            _write({"jsonrpc": "2.0", "id": msg_id, "result": {"padding": "x" * size}})
            continue

        _write({"jsonrpc": "2.0", "id": msg_id, "result": {"marker": marker, "method": method}})


if __name__ == "__main__":
    main()

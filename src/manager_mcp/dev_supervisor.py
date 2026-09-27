# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Dev-mode stdio supervisor for manager-mcp.

Claude Code launches `manager-mcp` once per session and talks MCP over its
stdin/stdout. Editing `src/manager_mcp/**` does not change that already-running
process -- tool registration (`@mcp.tool`, `register_write_tools()`) runs once
at import time. Reconnecting the MCP session does not reliably kill+respawn
the underlying stdio subprocess either, so newly added tools stay invisible
until a human restarts Claude Code.

This module is an opt-in (`MANAGER_MCP_DEV_SUPERVISOR=1`) transparent proxy
that sits between Claude Code and the real server:

    Claude Code <--stdio--> Supervisor <--stdio--> manager_mcp.server (child)

It watches `src/manager_mcp/**/*.py` for changes and, once no request is
in flight, kills and respawns the child -- replaying the one-time MCP
`initialize`/`notifications/initialized` handshake against the new child so
Claude Code never has to redo it. Child crashes are handled the same way.

Hard rule: nothing the supervisor itself emits ever reaches real stdout --
that stream is the MCP transport. All supervisor logging goes to stderr.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

DEV_SUPERVISOR_ENV = "MANAGER_MCP_DEV_SUPERVISOR"
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_RESTART_WAIT_TIMEOUT = 120.0
DEFAULT_HANDSHAKE_TIMEOUT = 10.0
DEFAULT_TERMINATE_TIMEOUT = 5.0

# asyncio.StreamReader's default readline() buffer is 64 KiB
# (asyncio.streams._DEFAULT_LIMIT); real report payloads (trial_balance,
# balance_sheet, ...) are one JSON-RPC line and routinely exceed that by
# 10-50x, which raises LimitOverrunError and kills the whole supervisor.
# Every StreamReader this module creates -- our own stdin and the child's
# stdout -- needs this raised well above any realistic single-line payload.
STREAM_LIMIT = 64 * 1024 * 1024


def is_dev_supervisor_enabled(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return env.get(DEV_SUPERVISOR_ENV, "").strip().lower() in {"1", "true", "yes"}


def scan_signature(root: Path) -> dict[str, int]:
    """mtime_ns per *.py file under `root`, relative-path keyed.

    Any change -- edit, add, remove, rename -- changes this dict, which is
    exactly what "source changed" needs to mean here.
    """
    if not root.is_dir():
        return {}
    return {
        str(p.relative_to(root)): p.stat().st_mtime_ns
        for p in sorted(root.rglob("*.py"))
    }


def _try_parse_json_line(line: bytes) -> Any | None:
    try:
        return json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


class RequestTracker:
    """Tracks JSON-RPC request ids in flight between client and child."""

    def __init__(self) -> None:
        self._in_flight: set[Any] = set()

    def observe_outgoing_to_child(self, line: bytes) -> None:
        msg = _try_parse_json_line(line)
        if isinstance(msg, dict) and "method" in msg and msg.get("id") is not None:
            self._in_flight.add(msg["id"])

    def observe_incoming_from_child(self, line: bytes) -> None:
        msg = _try_parse_json_line(line)
        if isinstance(msg, dict) and msg.get("id") is not None and (
            "result" in msg or "error" in msg
        ):
            self._in_flight.discard(msg["id"])

    def clear(self) -> None:
        self._in_flight.clear()

    @property
    def idle(self) -> bool:
        return not self._in_flight

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)


class HandshakeRecorder:
    """Captures the session's one-time initialize handshake for replay."""

    def __init__(self) -> None:
        self.initialize_request: bytes | None = None
        self.initialized_notification: bytes | None = None

    def observe(self, line: bytes) -> None:
        msg = _try_parse_json_line(line)
        if not isinstance(msg, dict):
            return
        method = msg.get("method")
        if method == "initialize" and self.initialize_request is None:
            self.initialize_request = line
        elif method == "notifications/initialized":
            self.initialized_notification = line

    @property
    def has_handshake(self) -> bool:
        return self.initialize_request is not None


@dataclass
class SupervisorConfig:
    watch_root: Path
    child_cmd: list[str]
    child_env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    child_cwd: Path | None = None
    poll_interval: float = DEFAULT_POLL_INTERVAL
    restart_wait_timeout: float = DEFAULT_RESTART_WAIT_TIMEOUT
    handshake_timeout: float = DEFAULT_HANDSHAKE_TIMEOUT
    terminate_timeout: float = DEFAULT_TERMINATE_TIMEOUT
    log_stream: TextIO = field(default_factory=lambda: sys.stderr)


class Supervisor:
    """Owns one child process and transparently proxies stdio to/from it."""

    def __init__(
        self,
        config: SupervisorConfig,
        stdin_reader: asyncio.StreamReader,
        stdout_writer: asyncio.StreamWriter,
    ) -> None:
        self._cfg = config
        self._stdin = stdin_reader
        self._stdout = stdout_writer
        self._tracker = RequestTracker()
        self._handshake = HandshakeRecorder()
        self.child: asyncio.subprocess.Process | None = None
        self.child_started_at: float | None = None
        self.restart_count = 0
        self.crash_count = 0
        self._restart_lock = asyncio.Lock()
        self._restarting = False
        self._paused_for_restart = False
        self._pending_client_lines: list[bytes] = []
        self._stop = asyncio.Event()
        self._signature = scan_signature(self._cfg.watch_root)

    def _log(self, msg: str) -> None:
        print(f"[manager-mcp-supervisor] {msg}", file=self._cfg.log_stream, flush=True)

    async def _spawn_child(self) -> asyncio.subprocess.Process:
        proc = await asyncio.create_subprocess_exec(
            *self._cfg.child_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
            env=self._cfg.child_env,
            cwd=str(self._cfg.child_cwd) if self._cfg.child_cwd else None,
            limit=STREAM_LIMIT,
        )
        self.child_started_at = time.time()
        self._log(f"child started pid={proc.pid}")
        return proc

    async def start(self) -> None:
        self.child = await self._spawn_child()
        await asyncio.gather(
            self._pump_client_to_child(),
            self._pump_child_to_client(),
            self._watch_source(),
            return_exceptions=False,
        )

    def stop(self) -> None:
        self._stop.set()

    async def shutdown(self) -> None:
        self.stop()
        if self.child is not None and self.child.returncode is None:
            self.child.terminate()
            try:
                await asyncio.wait_for(self.child.wait(), timeout=self._cfg.terminate_timeout)
            except asyncio.TimeoutError:
                self.child.kill()
                await self.child.wait()

    async def _pump_client_to_child(self) -> None:
        while not self._stop.is_set():
            line = await self._stdin.readline()
            if not line:
                self._log("client stdin closed; shutting down")
                self.stop()
                await self.shutdown()
                return
            self._handshake.observe(line)
            if self._paused_for_restart:
                self._pending_client_lines.append(line)
                continue
            await self._write_to_child(line)

    async def _write_to_child(self, line: bytes) -> None:
        assert self.child is not None and self.child.stdin is not None
        self._tracker.observe_outgoing_to_child(line)
        self.child.stdin.write(line)
        await self.child.stdin.drain()

    async def _pump_child_to_client(self) -> None:
        while not self._stop.is_set():
            if self._restarting:
                await asyncio.sleep(0.02)
                continue
            child = self.child
            assert child is not None and child.stdout is not None
            line = await child.stdout.readline()
            if not line:
                if self._restarting or self._stop.is_set():
                    continue
                await self._handle_unexpected_exit(child)
                continue
            if _try_parse_json_line(line) is None:
                self._log(f"dropped non-JSON line from child stdout: {line!r}")
                continue
            self._tracker.observe_incoming_from_child(line)
            self._stdout.write(line)
            await self._stdout.drain()

    async def _watch_source(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._cfg.poll_interval)
            if self._stop.is_set():
                return
            current = scan_signature(self._cfg.watch_root)
            if current != self._signature:
                self._signature = current
                self._log("source change detected; scheduling restart")
                await self._restart(reason="source_change")

    async def _restart(self, *, reason: str) -> None:
        async with self._restart_lock:
            if self._stop.is_set():
                return
            self._paused_for_restart = True
            waited = 0.0
            while not self._tracker.idle and waited < self._cfg.restart_wait_timeout:
                await asyncio.sleep(0.1)
                waited += 0.1
            if not self._tracker.idle:
                self._log(
                    f"restart_wait_timeout hit with {self._tracker.in_flight_count} "
                    "request(s) still in flight; restarting anyway"
                )
            self._log(f"restarting child (reason={reason})")
            await self._swap_child()
            self._paused_for_restart = False
            pending, self._pending_client_lines = self._pending_client_lines, []
            for line in pending:
                await self._write_to_child(line)

    async def _swap_child(self) -> None:
        old = self.child
        self._restarting = True
        try:
            if old is not None and old.returncode is None:
                old.terminate()
                try:
                    await asyncio.wait_for(old.wait(), timeout=self._cfg.terminate_timeout)
                except asyncio.TimeoutError:
                    old.kill()
                    await old.wait()
            new_child = await self._spawn_child()
            self.child = new_child
            self._tracker.clear()
            await self._replay_handshake(new_child)
            self.restart_count += 1
        finally:
            self._restarting = False

    async def _handle_unexpected_exit(self, dead_child: asyncio.subprocess.Process) -> None:
        async with self._restart_lock:
            if dead_child is not self.child or self._stop.is_set():
                return
            self.crash_count += 1
            self._log(f"child pid={dead_child.pid} exited unexpectedly; respawning")
            self._restarting = True
            try:
                new_child = await self._spawn_child()
                self.child = new_child
                self._tracker.clear()
                await self._replay_handshake(new_child)
            finally:
                self._restarting = False

    async def _replay_handshake(self, child: asyncio.subprocess.Process) -> None:
        if not self._handshake.has_handshake:
            return
        assert child.stdin is not None and child.stdout is not None
        child.stdin.write(self._handshake.initialize_request)  # type: ignore[arg-type]
        await child.stdin.drain()
        try:
            resp = await asyncio.wait_for(
                child.stdout.readline(), timeout=self._cfg.handshake_timeout
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("child did not respond to replayed initialize") from exc
        if not resp:
            raise RuntimeError("child exited during handshake replay")
        if self._handshake.initialized_notification is not None:
            child.stdin.write(self._handshake.initialized_notification)
            await child.stdin.drain()

    @property
    def status(self) -> dict[str, Any]:
        return {
            "child_pid": self.child.pid if self.child else None,
            "child_started_at": self.child_started_at,
            "restart_count": self.restart_count,
            "crash_count": self.crash_count,
            "in_flight_requests": self._tracker.in_flight_count,
        }


async def _open_real_stdio() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=STREAM_LIMIT)
    reader_protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: reader_protocol, sys.stdin)

    writer_transport, writer_protocol = await loop.connect_write_pipe(
        lambda: asyncio.streams.FlowControlMixin(loop=loop), sys.stdout
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, loop)
    return reader, writer


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_child_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop(DEV_SUPERVISOR_ENV, None)
    return env


async def _run_real_supervisor() -> None:
    repo_root = _repo_root()
    config = SupervisorConfig(
        watch_root=repo_root / "src" / "manager_mcp",
        child_cmd=[sys.executable, "-m", "manager_mcp.server"],
        child_env=_default_child_env(),
        child_cwd=repo_root,
    )
    stdin_reader, stdout_writer = await _open_real_stdio()
    supervisor = Supervisor(config, stdin_reader, stdout_writer)
    try:
        await supervisor.start()
    except asyncio.CancelledError:
        await supervisor.shutdown()
        raise


def run_supervisor() -> None:
    asyncio.run(_run_real_supervisor())


def main() -> None:
    run_supervisor()


if __name__ == "__main__":
    main()

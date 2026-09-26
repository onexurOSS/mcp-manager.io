"""Tests for the dev-mode stdio supervisor (manager_mcp.dev_supervisor).

Integration-style tests spawn a small fake child process (tests/fixtures/
fake_mcp_child.py) that speaks the same newline-delimited-JSON framing as
the real manager-mcp server, and drive the supervisor through a pair of
pipes standing in for Claude Code's stdin/stdout. This exercises the real
subprocess lifecycle (spawn/terminate/crash) without depending on Manager
API credentials or FastMCP tool registration.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
from pathlib import Path

import pytest

from manager_mcp.dev_supervisor import (
    DEV_SUPERVISOR_ENV,
    STREAM_LIMIT,
    HandshakeRecorder,
    RequestTracker,
    Supervisor,
    SupervisorConfig,
    is_dev_supervisor_enabled,
    scan_signature,
)

FAKE_CHILD = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_child.py"


# ── is_dev_supervisor_enabled ────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "YES"])
def test_dev_supervisor_enabled_truthy(value: str) -> None:
    assert is_dev_supervisor_enabled({DEV_SUPERVISOR_ENV: value})


@pytest.mark.parametrize("value", ["0", "false", "", "no"])
def test_dev_supervisor_enabled_falsy(value: str) -> None:
    assert not is_dev_supervisor_enabled({DEV_SUPERVISOR_ENV: value})


def test_dev_supervisor_enabled_missing_env() -> None:
    assert not is_dev_supervisor_enabled({})


# ── scan_signature ────────────────────────────────────────────────────────


def test_scan_signature_detects_edit(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    before = scan_signature(tmp_path)
    os.utime(f, (0, 0))
    f.write_text("x = 2\n")
    after = scan_signature(tmp_path)
    assert before != after


def test_scan_signature_detects_add_and_remove(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    sig1 = scan_signature(tmp_path)
    b = tmp_path / "b.py"
    b.write_text("y = 2\n")
    sig2 = scan_signature(tmp_path)
    assert sig1 != sig2
    b.unlink()
    sig3 = scan_signature(tmp_path)
    assert sig3 == sig1


def test_scan_signature_ignores_non_python_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    sig1 = scan_signature(tmp_path)
    (tmp_path / "notes.txt").write_text("hello\n")
    sig2 = scan_signature(tmp_path)
    assert sig1 == sig2


def test_scan_signature_missing_dir_is_empty(tmp_path: Path) -> None:
    assert scan_signature(tmp_path / "nope") == {}


# ── RequestTracker ────────────────────────────────────────────────────────


def test_request_tracker_tracks_request_until_response() -> None:
    t = RequestTracker()
    t.observe_outgoing_to_child(b'{"jsonrpc":"2.0","id":1,"method":"ping"}')
    assert not t.idle
    assert t.in_flight_count == 1
    t.observe_incoming_from_child(b'{"jsonrpc":"2.0","id":1,"result":{}}')
    assert t.idle


def test_request_tracker_ignores_notifications() -> None:
    t = RequestTracker()
    t.observe_outgoing_to_child(b'{"jsonrpc":"2.0","method":"notifications/initialized"}')
    assert t.idle


def test_request_tracker_ignores_non_json_lines() -> None:
    t = RequestTracker()
    t.observe_outgoing_to_child(b"not json at all")
    assert t.idle


def test_request_tracker_error_response_clears_in_flight() -> None:
    t = RequestTracker()
    t.observe_outgoing_to_child(b'{"jsonrpc":"2.0","id":5,"method":"boom"}')
    t.observe_incoming_from_child(b'{"jsonrpc":"2.0","id":5,"error":{"code":-1}}')
    assert t.idle


def test_request_tracker_clear() -> None:
    t = RequestTracker()
    t.observe_outgoing_to_child(b'{"jsonrpc":"2.0","id":1,"method":"ping"}')
    t.clear()
    assert t.idle


# ── HandshakeRecorder ─────────────────────────────────────────────────────


def test_handshake_recorder_captures_initialize_and_initialized() -> None:
    r = HandshakeRecorder()
    init_line = b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
    notif_line = b'{"jsonrpc":"2.0","method":"notifications/initialized"}'
    r.observe(init_line)
    r.observe(notif_line)
    assert r.has_handshake
    assert r.initialize_request == init_line
    assert r.initialized_notification == notif_line


def test_handshake_recorder_keeps_first_initialize_only() -> None:
    r = HandshakeRecorder()
    first = b'{"jsonrpc":"2.0","id":1,"method":"initialize"}'
    second = b'{"jsonrpc":"2.0","id":2,"method":"initialize"}'
    r.observe(first)
    r.observe(second)
    assert r.initialize_request == first


def test_handshake_recorder_ignores_unrelated_and_non_json() -> None:
    r = HandshakeRecorder()
    r.observe(b'{"jsonrpc":"2.0","id":1,"method":"ping"}')
    r.observe(b"garbage")
    assert not r.has_handshake


# ── Supervisor integration harness ───────────────────────────────────────


async def _pipe_reader(fd: int) -> asyncio.StreamReader:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=STREAM_LIMIT)
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, os.fdopen(fd, "rb", 0))
    return reader


async def _pipe_writer(fd: int) -> asyncio.StreamWriter:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.connect_write_pipe(
        lambda: asyncio.streams.FlowControlMixin(loop=loop), os.fdopen(fd, "wb", 0)
    )
    return asyncio.StreamWriter(transport, protocol, None, loop)


class Harness:
    def __init__(
        self,
        supervisor: Supervisor,
        client_write: asyncio.StreamWriter,
        client_read: asyncio.StreamReader,
        log_stream: io.StringIO,
        watch_root: Path,
    ) -> None:
        self.supervisor = supervisor
        self.client_write = client_write
        self.client_read = client_read
        self.log_stream = log_stream
        self.watch_root = watch_root
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.supervisor.start())

    async def send(self, obj: dict) -> None:
        self.client_write.write((json.dumps(obj) + "\n").encode())
        await self.client_write.drain()

    async def recv(self, timeout: float = 5.0) -> dict:
        line = await asyncio.wait_for(self.client_read.readline(), timeout=timeout)
        assert line, "client received EOF instead of a line"
        return json.loads(line)

    async def send_initialize(self, req_id: int = 1) -> dict:
        await self.send({"jsonrpc": "2.0", "id": req_id, "method": "initialize", "params": {}})
        resp = await self.recv()
        await self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return resp

    async def aclose(self) -> None:
        self.client_write.close()
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.task.cancel()
        await self.supervisor.shutdown()


async def _make_harness(
    tmp_path: Path,
    *,
    marker: str = "A",
    poll_interval: float = 0.05,
    restart_wait_timeout: float = 5.0,
) -> Harness:
    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    (watch_root / "module.py").write_text("value = 1\n")

    c2s_r, c2s_w = os.pipe()
    s2c_r, s2c_w = os.pipe()

    supervisor_stdin = await _pipe_reader(c2s_r)
    supervisor_stdout = await _pipe_writer(s2c_w)
    client_write = await _pipe_writer(c2s_w)
    client_read = await _pipe_reader(s2c_r)

    config = SupervisorConfig(
        watch_root=watch_root,
        child_cmd=[sys.executable, str(FAKE_CHILD)],
        child_env={**os.environ, "CHILD_MARKER": marker},
        poll_interval=poll_interval,
        restart_wait_timeout=restart_wait_timeout,
        handshake_timeout=3.0,
        terminate_timeout=2.0,
        log_stream=io.StringIO(),
    )
    supervisor = Supervisor(config, supervisor_stdin, supervisor_stdout)
    return Harness(supervisor, client_write, client_read, config.log_stream, watch_root)


def _touch_source_change(watch_root: Path) -> None:
    f = watch_root / "module.py"
    f.write_text(f"value = {os.urandom(2).hex()}\n")


async def _poll_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> None:
    waited = 0.0
    while not predicate():
        if waited >= timeout:
            raise AssertionError(f"condition not met within {timeout}s")
        await asyncio.sleep(interval)
        waited += interval


# ── Supervisor startup / child startup ───────────────────────────────────


@pytest.mark.asyncio
async def test_supervisor_startup_spawns_child(tmp_path: Path) -> None:
    h = await _make_harness(tmp_path)
    h.start()
    try:
        await _poll_until(lambda: h.supervisor.child is not None)
        assert h.supervisor.child.pid > 0
        assert h.supervisor.child.returncode is None
    finally:
        await h.aclose()


# ── stdin/stdout proxying ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stdio_proxy_roundtrip(tmp_path: Path) -> None:
    h = await _make_harness(tmp_path, marker="alpha")
    h.start()
    try:
        init_resp = await h.send_initialize()
        assert init_resp["result"]["marker"] == "alpha"

        await h.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        resp = await h.recv()
        assert resp == {"jsonrpc": "2.0", "id": 2, "result": {"marker": "alpha", "method": "ping"}}
    finally:
        await h.aclose()


@pytest.mark.asyncio
async def test_large_response_past_default_asyncio_limit_survives(tmp_path: Path) -> None:
    """Real report tools (trial_balance, balance_sheet, ...) return single
    JSON-RPC lines of hundreds of KB to several MB. asyncio.StreamReader's
    default readline() limit is 64 KiB (asyncio.streams._DEFAULT_LIMIT) --
    exceeding it raises LimitOverrunError and used to kill the supervisor.
    This sends a line comfortably past that default to prove the fix."""
    h = await _make_harness(tmp_path, marker="alpha")
    h.start()
    try:
        await h.send_initialize()
        size = 500_000  # >> 65536, comparable to a real trial_balance payload
        await h.send(
            {"jsonrpc": "2.0", "id": 55, "method": "large_echo", "params": {"size": size}}
        )
        resp = await h.recv(timeout=10.0)
        assert len(resp["result"]["padding"]) == size

        # Connection must still be alive afterward -- no crash/respawn.
        original_pid = h.supervisor.child.pid
        await h.send({"jsonrpc": "2.0", "id": 56, "method": "ping"})
        resp2 = await h.recv(timeout=5.0)
        assert resp2["result"]["marker"] == "alpha"
        assert h.supervisor.child.pid == original_pid
        assert h.supervisor.crash_count == 0
    finally:
        await h.aclose()


@pytest.mark.asyncio
async def test_non_json_child_output_is_dropped_not_forwarded(tmp_path: Path) -> None:
    h = await _make_harness(tmp_path, marker="alpha")
    h.start()
    try:
        await h.send_initialize()
        await h.send({"jsonrpc": "2.0", "id": 9, "method": "emit_junk_then_ping"})
        resp = await h.recv()
        # The garbage line never reached the client as a message (json.loads
        # in recv() would have failed on it); it must show up in the
        # supervisor's own log instead, not vanish silently.
        assert resp["result"]["marker"] == "alpha"
        assert "not-valid-json-garbage" in h.log_stream.getvalue()
    finally:
        await h.aclose()


@pytest.mark.asyncio
async def test_supervisor_never_writes_its_own_logs_to_client_stdout(tmp_path: Path) -> None:
    h = await _make_harness(tmp_path, marker="alpha", poll_interval=0.05)
    h.start()
    try:
        await h.send_initialize()
        _touch_source_change(h.watch_root)
        await _poll_until(lambda: h.supervisor.restart_count >= 1)
        await h.send({"jsonrpc": "2.0", "id": 100, "method": "ping"})
        resp = await h.recv()
        assert resp["result"]["marker"] == "alpha"
        assert "manager-mcp-supervisor" in h.log_stream.getvalue()  # logs did happen
    finally:
        await h.aclose()


# ── restart on source change ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restart_on_source_change_spawns_new_child(tmp_path: Path) -> None:
    h = await _make_harness(tmp_path, poll_interval=0.05)
    h.start()
    try:
        await h.send_initialize()
        old_pid = h.supervisor.child.pid

        _touch_source_change(h.watch_root)
        await _poll_until(lambda: h.supervisor.restart_count >= 1)

        assert h.supervisor.child.pid != old_pid
        assert h.supervisor.child.returncode is None

        await h.send({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        resp = await h.recv()
        assert resp["id"] == 3
        assert resp["result"]["marker"] == "A"
    finally:
        await h.aclose()


@pytest.mark.asyncio
async def test_restart_does_not_leak_synthetic_handshake_to_client(tmp_path: Path) -> None:
    """After a restart replays initialize/initialized against the new child
    internally, the client must see exactly the next real response it asked
    for -- no extra unsolicited message from the replay."""
    h = await _make_harness(tmp_path, poll_interval=0.05)
    h.start()
    try:
        await h.send_initialize()
        _touch_source_change(h.watch_root)
        await _poll_until(lambda: h.supervisor.restart_count >= 1)

        await h.send({"jsonrpc": "2.0", "id": 42, "method": "ping"})
        resp = await h.recv(timeout=2.0)
        assert resp["id"] == 42
    finally:
        await h.aclose()


@pytest.mark.asyncio
async def test_restart_waits_for_in_flight_request(tmp_path: Path) -> None:
    h = await _make_harness(tmp_path, poll_interval=0.05, restart_wait_timeout=5.0)
    h.start()
    try:
        await h.send_initialize()
        original_pid = h.supervisor.child.pid

        await h.send(
            {"jsonrpc": "2.0", "id": 7, "method": "slow_ping", "params": {"delay": 0.6}}
        )
        await asyncio.sleep(0.1)  # let the request register as in-flight
        _touch_source_change(h.watch_root)

        await asyncio.sleep(0.3)  # well before slow_ping resolves
        assert h.supervisor.restart_count == 0
        assert h.supervisor.child.pid == original_pid

        resp = await h.recv(timeout=2.0)
        assert resp["id"] == 7

        await _poll_until(lambda: h.supervisor.restart_count >= 1, timeout=3.0)
        assert h.supervisor.child.pid != original_pid
    finally:
        await h.aclose()


# ── crash / respawn ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_child_crash_triggers_respawn(tmp_path: Path) -> None:
    h = await _make_harness(tmp_path, marker="beta")
    h.start()
    try:
        await h.send_initialize()
        original_pid = h.supervisor.child.pid

        await h.send({"jsonrpc": "2.0", "id": 11, "method": "crash"})

        await _poll_until(lambda: h.supervisor.crash_count >= 1, timeout=5.0)
        await _poll_until(
            lambda: h.supervisor.child is not None and h.supervisor.child.pid != original_pid,
            timeout=5.0,
        )

        await h.send({"jsonrpc": "2.0", "id": 12, "method": "ping"})
        resp = await h.recv(timeout=3.0)
        assert resp["result"]["marker"] == "beta"
    finally:
        await h.aclose()


# ── clean shutdown ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clean_shutdown_on_client_stdin_close(tmp_path: Path) -> None:
    h = await _make_harness(tmp_path)
    h.start()
    try:
        await h.send_initialize()
        child = h.supervisor.child
        h.client_write.close()
        await asyncio.wait_for(h.task, timeout=5.0)
        await _poll_until(lambda: child.returncode is not None, timeout=5.0)
    finally:
        h.task = None  # already awaited; avoid double-await in aclose
        await h.supervisor.shutdown()


@pytest.mark.asyncio
async def test_shutdown_is_idempotent_and_bounded(tmp_path: Path) -> None:
    h = await _make_harness(tmp_path)
    h.start()
    try:
        await h.send_initialize()
    finally:
        await h.aclose()
        await asyncio.wait_for(h.supervisor.shutdown(), timeout=5.0)  # no hang, no raise

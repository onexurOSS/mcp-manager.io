# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""A startup warning when the API key would go over plain http to a remote host."""

from __future__ import annotations

import logging

import pytest

from manager_mcp.client import ManagerClient
from manager_mcp.transport_check import plaintext_remote_warning

KEY = "SENTINEL-KEY-0123456789abcdef"


@pytest.mark.parametrize(
    "url",
    [
        "http://manager.example.com/api2",
        "http://192.168.1.20:55667/api2",
        "http://10.0.0.5/api2",
        "HTTP://Manager.Example.com/api2",
    ],
)
def test_plain_http_to_a_remote_host_warns(url: str) -> None:
    message = plaintext_remote_warning(url)
    assert message is not None
    assert "https" in message and "plain http" in message


@pytest.mark.parametrize(
    "url",
    [
        "https://manager.example.com/api2",
        "http://127.0.0.1:55667/api2",
        "http://127.1.2.3/api2",
        "http://localhost:55667/api2",
        "http://[::1]:55667/api2",
        "http://manager.localhost/api2",
        "",
    ],
)
def test_https_and_loopback_do_not_warn(url: str) -> None:
    assert plaintext_remote_warning(url) is None


def test_client_logs_the_warning_once_and_never_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    ManagerClient("http://manager.example.com/api2", KEY)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert KEY not in warnings[0].getMessage()
    assert "manager.example.com" in warnings[0].getMessage()


def test_client_is_silent_for_loopback_and_https(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    ManagerClient("http://127.0.0.1:55667/api2", KEY)
    ManagerClient("https://manager.example.com/api2", KEY)
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]

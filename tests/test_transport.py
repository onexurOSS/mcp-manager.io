# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Pure config-resolution tests for manager_mcp.transport -- no server, no network."""

from __future__ import annotations

import pytest

from manager_mcp.transport import (
    HTTP_AUTH_TOKEN_ENV,
    HTTP_HOST_ENV,
    HTTP_PORT_ENV,
    TRANSPORT_ENV,
    TransportConfig,
    TransportConfigError,
    resolve_transport_config,
    warn_if_no_http_auth,
)


def test_default_is_stdio_with_no_env_at_all() -> None:
    config = resolve_transport_config({})
    assert config.transport == "stdio"
    assert config.host is None
    assert config.port is None
    assert config.is_http is False


def test_empty_string_transport_env_falls_back_to_stdio() -> None:
    # An existing deployment's process manager might set the var to "" rather than
    # leaving it unset -- must behave identically to unset, not error.
    config = resolve_transport_config({TRANSPORT_ENV: ""})
    assert config.transport == "stdio"


def test_http_transport_selected() -> None:
    config = resolve_transport_config({TRANSPORT_ENV: "http"})
    assert config.transport == "http"
    assert config.is_http is True


@pytest.mark.parametrize("value", ["HTTP", " http ", "Http"])
def test_transport_env_is_case_and_whitespace_insensitive(value: str) -> None:
    assert resolve_transport_config({TRANSPORT_ENV: value}).transport == "http"


def test_streamable_http_and_sse_are_also_accepted() -> None:
    config = resolve_transport_config({TRANSPORT_ENV: "streamable-http"})
    assert config.transport == "streamable-http"
    assert resolve_transport_config({TRANSPORT_ENV: "sse"}).transport == "sse"


def test_unknown_transport_raises() -> None:
    with pytest.raises(TransportConfigError, match="not a recognised transport"):
        resolve_transport_config({TRANSPORT_ENV: "carrier-pigeon"})


def test_host_and_port_ignored_in_stdio_mode() -> None:
    # Set but irrelevant -- must not raise, must not leak into the config, matching this
    # repo's existing tolerance for irrelevant env vars being present.
    config = resolve_transport_config({HTTP_HOST_ENV: "0.0.0.0", HTTP_PORT_ENV: "9999"})
    assert config.transport == "stdio"
    assert config.host is None
    assert config.port is None


def test_host_and_port_passthrough_in_http_mode() -> None:
    config = resolve_transport_config(
        {TRANSPORT_ENV: "http", HTTP_HOST_ENV: "0.0.0.0", HTTP_PORT_ENV: "8765"}
    )
    assert config.host == "0.0.0.0"
    assert config.port == 8765


def test_http_mode_with_no_host_or_port_set_leaves_both_none() -> None:
    # None is the sentinel "let FastMCP use its own default (127.0.0.1:8000)" --
    # this module must not invent its own default to track separately.
    config = resolve_transport_config({TRANSPORT_ENV: "http"})
    assert config.host is None
    assert config.port is None


def test_non_integer_port_raises() -> None:
    with pytest.raises(TransportConfigError, match="not an integer"):
        resolve_transport_config({TRANSPORT_ENV: "http", HTTP_PORT_ENV: "not-a-port"})


def test_no_auth_token_by_default() -> None:
    config = resolve_transport_config({TRANSPORT_ENV: "http"})
    assert config.auth_token is None


def test_auth_token_passthrough() -> None:
    config = resolve_transport_config({TRANSPORT_ENV: "http", HTTP_AUTH_TOKEN_ENV: "s3cret"})
    assert config.auth_token == "s3cret"


def test_auth_token_ignored_in_stdio_mode() -> None:
    config = resolve_transport_config({HTTP_AUTH_TOKEN_ENV: "s3cret"})
    assert config.auth_token is None


def test_warn_if_no_http_auth_prints_for_http_without_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    warn_if_no_http_auth(TransportConfig(transport="http"))
    assert "no transport-level authentication" in capsys.readouterr().err


def test_warn_if_no_http_auth_silent_when_token_set(capsys: pytest.CaptureFixture[str]) -> None:
    warn_if_no_http_auth(TransportConfig(transport="http", auth_token="s3cret"))
    assert capsys.readouterr().err == ""


def test_warn_if_no_http_auth_silent_for_stdio(capsys: pytest.CaptureFixture[str]) -> None:
    warn_if_no_http_auth(TransportConfig(transport="stdio"))
    assert capsys.readouterr().err == ""

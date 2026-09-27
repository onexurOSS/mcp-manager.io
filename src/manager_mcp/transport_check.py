# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Xalterra Ltd, trading as Onexur
"""Warn when the Manager API key would travel over plain http to a remote host.

The key is sent in the X-API-KEY header on every request. Over https, or to a loopback
address such as the default Manager desktop address, it stays private. Over http to any
other host, anyone on the network path can read it.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


def _is_loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def plaintext_remote_warning(base_url: str) -> str | None:
    """Return a warning message when base_url is plain http to a non-loopback host."""
    parts = urlsplit(base_url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() != "http" or not host or _is_loopback(host):
        return None
    return (
        f"MANAGER_API_URL uses plain http to the non-loopback host {host!r}. The Manager "
        "API key is sent in a header on every request and can be read by anyone on the "
        "network path. Use https, or a loopback address, unless the network is fully trusted."
    )


__all__ = ["plaintext_remote_warning"]

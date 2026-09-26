"""Branding icons advertised on the FastMCP server."""

from __future__ import annotations

from manager_mcp.server import _ICON_PATH, mcp, server_icons


def test_server_icons_include_data_uri_and_https() -> None:
    icons = server_icons()
    assert _ICON_PATH.is_file()
    assert len(icons) >= 2
    assert icons[0].src.startswith("data:image/png;base64,")
    assert icons[0].mimeType == "image/png"
    assert icons[0].sizes == ["512x512"]
    assert icons[1].src.startswith("https://")
    assert icons[1].src.endswith("icon-512.png")


def test_fastmcp_configured_with_icons_and_website() -> None:
    assert mcp.name == "manager-mcp"
    assert getattr(mcp, "website_url", None) == "https://www.manager.io/"
    configured = getattr(mcp, "icons", None)
    assert configured is not None
    assert len(configured) >= 1

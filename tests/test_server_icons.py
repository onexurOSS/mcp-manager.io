"""The server does not embed third-party branding."""

from __future__ import annotations

from manager_mcp.server import mcp


def test_fastmcp_advertises_website_and_no_embedded_icons() -> None:
    assert mcp.name == "manager-mcp"
    assert getattr(mcp, "website_url", None) == "https://www.manager.io/"
    # No Manager.io logos are shipped, so serverInfo carries no icons.
    assert not getattr(mcp, "icons", None)

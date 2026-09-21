"""A real MCP server over stdio whose only answer is the credential it was given.

Integration tests use it as an observable connector: the ``PROBE_TOKEN`` a
session is launched with is the answer ``whoami`` returns, so calling a tool
tells you *whose* connector that call ran through — which is the whole question
when one agent is shared between users.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("probe")


@mcp.tool()
def whoami() -> str:
    """Return the credential this MCP server was launched with."""
    return f"token={os.environ.get('PROBE_TOKEN', '')}"


if __name__ == "__main__":
    mcp.run(transport="stdio")

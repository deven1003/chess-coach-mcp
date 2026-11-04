"""MCP resources: data the AI reads like files."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import games


def register_resources(server: MCPServer) -> None:
    games.register(server)


__all__ = ["register_resources"]

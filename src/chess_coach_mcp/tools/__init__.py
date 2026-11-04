"""MCP tools: actions the AI can call. Each is a thin wrapper over ``core``."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import analyze, games, stats


def register_tools(server: MCPServer) -> None:
    games.register(server)
    analyze.register(server)
    stats.register(server)


__all__ = ["register_tools"]

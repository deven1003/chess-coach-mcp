"""MCP prompts: reusable coaching templates the user can invoke from their client."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import coach


def register_prompts(server: MCPServer) -> None:
    coach.register(server)


__all__ = ["register_prompts"]

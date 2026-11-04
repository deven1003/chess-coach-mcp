"""Tool: get_recent_games."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from chess_coach_mcp.errors import tool_errors
from chess_coach_mcp.services import get_services, normalize_platform

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


async def get_recent_games(
    username: str, platform: str = "lichess", count: int = 10
) -> dict[str, Any]:
    """List a player's most recent games from Lichess or Chess.com.

    Returns one entry per game with the game_id (use it with analyze_game / find_blunders),
    the opponent, the result from the player's point of view, the opening and the date.
    Games are cached locally so later tools can find them by id.

    Args:
        username: The player's username on the chosen platform.
        platform: "lichess" (default) or "chesscom".
        count: How many games to return (1-100).
    """
    with tool_errors():
        services = get_services()
        resolved = normalize_platform(platform)
        count = max(1, min(int(count), 100))
        games = await services.recent_games(username, resolved, count)
    items = []
    for game in games:
        summary = game.summary.to_dict(username)
        items.append(
            {
                "game_id": summary["game_id"],
                "platform": summary["platform"],
                "date": summary["date"],
                "user_color": summary["user_color"],
                "user_result": summary["user_result"],
                "opponent": summary["opponent"],
                "result": summary["result"],
                "opening": summary["opening"],
                "eco": summary["eco"],
                "speed": summary["speed"],
                "time_control": summary["time_control"],
                "moves_played": len(game.moves),
                "termination": summary["termination"],
                "url": summary["url"],
            }
        )
    return {"username": username, "platform": resolved, "count": len(items), "games": items}


def register(server: MCPServer) -> None:
    server.add_tool(get_recent_games, annotations=READ_ONLY)

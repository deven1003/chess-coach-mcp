"""Tool: get_opening_stats."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from chess_coach_mcp.errors import tool_errors
from chess_coach_mcp.services import get_services, normalize_platform

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def _opening_family(name: str | None) -> str:
    """Collapse "Sicilian Defense: Najdorf Variation, English Attack" into "Sicilian Defense"."""
    if not name:
        return "Unknown opening"
    return name.split(":", 1)[0].split(",", 1)[0].strip()


async def get_opening_stats(
    username: str,
    platform: str = "lichess",
    color: str = "both",
    count: int = 50,
    group_by_family: bool = True,
) -> dict[str, Any]:
    """Win/draw/loss record per opening over a player's recent games.

    Helps spot openings the player struggles with. Each row carries the game ids so you
    can dig into a specific loss with find_blunders.

    Args:
        username: The player's username.
        platform: "lichess" (default) or "chesscom".
        color: "white", "black" or "both" (default).
        count: How many recent games to look at (1-300).
        group_by_family: Group variations under their opening family (default true).
    """
    with tool_errors():
        services = get_services()
        resolved = normalize_platform(platform)
        color_key = color.strip().lower()
        if color_key not in {"white", "black", "both"}:
            raise ValueError("color must be 'white', 'black' or 'both'")
        count = max(1, min(int(count), 300))
        games = await services.recent_games(username, resolved, count)

    table: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"wins": 0, "draws": 0, "losses": 0, "games": 0, "game_ids": []}
    )
    considered = 0
    for game in games:
        summary = game.summary
        user_color = summary.color_of(username)
        outcome = summary.outcome_for(username)
        if user_color is None or outcome is None:
            continue
        if color_key != "both" and user_color != color_key:
            continue
        considered += 1
        name = (
            _opening_family(summary.opening)
            if group_by_family
            else (summary.opening or "Unknown opening")
        )
        row = table[(name, user_color)]
        row["games"] += 1
        row["game_ids"].append(summary.game_id)
        if outcome == "win":
            row["wins"] += 1
        elif outcome == "draw":
            row["draws"] += 1
        else:
            row["losses"] += 1

    rows = []
    for (name, user_color), row in table.items():
        score = (row["wins"] + 0.5 * row["draws"]) / row["games"]
        rows.append(
            {
                "opening": name,
                "color": user_color,
                "games": row["games"],
                "wins": row["wins"],
                "draws": row["draws"],
                "losses": row["losses"],
                "score_pct": round(100 * score, 1),
                "game_ids": row["game_ids"],
            }
        )
    rows.sort(key=lambda r: (-r["games"], r["score_pct"]))
    worst = [r for r in rows if r["games"] >= 3]
    worst.sort(key=lambda r: r["score_pct"])
    return {
        "username": username,
        "platform": resolved,
        "games_considered": considered,
        "openings": rows,
        "weakest_openings": worst[:3],
    }


def register(server: MCPServer) -> None:
    server.add_tool(get_opening_stats, annotations=READ_ONLY)

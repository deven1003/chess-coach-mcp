"""Resources exposing games and PGNs under the ``chess://`` scheme.

chess://games/{username}/recent             recent Lichess games (JSON)
chess://{platform}/games/{username}/recent  recent games on a platform (JSON)
chess://game/{game_id}/pgn                  PGN of a game (platform guessed from id)
chess://{platform}/game/{game_id}/pgn       PGN of a game on a platform
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from chess_coach_mcp.errors import resource_errors
from chess_coach_mcp.services import get_services, normalize_platform

RECENT_COUNT = 10


async def _recent_json(username: str, platform: str) -> str:
    with resource_errors():
        services = get_services()
        resolved = normalize_platform(platform)
        games = await services.recent_games(username, resolved, RECENT_COUNT)
    payload = [g.summary.to_dict(username) for g in games]
    return json.dumps(payload, indent=2)


async def _pgn(game_id: str, platform: str | None) -> str:
    with resource_errors():
        services = get_services()
        game = await services.get_game(game_id, platform)
    return game.pgn


def register(server: MCPServer) -> None:
    @server.resource(
        "chess://games/{username}/recent",
        name="recent_lichess_games",
        description="The player's 10 most recent Lichess games as JSON.",
        mime_type="application/json",
    )
    async def recent_games(username: str) -> str:
        return await _recent_json(username, "lichess")

    @server.resource(
        "chess://{platform}/games/{username}/recent",
        name="recent_games",
        description="The player's 10 most recent games on 'lichess' or 'chesscom' as JSON.",
        mime_type="application/json",
    )
    async def recent_games_on(platform: str, username: str) -> str:
        return await _recent_json(username, platform)

    @server.resource(
        "chess://game/{game_id}/pgn",
        name="game_pgn",
        description="Raw PGN of a game. Platform is guessed from the id "
        "(numeric = Chess.com, otherwise Lichess).",
        mime_type="application/x-chess-pgn",
    )
    async def game_pgn(game_id: str) -> str:
        return await _pgn(game_id, None)

    @server.resource(
        "chess://{platform}/game/{game_id}/pgn",
        name="game_pgn_on",
        description="Raw PGN of a game on 'lichess' or 'chesscom'.",
        mime_type="application/x-chess-pgn",
    )
    async def game_pgn_on(platform: str, game_id: str) -> str:
        return await _pgn(game_id, platform)

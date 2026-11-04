"""Builds the MCP server and runs it over stdio.

Everything here is wiring: tools, resources and prompts live in their own packages and
the chess logic lives in ``core``.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from chess_coach_mcp import __version__
from chess_coach_mcp.prompts import register_prompts
from chess_coach_mcp.resources import register_resources
from chess_coach_mcp.services import close_services
from chess_coach_mcp.tools import register_tools

INSTRUCTIONS = """\
Chess Coach gives you a player's online games together with Stockfish ground truth so
you can explain their mistakes in plain language.

Typical flow: get_recent_games -> analyze_game (or find_blunders) -> explain.
Pass the player's username to analyze_game / find_blunders so results are from their
point of view. Evaluations are centipawns from White's view; cp_loss is always from
the mover's view. The first analysis of a game takes 10-60 seconds; afterwards it is
cached. If engine_status reports the Lichess cloud fallback, analysis is partial.
"""


@asynccontextmanager
async def _lifespan(server: MCPServer) -> AsyncIterator[None]:
    try:
        yield None
    finally:
        await close_services()


def create_server() -> MCPServer:
    server = MCPServer(
        name="chess-coach",
        title="Chess Coach",
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=_lifespan,
    )
    register_tools(server)
    register_resources(server)
    register_prompts(server)
    return server


def main() -> None:
    # stdout is the MCP transport; all logging must go to stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    create_server().run("stdio")


if __name__ == "__main__":
    main()

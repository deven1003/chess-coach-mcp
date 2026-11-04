"""Tools: analyze_game, find_blunders, engine_status."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

from chess_coach_mcp.core import analysis
from chess_coach_mcp.core.models import GameReport, MoveEval
from chess_coach_mcp.errors import tool_errors
from chess_coach_mcp.services import get_services

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def _compact_move(move: MoveEval, *, with_fen: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "ply": move.ply,
        "move": f"{move.move_number}{'.' if move.color == 'white' else '...'} {move.san}",
        "color": move.color,
        "san": move.san,
        "eval_before": move.eval_before,
        "eval_after": move.eval_after,
        "cp_loss": move.cp_loss,
        "classification": move.classification,
        "best_move": move.best_move,
        "best_line": move.best_line,
    }
    if move.mate_before is not None:
        data["mate_before"] = move.mate_before
    if move.mate_after is not None:
        data["mate_after"] = move.mate_after
    if with_fen:
        data["fen_before"] = move.fen_before
    return data


def _report_to_dict(report: GameReport, username: str | None) -> dict[str, Any]:
    critical = sorted(
        (m for m in report.moves if m.classification in {"blunder", "mistake"}),
        key=lambda m: m.cp_loss or 0,
        reverse=True,
    )[:5]
    return {
        "game": report.game.to_dict(username),
        "engine": report.engine,
        "depth": report.depth,
        "refine_depth": report.refine_depth,
        "white": report.white.to_dict(),
        "black": report.black.to_dict(),
        "critical_moments": [_compact_move(m, with_fen=True) for m in critical],
        "moves": [
            _compact_move(m, with_fen=m.classification in {"inaccuracy", "mistake", "blunder"})
            for m in report.moves
        ],
        "notes": report.notes,
        "legend": {
            "eval": "centipawns from White's point of view, clamped to +/-1000; "
            "+100 = White is a pawn up",
            "cp_loss": "how much the mover's position got worse compared with the best move",
            "classification": "best / good / inaccuracy (>=50) / mistake (>=100) / "
            "blunder (>300) / unknown",
        },
    }


async def _get_report(
    game_id: str,
    platform: str | None,
    username: str | None,
    depth: int | None,
    ctx: Context | None,
) -> GameReport:
    with tool_errors():
        return await _compute_report(game_id, platform, username, depth, ctx)


async def _compute_report(
    game_id: str,
    platform: str | None,
    username: str | None,
    depth: int | None,
    ctx: Context | None,
) -> GameReport:
    services = get_services()
    settings = services.settings
    game = await services.get_game(game_id, platform, username)
    engine = await services.engine()
    use_depth = int(depth) if depth else settings.default_depth
    use_depth = max(6, min(use_depth, 30))

    cached = services.cache.get_report(game.platform, game.game_id, use_depth, engine.name)
    if cached is not None:
        return cached

    async def on_progress(done: int, total: int, message: str) -> None:
        if ctx is not None:
            await ctx.report_progress(done, total, message)

    report = await analysis.analyze_game(
        game,
        engine,
        depth=use_depth,
        refine_depth=max(settings.refine_depth, use_depth + 2),
        time_limit=settings.time_per_position or None,
        refine_time_limit=settings.refine_time_per_position or None,
        on_progress=on_progress,
    )
    services.cache.put_report(report)
    return report


async def analyze_game(
    game_id: str,
    platform: str | None = None,
    username: str | None = None,
    depth: int | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Run a full engine analysis of one game and classify every move.

    Returns evaluation before/after each move, centipawn loss, a label
    (best/good/inaccuracy/mistake/blunder), the engine's best move and line, per-player
    accuracy and error counts, and the five most critical moments with their FEN so you
    can explain *why* a move was bad. Results are cached, so repeat calls are instant.

    Args:
        game_id: Game id or URL from Lichess or Chess.com (as returned by get_recent_games).
        platform: "lichess" or "chesscom". Guessed from the id when omitted.
        username: The player to coach. Required for Chess.com games that were not
            fetched via get_recent_games; otherwise optional (adds user_color/user_result).
        depth: Engine search depth (default 14; 18+ is slower but more precise).
    """
    report = await _get_report(game_id, platform, username, depth, ctx)
    return _report_to_dict(report, username)


async def find_blunders(
    game_id: str,
    platform: str | None = None,
    username: str | None = None,
    threshold: int = 100,
    only_user: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Return only the moves that lost at least ``threshold`` centipawns, with the position
    (FEN), the engine's best move and the best line, sorted from worst to least bad.

    Cheaper to read than analyze_game when you only need the mistakes.

    Args:
        game_id: Game id or URL from Lichess or Chess.com.
        platform: "lichess" or "chesscom". Guessed from the id when omitted.
        username: The player to coach; required for uncached Chess.com games.
        threshold: Minimum centipawn loss to include (100 = mistakes and blunders).
        only_user: If true, return only the mistakes made by ``username``.
    """
    report = await _get_report(game_id, platform, username, None, ctx)
    user_color = report.game.color_of(username) if username else None
    moves = [
        m
        for m in report.moves
        if m.cp_loss is not None
        and m.cp_loss >= int(threshold)
        and (not only_user or user_color is None or m.color == user_color)
    ]
    moves.sort(key=lambda m: m.cp_loss or 0, reverse=True)
    return {
        "game": report.game.to_dict(username),
        "engine": report.engine,
        "depth": report.depth,
        "threshold": int(threshold),
        "count": len(moves),
        "blunders": [_compact_move(m, with_fen=True) for m in moves],
        "notes": report.notes,
    }


async def engine_status() -> dict[str, Any]:
    """Report which chess engine the server is using (local Stockfish or Lichess cloud
    fallback), where it lives, and cache statistics. Useful for troubleshooting."""
    services = get_services()
    with tool_errors():
        engine = await services.engine()
    settings = services.settings
    return {
        "engine": engine.name,
        "description": getattr(engine, "description", ""),
        "default_depth": settings.default_depth,
        "refine_depth": settings.refine_depth,
        "cache_dir": str(settings.cache_dir),
        "cache": services.cache.stats(),
        "note": services.engine_note or None,
    }


def register(server: MCPServer) -> None:
    server.add_tool(analyze_game, annotations=READ_ONLY)
    server.add_tool(find_blunders, annotations=READ_ONLY)
    server.add_tool(engine_status, annotations=ToolAnnotations(readOnlyHint=True))

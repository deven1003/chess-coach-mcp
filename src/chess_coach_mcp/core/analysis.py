"""Move-by-move analysis: evaluate every position, compute centipawn loss, classify moves.

This module is pure chess logic. It depends on an :class:`EngineBackend` but has no idea
that MCP exists.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable

import chess

from .engine import EngineBackend
from .models import (
    MATE_SCORE,
    Classification,
    Color,
    Game,
    GameReport,
    MoveEval,
    PlayerStats,
    PositionEval,
)

log = logging.getLogger(__name__)

#: Centipawn-loss thresholds (inclusive lower bounds, except blunder which is strictly greater).
INACCURACY_THRESHOLD = 50
MISTAKE_THRESHOLD = 100
BLUNDER_THRESHOLD = 300

#: Evaluations are clamped to this magnitude before computing loss, so that going from
#: +15 to +9 does not register as a 600 centipawn "blunder".
EVAL_CAP = 1000

#: How many moves of the engine's principal variation to keep per move.
PV_LENGTH = 6

ProgressFn = Callable[[int, int, str], Awaitable[None]]


def classify(cp_loss: int | None, *, is_best: bool = False) -> Classification:
    """Map a centipawn loss to a label.

    >>> classify(0, is_best=True), classify(49), classify(50), classify(100), classify(301)
    ('best', 'good', 'inaccuracy', 'mistake', 'blunder')
    """
    if cp_loss is None:
        return "unknown"
    if cp_loss > BLUNDER_THRESHOLD:
        return "blunder"
    if cp_loss >= MISTAKE_THRESHOLD:
        return "mistake"
    if cp_loss >= INACCURACY_THRESHOLD:
        return "inaccuracy"
    return "best" if is_best else "good"


def centipawn_loss(eval_before: int, eval_after: int, color: Color) -> int:
    """How much the mover's position got worse, from the mover's point of view (never < 0)."""
    sign = 1 if color == "white" else -1
    return max(0, sign * (eval_before - eval_after))


def move_accuracy(cp_loss: int) -> float:
    """Lichess-style accuracy for a single move (0-100)."""
    value = 103.1668 * math.exp(-0.04354 * cp_loss) - 3.1669
    return max(0.0, min(100.0, value))


def summarize(moves: list[MoveEval], color: Color) -> PlayerStats:
    own = [m for m in moves if m.color == color]
    analyzed = [m for m in own if m.cp_loss is not None]
    losses = [m.cp_loss for m in analyzed if m.cp_loss is not None]
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    accuracy = sum(move_accuracy(loss) for loss in losses) / len(losses) if losses else 0.0
    return PlayerStats(
        moves=len(own),
        analyzed_moves=len(analyzed),
        best=sum(1 for m in analyzed if m.classification == "best"),
        inaccuracies=sum(1 for m in analyzed if m.classification == "inaccuracy"),
        mistakes=sum(1 for m in analyzed if m.classification == "mistake"),
        blunders=sum(1 for m in analyzed if m.classification == "blunder"),
        average_cp_loss=round(avg_loss, 1),
        accuracy=round(accuracy, 1),
    )


def terminal_eval(board: chess.Board) -> PositionEval | None:
    """Evaluate a finished position without an engine. ``None`` if the game is not over."""
    if board.is_checkmate():
        cp = -MATE_SCORE if board.turn == chess.WHITE else MATE_SCORE
        return PositionEval(cp=cp, mate=0, best_move=None, pv=[], depth=0, source="terminal")
    if board.is_game_over(claim_draw=False):
        return PositionEval(cp=0, mate=None, best_move=None, pv=[], depth=0, source="terminal")
    return None


async def evaluate_position(
    engine: EngineBackend, fen: str, depth: int, time_limit: float | None = None
) -> PositionEval | None:
    board = chess.Board(fen)
    terminal = terminal_eval(board)
    if terminal is not None:
        return terminal
    return await engine.evaluate(fen, depth, time_limit)


def _san_line(board: chess.Board, pv: list[str], limit: int = PV_LENGTH) -> list[str]:
    line: list[str] = []
    scratch = board.copy()
    for uci in pv[:limit]:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in scratch.legal_moves:
            break
        line.append(scratch.san(move))
        scratch.push(move)
    return line


def build_move_eval(
    *,
    ply: int,
    fen_before: str,
    san: str,
    uci: str,
    before: PositionEval | None,
    after: PositionEval | None,
    depth: int,
) -> MoveEval:
    board = chess.Board(fen_before)
    color: Color = "white" if board.turn == chess.WHITE else "black"
    move_number = board.fullmove_number

    best_move_san: str | None = None
    best_line: list[str] = []
    is_best = False
    if before is not None and before.best_move:
        best_line = _san_line(board, before.pv)
        best_move_san = best_line[0] if best_line else None
        is_best = before.best_move == uci

    eval_before = before.score(EVAL_CAP) if before is not None else None
    eval_after = after.score(EVAL_CAP) if after is not None else None
    cp_loss: int | None
    if eval_before is None or eval_after is None:
        cp_loss = None
    elif is_best:
        cp_loss = 0
    else:
        cp_loss = centipawn_loss(eval_before, eval_after, color)

    return MoveEval(
        ply=ply,
        move_number=move_number,
        color=color,
        san=san,
        uci=uci,
        fen_before=fen_before,
        eval_before=eval_before,
        eval_after=eval_after,
        mate_before=before.mate if before is not None else None,
        mate_after=after.mate if after is not None else None,
        cp_loss=cp_loss,
        classification=classify(cp_loss, is_best=is_best),
        best_move=best_move_san,
        best_line=best_line,
        is_best=is_best,
        depth=depth,
    )


async def analyze_game(
    game: Game,
    engine: EngineBackend,
    *,
    depth: int = 14,
    refine_depth: int | None = 18,
    time_limit: float | None = 0.5,
    refine_time_limit: float | None = 2.0,
    on_progress: ProgressFn | None = None,
) -> GameReport:
    """Evaluate every position of ``game`` and classify every move.

    Two passes: a quick pass at ``depth`` over every position, then a deeper pass at
    ``refine_depth`` only around moves that looked suspicious, so that cheap false
    positives get corrected without paying the deep price for the whole game.

    ``time_limit`` / ``refine_time_limit`` cap the seconds spent per position in each pass
    (the engine stops at whichever of depth or time comes first), which keeps a long game
    to roughly a minute instead of several.
    """
    board = chess.Board()
    plies: list[tuple[str, str, str]] = []  # (fen_before, san, uci)
    for san in game.moves:
        try:
            move = board.parse_san(san)
        except ValueError as exc:
            raise ValueError(
                f"Illegal or unparseable move {san!r} at ply {len(plies) + 1}"
            ) from exc
        plies.append((board.fen(), board.san(move), move.uci()))
        board.push(move)
    fens = [fen for fen, _, _ in plies] + [board.fen()]

    evals: list[PositionEval | None] = []
    total = len(fens)
    for index, fen in enumerate(fens):
        evals.append(await evaluate_position(engine, fen, depth, time_limit))
        if on_progress is not None:
            await on_progress(index + 1, total, f"Evaluating position {index + 1}/{total}")

    moves = [
        build_move_eval(
            ply=i + 1,
            fen_before=fen,
            san=san,
            uci=uci,
            before=evals[i],
            after=evals[i + 1],
            depth=depth,
        )
        for i, (fen, san, uci) in enumerate(plies)
    ]

    used_refine: int | None = None
    if refine_depth is not None and refine_depth > depth:
        suspicious = [
            i
            for i, m in enumerate(moves)
            if m.cp_loss is not None and m.cp_loss >= INACCURACY_THRESHOLD
        ]
        if suspicious:
            used_refine = refine_depth
            needed = sorted({i for i in suspicious} | {i + 1 for i in suspicious})
            deep: dict[int, PositionEval | None] = {}
            for count, j in enumerate(needed, start=1):
                deep[j] = await evaluate_position(engine, fens[j], refine_depth, refine_time_limit)
                if on_progress is not None:
                    await on_progress(count, len(needed), f"Double-checking {count}/{len(needed)}")
            for i in suspicious:
                fen, san, uci = plies[i]
                moves[i] = build_move_eval(
                    ply=i + 1,
                    fen_before=fen,
                    san=san,
                    uci=uci,
                    before=deep.get(i) or evals[i],
                    after=deep.get(i + 1) or evals[i + 1],
                    depth=refine_depth,
                )

    notes: list[str] = []
    missing = sum(1 for e in evals if e is None)
    if missing:
        notes.append(
            f"{missing} of {total} positions could not be evaluated by {engine.name}; "
            "moves touching them are marked 'unknown'."
        )
    if engine.name == "lichess-cloud":
        notes.append(
            "Analysis used Lichess cloud evals because no local Stockfish was available. "
            "Install Stockfish (or let the server download it) for full coverage."
        )

    return GameReport(
        game=game.summary,
        moves=moves,
        white=summarize(moves, "white"),
        black=summarize(moves, "black"),
        engine=engine.name,
        depth=depth,
        refine_depth=used_refine,
        notes=notes,
    )

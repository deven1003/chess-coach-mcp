from __future__ import annotations

import chess
import pytest

from chess_coach_mcp.core import analysis
from chess_coach_mcp.core.models import MATE_SCORE, PositionEval

from .conftest import FakeEngine


@pytest.mark.parametrize(
    ("cp_loss", "is_best", "expected"),
    [
        (0, True, "best"),
        (0, False, "good"),
        (49, False, "good"),
        (50, False, "inaccuracy"),
        (99, False, "inaccuracy"),
        (100, False, "mistake"),
        (300, False, "mistake"),
        (301, False, "blunder"),
        (5000, False, "blunder"),
        (None, False, "unknown"),
    ],
)
def test_classify_thresholds(cp_loss, is_best, expected):
    assert analysis.classify(cp_loss, is_best=is_best) == expected


def test_centipawn_loss_is_from_movers_point_of_view():
    # White goes from +50 to -150: lost 200.
    assert analysis.centipawn_loss(50, -150, "white") == 200
    # Black goes from -150 (good for Black) to +50: lost 200.
    assert analysis.centipawn_loss(-150, 50, "black") == 200
    # Improving never counts as a loss.
    assert analysis.centipawn_loss(0, 300, "white") == 0
    assert analysis.centipawn_loss(0, -300, "black") == 0


def test_eval_is_clamped_so_winning_more_slowly_is_not_a_blunder():
    before = PositionEval(cp=1500, mate=None, best_move=None, pv=[], depth=1, source="t")
    after = PositionEval(cp=900, mate=None, best_move=None, pv=[], depth=1, source="t")
    assert before.score() == 1000
    assert analysis.centipawn_loss(before.score(), after.score(), "white") == 100


def test_mate_scores_map_to_cap():
    assert PositionEval(cp=None, mate=3, best_move=None, pv=[], depth=1, source="t").score() == 1000
    assert (
        PositionEval(cp=None, mate=-1, best_move=None, pv=[], depth=1, source="t").score() == -1000
    )
    mated = PositionEval(cp=-MATE_SCORE, mate=0, best_move=None, pv=[], depth=0, source="terminal")
    assert mated.score() == -1000


def test_move_accuracy_formula():
    assert analysis.move_accuracy(0) == pytest.approx(100.0, abs=0.01)
    assert 0 < analysis.move_accuracy(20) < analysis.move_accuracy(10) < 100
    assert analysis.move_accuracy(1000) == 0.0


def test_terminal_eval():
    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    result = analysis.terminal_eval(board)
    assert result is not None and result.mate == 0 and result.score() == -1000
    assert analysis.terminal_eval(chess.Board()) is None


async def test_analyze_game_flags_the_losing_move(sample_game):
    engine = FakeEngine()
    report = await analysis.analyze_game(sample_game, engine, depth=10, refine_depth=12)

    assert [m.san for m in report.moves] == ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]
    nf6 = report.moves[5]
    assert nf6.color == "black" and nf6.move_number == 3
    assert nf6.classification == "blunder"
    assert nf6.cp_loss == 1000
    assert nf6.mate_after == 1
    assert nf6.fen_before.startswith("r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b")

    mate = report.moves[6]
    assert mate.classification == "best"
    assert mate.is_best and mate.best_move == "Qxf7#"
    assert mate.cp_loss == 0

    assert report.black.blunders == 1
    assert report.black.moves == 3
    assert report.white.blunders == 0
    assert report.white.accuracy == 100.0
    assert report.engine == "fake"
    assert report.depth == 10
    assert report.refine_depth == 12  # the blunder triggered a deeper pass
    assert report.notes == []


async def test_refine_pass_only_touches_suspicious_positions(sample_game):
    engine = FakeEngine()
    await analysis.analyze_game(sample_game, engine, depth=10, refine_depth=12)
    shallow = [fen for fen, depth in engine.calls if depth == 10]
    deep = [fen for fen, depth in engine.calls if depth == 12]
    # 8 positions, but the final checkmate is terminal and never sent to the engine.
    assert len(shallow) == 7
    # Only the position before and after the blunder are re-evaluated.
    assert len(deep) == 2


async def test_refine_can_be_disabled(sample_game):
    engine = FakeEngine()
    report = await analysis.analyze_game(sample_game, engine, depth=10, refine_depth=None)
    assert all(depth == 10 for _, depth in engine.calls)
    assert report.refine_depth is None


async def test_unknown_positions_are_reported(sample_game):
    start_fen = chess.Board().fen()
    engine = FakeEngine(overrides={start_fen: None})
    report = await analysis.analyze_game(sample_game, engine, depth=10, refine_depth=None)
    assert report.moves[0].classification == "unknown"
    assert report.moves[0].cp_loss is None
    assert report.moves[1].classification != "unknown"
    assert report.white.analyzed_moves == 3
    assert report.notes and "1 of 8 positions" in report.notes[0]


async def test_progress_callback(sample_game):
    seen: list[tuple[int, int]] = []

    async def on_progress(done: int, total: int, message: str) -> None:
        seen.append((done, total))

    await analysis.analyze_game(
        sample_game, FakeEngine(), depth=10, refine_depth=None, on_progress=on_progress
    )
    assert seen[0] == (1, 8)
    assert seen[-1] == (8, 8)


async def test_illegal_move_raises(sample_game):
    sample_game.moves[2] = "Qh5"  # bishop is still on f1; the queen cannot reach h5 via... it can
    sample_game.moves[3] = "Ke2"  # black has no king on e1: illegal
    with pytest.raises(ValueError, match="ply 4"):
        await analysis.analyze_game(sample_game, FakeEngine(), depth=10, refine_depth=None)


def test_report_round_trips_through_dict(sample_game):
    import asyncio

    report = asyncio.run(analysis.analyze_game(sample_game, FakeEngine(), depth=10))
    from chess_coach_mcp.core.models import GameReport

    clone = GameReport.from_dict(report.to_dict())
    assert clone.to_dict() == report.to_dict()

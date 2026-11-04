"""Shared fixtures: a deterministic fake engine and a Services instance with no network."""

from __future__ import annotations

from pathlib import Path

import chess
import httpx
import pytest
import respx

from chess_coach_mcp.clients import ChessComClient, LichessClient
from chess_coach_mcp.config import Settings
from chess_coach_mcp.core.cache import Cache
from chess_coach_mcp.core.models import Game, GameSummary, Player, PositionEval
from chess_coach_mcp.core.pgn import parse_pgn
from chess_coach_mcp.services import Services, set_services

FIXTURES = Path(__file__).parent / "fixtures"

PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 300,
    chess.BISHOP: 300,
    chess.ROOK: 500,
    chess.QUEEN: 900,
}


def material(board: chess.Board) -> int:
    total = 0
    for piece_type, value in PIECE_VALUES.items():
        total += value * len(board.pieces(piece_type, chess.WHITE))
        total -= value * len(board.pieces(piece_type, chess.BLACK))
    return total


class FakeEngine:
    """Material counter with one-ply mate detection. Deterministic and instant.

    ``overrides`` maps FEN -> PositionEval (or None to simulate a cloud miss).
    Every call is recorded in ``calls`` as ``(fen, depth)``.
    """

    name = "fake"
    description = "fake engine for tests"

    def __init__(self, overrides: dict[str, PositionEval | None] | None = None):
        self.overrides = overrides or {}
        self.calls: list[tuple[str, int]] = []
        self.closed = False

    async def evaluate(
        self, fen: str, depth: int, time_limit: float | None = None
    ) -> PositionEval | None:
        self.calls.append((fen, depth))
        if fen in self.overrides:
            return self.overrides[fen]
        board = chess.Board(fen)
        legal = list(board.legal_moves)
        for move in legal:
            board.push(move)
            mate = board.is_checkmate()
            board.pop()
            if mate:
                return PositionEval(
                    cp=None,
                    mate=1 if board.turn == chess.WHITE else -1,
                    best_move=move.uci(),
                    pv=[move.uci()],
                    depth=depth,
                    source=self.name,
                )
        best = legal[0].uci() if legal else None
        return PositionEval(
            cp=material(board),
            mate=None,
            best_move=best,
            pv=[best] if best else [],
            depth=depth,
            source=self.name,
        )

    async def close(self) -> None:
        self.closed = True


def load_sample_game() -> Game:
    pgn = (FIXTURES / "sample_game.pgn").read_text()
    headers, moves = parse_pgn(pgn)
    summary = GameSummary(
        game_id="abcdefgh",
        platform="lichess",
        white=Player(headers["White"], 1500),
        black=Player(headers["Black"], 1400),
        result=headers["Result"],
        winner="white",
        date="2026-11-01T12:00:00+00:00",
        opening=headers["Opening"],
        eco=headers["ECO"],
        time_control="300+0",
        speed="blitz",
        url=headers["Site"],
        termination="mate",
        rated=False,
    )
    return Game(summary=summary, pgn=pgn, moves=moves)


@pytest.fixture
def sample_game() -> Game:
    return load_sample_game()


@pytest.fixture
def fake_engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture(autouse=True)
def no_real_network():
    """Every HTTP call must be mocked with ``respx.get(...)``; anything else raises."""
    with respx.mock:
        yield
        respx.mock.reset()


@pytest.fixture
def services(tmp_path: Path, fake_engine: FakeEngine) -> Services:
    """A Services instance with an in-memory cache, a fake engine and no real network."""
    settings = Settings(
        cache_dir=tmp_path,
        auto_download_engine=False,
        default_depth=10,
        refine_depth=12,
    )
    http = httpx.AsyncClient()
    svc = Services(
        settings=settings,
        cache=Cache(None),
        http=http,
        lichess=LichessClient(http),
        chesscom=ChessComClient(http),
        engine_override=fake_engine,
    )
    set_services(svc)
    yield svc
    set_services(None)

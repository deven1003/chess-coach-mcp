"""Plain dataclasses shared by the whole project.

Evaluations are always stored from White's point of view, in centipawns.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Platform = Literal["lichess", "chesscom"]
Color = Literal["white", "black"]
Outcome = Literal["win", "loss", "draw"]
Classification = Literal["best", "good", "inaccuracy", "mistake", "blunder", "unknown"]

#: Score assigned to a position where the side to move is checkmated (before clamping).
MATE_SCORE = 100_000


@dataclass(slots=True)
class Player:
    name: str
    rating: int | None = None


@dataclass(slots=True)
class GameSummary:
    """Metadata about one game, without the moves."""

    game_id: str
    platform: Platform
    white: Player
    black: Player
    result: str  # "1-0", "0-1", "1/2-1/2" or "*"
    winner: Literal["white", "black"] | None
    date: str | None  # ISO 8601, UTC
    opening: str | None
    eco: str | None
    time_control: str | None
    speed: str | None  # bullet / blitz / rapid / classical / correspondence
    url: str | None
    termination: str | None = None
    rated: bool | None = None

    def color_of(self, username: str) -> Color | None:
        name = username.lower()
        if self.white.name.lower() == name:
            return "white"
        if self.black.name.lower() == name:
            return "black"
        return None

    def outcome_for(self, username: str) -> Outcome | None:
        color = self.color_of(username)
        if color is None or self.result == "*":
            return None
        if self.winner is None:
            return "draw"
        return "win" if self.winner == color else "loss"

    def opponent_of(self, username: str) -> Player | None:
        color = self.color_of(username)
        if color == "white":
            return self.black
        if color == "black":
            return self.white
        return None

    def to_dict(self, username: str | None = None) -> dict[str, Any]:
        data = asdict(self)
        if username:
            opponent = self.opponent_of(username)
            data["user_color"] = self.color_of(username)
            data["user_result"] = self.outcome_for(username)
            data["opponent"] = asdict(opponent) if opponent else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameSummary:
        return cls(
            game_id=data["game_id"],
            platform=data["platform"],
            white=Player(**data["white"]),
            black=Player(**data["black"]),
            result=data["result"],
            winner=data.get("winner"),
            date=data.get("date"),
            opening=data.get("opening"),
            eco=data.get("eco"),
            time_control=data.get("time_control"),
            speed=data.get("speed"),
            url=data.get("url"),
            termination=data.get("termination"),
            rated=data.get("rated"),
        )


@dataclass(slots=True)
class Game:
    """A full game: metadata, the raw PGN and the move list in SAN."""

    summary: GameSummary
    pgn: str
    moves: list[str]

    @property
    def game_id(self) -> str:
        return self.summary.game_id

    @property
    def platform(self) -> Platform:
        return self.summary.platform

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary.to_dict(), "pgn": self.pgn, "moves": list(self.moves)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Game:
        return cls(
            summary=GameSummary.from_dict(data["summary"]),
            pgn=data["pgn"],
            moves=list(data["moves"]),
        )


@dataclass(slots=True)
class PositionEval:
    """Engine verdict on one position. ``cp`` and ``mate`` are from White's point of view."""

    cp: int | None
    mate: int | None
    best_move: str | None  # UCI
    pv: list[str]  # UCI moves
    depth: int
    source: str  # "stockfish", "lichess-cloud", "terminal", ...

    def score(self, cap: int = 1000) -> int:
        """Centipawn score from White's point of view, with mates mapped to +/-cap."""
        if self.mate is not None and self.mate != 0:
            return cap if self.mate > 0 else -cap
        value = self.cp if self.cp is not None else 0
        return max(-cap, min(cap, value))


@dataclass(slots=True)
class MoveEval:
    """One played move with the engine's judgement of it."""

    ply: int  # 1-based half-move index
    move_number: int
    color: Color
    san: str
    uci: str
    fen_before: str
    eval_before: int | None  # clamped cp, White POV, before the move
    eval_after: int | None  # clamped cp, White POV, after the move
    mate_before: int | None
    mate_after: int | None
    cp_loss: int | None
    classification: Classification
    best_move: str | None  # SAN
    best_line: list[str]  # SAN
    is_best: bool
    depth: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MoveEval:
        return cls(**data)


@dataclass(slots=True)
class PlayerStats:
    moves: int
    analyzed_moves: int
    best: int
    inaccuracies: int
    mistakes: int
    blunders: int
    average_cp_loss: float
    accuracy: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayerStats:
        return cls(**data)


@dataclass(slots=True)
class GameReport:
    """Full analysis of one game."""

    game: GameSummary
    moves: list[MoveEval]
    white: PlayerStats
    black: PlayerStats
    engine: str
    depth: int
    refine_depth: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "game": self.game.to_dict(),
            "moves": [m.to_dict() for m in self.moves],
            "white": self.white.to_dict(),
            "black": self.black.to_dict(),
            "engine": self.engine,
            "depth": self.depth,
            "refine_depth": self.refine_depth,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameReport:
        return cls(
            game=GameSummary.from_dict(data["game"]),
            moves=[MoveEval.from_dict(m) for m in data["moves"]],
            white=PlayerStats.from_dict(data["white"]),
            black=PlayerStats.from_dict(data["black"]),
            engine=data["engine"],
            depth=data["depth"],
            refine_depth=data.get("refine_depth"),
            notes=list(data.get("notes", [])),
        )

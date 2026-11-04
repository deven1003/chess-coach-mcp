"""SQLite cache for fetched games and finished analyses.

Engine analysis is the expensive part (tens of seconds per game), so every report is
stored keyed by ``(platform, game_id, depth, engine)`` and reused on later calls.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from .models import Game, GameReport, Platform

_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    platform    TEXT NOT NULL,
    game_id     TEXT NOT NULL,
    white       TEXT NOT NULL,
    black       TEXT NOT NULL,
    played_at   TEXT,
    fetched_at  TEXT NOT NULL,
    data        TEXT NOT NULL,
    PRIMARY KEY (platform, game_id)
);
CREATE INDEX IF NOT EXISTS games_white ON games (platform, white, played_at);
CREATE INDEX IF NOT EXISTS games_black ON games (platform, black, played_at);

CREATE TABLE IF NOT EXISTS reports (
    platform    TEXT NOT NULL,
    game_id     TEXT NOT NULL,
    depth       INTEGER NOT NULL,
    engine      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    data        TEXT NOT NULL,
    PRIMARY KEY (platform, game_id, depth, engine)
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Cache:
    """Thread-safe wrapper around one SQLite file (or an in-memory database)."""

    def __init__(self, path: Path | str | None = None):
        if path is None:
            target = ":memory:"
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            target = str(path)
        self.path = target
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if target != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    # ---------------------------------------------------------------- games

    def get_game(self, platform: Platform, game_id: str) -> Game | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM games WHERE platform = ? AND game_id = ?", (platform, game_id)
            ).fetchone()
        return Game.from_dict(json.loads(row["data"])) if row else None

    def find_game(self, game_id: str) -> Game | None:
        """Look a game up by id on any platform (most recently fetched wins)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM games WHERE game_id = ? ORDER BY fetched_at DESC LIMIT 1",
                (game_id,),
            ).fetchone()
        return Game.from_dict(json.loads(row["data"])) if row else None

    def put_game(self, game: Game) -> None:
        self.put_games([game])

    def put_games(self, games: list[Game]) -> None:
        rows = [
            (
                g.platform,
                g.game_id,
                g.summary.white.name.lower(),
                g.summary.black.name.lower(),
                g.summary.date,
                _now(),
                json.dumps(g.to_dict()),
            )
            for g in games
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO games "
                "(platform, game_id, white, black, played_at, fetched_at, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()

    def games_for_user(self, platform: Platform, username: str, limit: int = 50) -> list[Game]:
        name = username.lower()
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM games WHERE platform = ? AND (white = ? OR black = ?) "
                "ORDER BY played_at DESC LIMIT ?",
                (platform, name, name, limit),
            ).fetchall()
        return [Game.from_dict(json.loads(r["data"])) for r in rows]

    # -------------------------------------------------------------- reports

    def get_report(
        self, platform: Platform, game_id: str, depth: int, engine: str
    ) -> GameReport | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM reports WHERE platform = ? AND game_id = ? "
                "AND depth = ? AND engine = ?",
                (platform, game_id, depth, engine),
            ).fetchone()
        return GameReport.from_dict(json.loads(row["data"])) if row else None

    def put_report(self, report: GameReport) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO reports "
                "(platform, game_id, depth, engine, created_at, data) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    report.game.platform,
                    report.game.game_id,
                    report.depth,
                    report.engine,
                    _now(),
                    json.dumps(report.to_dict()),
                ),
            )
            self._conn.commit()

    def stats(self) -> dict[str, int]:
        with self._lock:
            games = self._conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
            reports = self._conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        return {"games": games, "reports": reports}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

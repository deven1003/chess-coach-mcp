"""Lichess API client: user games (NDJSON), single game export, cloud evaluation.

API docs: https://lichess.org/api
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from chess_coach_mcp.core.models import Game, GameSummary, Player, PositionEval
from chess_coach_mcp.core.pgn import moves_to_pgn, parse_pgn

log = logging.getLogger(__name__)

LICHESS_URL = "https://lichess.org"


class LichessError(RuntimeError):
    """A request to Lichess failed in a way worth telling the user about."""


def _player(data: dict[str, Any]) -> Player:
    user = data.get("user") or {}
    name = user.get("name") or data.get("name")
    if not name and "aiLevel" in data:
        name = f"Stockfish level {data['aiLevel']}"
    return Player(name=name or "Anonymous", rating=data.get("rating"))


def _timestamp(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat(timespec="seconds")


def parse_lichess_game(data: dict[str, Any]) -> Game | None:
    """Convert one Lichess game JSON object into a :class:`Game`.

    Returns ``None`` for variants other than standard chess.
    """
    if data.get("variant", "standard") != "standard":
        return None

    players = data.get("players") or {}
    white = _player(players.get("white") or {})
    black = _player(players.get("black") or {})

    status = data.get("status", "")
    winner = data.get("winner")
    if winner == "white":
        result = "1-0"
    elif winner == "black":
        result = "0-1"
    elif status in {"created", "started"}:
        result = "*"
    else:
        result = "1/2-1/2"

    clock = data.get("clock")
    if clock:
        time_control = f"{clock.get('initial', 0)}+{clock.get('increment', 0)}"
    elif data.get("daysPerTurn"):
        time_control = f"{data['daysPerTurn']} days/move"
    else:
        time_control = None

    opening = data.get("opening") or {}
    game_id = str(data["id"])
    summary = GameSummary(
        game_id=game_id,
        platform="lichess",
        white=white,
        black=black,
        result=result,
        winner=winner if winner in {"white", "black"} else None,
        date=_timestamp(data.get("createdAt")),
        opening=opening.get("name"),
        eco=opening.get("eco"),
        time_control=time_control,
        speed=data.get("speed"),
        url=f"{LICHESS_URL}/{game_id}",
        termination=status or None,
        rated=data.get("rated"),
    )

    moves_field = data.get("moves")
    pgn = data.get("pgn")
    if moves_field:
        moves = moves_field.split()
    elif pgn:
        _, moves = parse_pgn(pgn)
    else:
        moves = []
    if not pgn:
        headers = {
            "Event": f"Lichess {summary.speed or 'game'}",
            "Site": summary.url or "",
            "White": white.name,
            "Black": black.name,
            "Result": result,
        }
        pgn = moves_to_pgn(headers, moves)
    return Game(summary=summary, pgn=pgn, moves=moves)


class LichessClient:
    def __init__(self, http: httpx.AsyncClient, token: str | None = None):
        self._http = http
        self._token = token

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {"Accept": accept}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @staticmethod
    def _check(response: httpx.Response, what: str) -> None:
        if response.status_code == 404:
            raise LichessError(f"{what} not found on Lichess")
        if response.status_code == 429:
            raise LichessError("Lichess rate limit reached; wait a minute and try again")
        if response.status_code >= 400:
            raise LichessError(f"Lichess returned HTTP {response.status_code} for {what}")

    async def get_recent_games(
        self,
        username: str,
        count: int = 10,
        *,
        since_ms: int | None = None,
        until_ms: int | None = None,
    ) -> list[Game]:
        params: dict[str, Any] = {
            "max": max(1, min(count, 300)),
            "pgnInJson": "true",
            "opening": "true",
            "moves": "true",
            "clocks": "false",
            "evals": "false",
        }
        if since_ms is not None:
            params["since"] = since_ms
        if until_ms is not None:
            params["until"] = until_ms
        response = await self._http.get(
            f"{LICHESS_URL}/api/games/user/{username}",
            params=params,
            headers=self._headers("application/x-ndjson"),
        )
        self._check(response, f"user '{username}'")
        games: list[Game] = []
        for line in response.text.splitlines():
            line = line.strip()
            if not line:
                continue
            game = parse_lichess_game(json.loads(line))
            if game is not None:
                games.append(game)
        return games

    async def get_game(self, game_id: str) -> Game:
        response = await self._http.get(
            f"{LICHESS_URL}/game/export/{game_id}",
            params={"pgnInJson": "true", "opening": "true", "moves": "true"},
            headers=self._headers("application/json"),
        )
        self._check(response, f"game '{game_id}'")
        game = parse_lichess_game(response.json())
        if game is None:
            raise LichessError(f"Game '{game_id}' is not a standard chess game")
        return game

    async def cloud_eval(self, fen: str, multi_pv: int = 1) -> PositionEval | None:
        """Return the cloud evaluation of ``fen`` (White's point of view) or ``None``."""
        response = await self._http.get(
            f"{LICHESS_URL}/api/cloud-eval",
            params={"fen": fen, "multiPv": multi_pv},
            headers=self._headers("application/json"),
        )
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            log.warning("Lichess cloud-eval rate limit hit")
            return None
        if response.status_code >= 400:
            return None
        data = response.json()
        pvs = data.get("pvs") or []
        if not pvs:
            return None
        first = pvs[0]
        pv = str(first.get("moves", "")).split()
        return PositionEval(
            cp=first.get("cp"),
            mate=first.get("mate"),
            best_move=pv[0] if pv else None,
            pv=pv,
            depth=int(data.get("depth", 0)),
            source="lichess-cloud",
        )

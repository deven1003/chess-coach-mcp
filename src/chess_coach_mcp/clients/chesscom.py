"""Chess.com published-data API client.

Chess.com exposes games only as monthly archives per player, so fetching "recent games"
means walking archives from newest to oldest. There is no endpoint for a single game by
id; :meth:`ChessComClient.find_game` scans a player's recent archives instead.

API docs: https://www.chess.com/news/view/published-data-api
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from chess_coach_mcp.core.models import Game, GameSummary, Player
from chess_coach_mcp.core.pgn import parse_pgn

log = logging.getLogger(__name__)

CHESSCOM_API = "https://api.chess.com/pub"

_DRAW_RESULTS = {
    "agreed",
    "repetition",
    "stalemate",
    "insufficient",
    "50move",
    "timevsinsufficient",
}
_MOVE_TOKEN = re.compile(
    r"^(\d+\.{1,3})?([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](=[QRBN])?[+#]?|O-O(-O)?[+#]?)$"
)


class ChessComError(RuntimeError):
    """A request to Chess.com failed in a way worth telling the user about."""


def opening_name_from_url(url: str | None) -> str | None:
    """Turn ``https://www.chess.com/openings/Sicilian-Defense-Najdorf-6.Bg5-e6`` into
    ``Sicilian Defense Najdorf``: the trailing move list is dropped."""
    if not url:
        return None
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    words: list[str] = []
    for token in slug.split("-"):
        if _MOVE_TOKEN.match(token):
            break
        words.append(token)
    return " ".join(words) or None


def parse_chesscom_game(data: dict[str, Any]) -> Game | None:
    """Convert one archive entry into a :class:`Game`; ``None`` for variants."""
    if data.get("rules", "chess") != "chess":
        return None
    pgn = data.get("pgn")
    if not pgn:
        return None
    headers, moves = parse_pgn(pgn)

    url = str(data.get("url", ""))
    game_id = url.rstrip("/").rsplit("/", 1)[-1] or headers.get("Link", "")
    white_data = data.get("white") or {}
    black_data = data.get("black") or {}
    white = Player(name=white_data.get("username", "?"), rating=white_data.get("rating"))
    black = Player(name=black_data.get("username", "?"), rating=black_data.get("rating"))

    white_result = white_data.get("result", "")
    black_result = black_data.get("result", "")
    if white_result == "win":
        winner: str | None = "white"
        result = "1-0"
    elif black_result == "win":
        winner = "black"
        result = "0-1"
    elif white_result in _DRAW_RESULTS or black_result in _DRAW_RESULTS:
        winner = None
        result = "1/2-1/2"
    else:
        winner = None
        result = headers.get("Result", "*")

    end_time = data.get("end_time")
    date = (
        datetime.fromtimestamp(end_time, tz=UTC).isoformat(timespec="seconds") if end_time else None
    )
    eco_url = data.get("eco") or headers.get("ECOUrl")
    summary = GameSummary(
        game_id=game_id,
        platform="chesscom",
        white=white,
        black=black,
        result=result,
        winner=winner,  # type: ignore[arg-type]
        date=date,
        opening=opening_name_from_url(eco_url),
        eco=headers.get("ECO"),
        time_control=data.get("time_control"),
        speed=data.get("time_class"),
        url=url or None,
        termination=headers.get("Termination") or white_result or None,
        rated=data.get("rated"),
    )
    return Game(summary=summary, pgn=pgn, moves=moves)


class ChessComClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def _get_json(self, url: str, what: str) -> dict[str, Any]:
        response = await self._http.get(url, headers={"Accept": "application/json"})
        if response.status_code == 404:
            raise ChessComError(f"{what} not found on Chess.com")
        if response.status_code == 429:
            raise ChessComError("Chess.com rate limit reached; wait a moment and try again")
        if response.status_code >= 400:
            raise ChessComError(f"Chess.com returned HTTP {response.status_code} for {what}")
        return response.json()

    async def get_archives(self, username: str) -> list[str]:
        data = await self._get_json(
            f"{CHESSCOM_API}/player/{username.lower()}/games/archives", f"user '{username}'"
        )
        return list(data.get("archives", []))

    async def get_archive(self, archive_url: str) -> list[Game]:
        data = await self._get_json(archive_url, "monthly archive")
        games: list[Game] = []
        for entry in data.get("games", []):
            try:
                game = parse_chesscom_game(entry)
            except ValueError:
                log.debug("Skipping unparseable Chess.com game %s", entry.get("url"))
                continue
            if game is not None:
                games.append(game)
        return games

    async def get_recent_games(
        self, username: str, count: int = 10, *, max_months: int = 12
    ) -> list[Game]:
        archives = await self.get_archives(username)
        collected: list[Game] = []
        for archive_url in reversed(archives[-max_months:]):
            month = await self.get_archive(archive_url)
            collected.extend(reversed(month))  # archives are oldest-first
            if len(collected) >= count:
                break
        collected.sort(key=lambda g: g.summary.date or "", reverse=True)
        return collected[:count]

    async def find_game(self, username: str, game_id: str, *, max_months: int = 12) -> Game | None:
        archives = await self.get_archives(username)
        for archive_url in reversed(archives[-max_months:]):
            for game in await self.get_archive(archive_url):
                if game.game_id == game_id:
                    return game
        return None

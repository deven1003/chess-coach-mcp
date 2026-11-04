"""Shared runtime objects (cache, HTTP clients, engine) used by tools, resources and prompts.

A single :class:`Services` instance lives for the lifetime of the server. Tests swap it
with :func:`set_services` to inject fakes.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

import httpx

from chess_coach_mcp.clients import ChessComClient, LichessClient
from chess_coach_mcp.config import Settings
from chess_coach_mcp.core.cache import Cache
from chess_coach_mcp.core.engine import (
    EngineBackend,
    EngineError,
    LichessCloudEngine,
    StockfishEngine,
    download_stockfish,
    find_stockfish,
)
from chess_coach_mcp.core.models import Game, Platform

log = logging.getLogger(__name__)

_LICHESS_URL = re.compile(r"lichess\.org/([A-Za-z0-9]{8})")
_CHESSCOM_URL = re.compile(r"chess\.com/(?:game|live|daily)/(?:live/|daily/)?(\d+)")


def normalize_game_id(raw: str) -> str:
    """Accept a bare id or a full game URL from either site and return the bare id."""
    text = raw.strip()
    match = _LICHESS_URL.search(text)
    if match:
        return match.group(1)
    match = _CHESSCOM_URL.search(text)
    if match:
        return match.group(1)
    return text.rstrip("/").rsplit("/", 1)[-1] if "/" in text else text


def detect_platform(game_id: str) -> Platform:
    """Chess.com ids are numeric; Lichess ids are 8 alphanumeric characters."""
    return "chesscom" if game_id.isdigit() else "lichess"


def normalize_platform(platform: str | None) -> Platform:
    if platform is None:
        return "lichess"
    key = platform.strip().lower().replace(".", "").replace("_", "").replace("-", "")
    if key in {"lichess", "li"}:
        return "lichess"
    if key in {"chesscom", "chess", "cc"}:
        return "chesscom"
    raise ValueError(f"Unknown platform {platform!r}; use 'lichess' or 'chesscom'")


@dataclass
class Services:
    settings: Settings
    cache: Cache
    http: httpx.AsyncClient
    lichess: LichessClient
    chesscom: ChessComClient
    engine_override: EngineBackend | None = None
    engine_note: str = ""
    _engine: EngineBackend | None = field(default=None, init=False, repr=False)
    _engine_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @classmethod
    def create(cls, settings: Settings | None = None) -> Services:
        settings = settings or Settings.from_env()
        http = httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )
        return cls(
            settings=settings,
            cache=Cache(settings.cache_dir / "cache.db"),
            http=http,
            lichess=LichessClient(http, token=settings.lichess_token),
            chesscom=ChessComClient(http),
        )

    # ---------------------------------------------------------------- engine

    async def engine(self) -> EngineBackend:
        """Return the engine backend, starting (and if needed downloading) Stockfish once."""
        if self.engine_override is not None:
            return self.engine_override
        if self._engine is not None:
            return self._engine
        async with self._engine_lock:
            if self._engine is None:
                self._engine = await self._start_engine()
        return self._engine

    async def _start_engine(self) -> EngineBackend:
        settings = self.settings
        path = find_stockfish(settings.stockfish_path, settings.cache_dir)
        if path is None and settings.auto_download_engine:
            try:
                path = await download_stockfish(self.http, settings.cache_dir)
            except (EngineError, httpx.HTTPError, OSError) as exc:
                log.warning("Automatic Stockfish download failed: %s", exc)
                self.engine_note = f"Stockfish download failed: {exc}"
        if path is not None:
            try:
                engine = await asyncio.to_thread(
                    StockfishEngine,
                    path,
                    threads=settings.engine_threads,
                    hash_mb=settings.engine_hash_mb,
                )
                log.info("Using %s", engine.description)
                return engine
            except EngineError as exc:
                log.warning("%s", exc)
                self.engine_note = str(exc)
        log.warning("No local Stockfish; falling back to Lichess cloud evaluations")
        return LichessCloudEngine(self.lichess.cloud_eval)

    # ----------------------------------------------------------------- games

    async def recent_games(self, username: str, platform: Platform, count: int) -> list[Game]:
        if platform == "lichess":
            games = await self.lichess.get_recent_games(username, count)
        else:
            games = await self.chesscom.get_recent_games(username, count)
        if games:
            self.cache.put_games(games)
        return games

    async def get_game(
        self, game_id: str, platform: str | None = None, username: str | None = None
    ) -> Game:
        """Resolve a game by id: cache first, then the platform API."""
        game_id = normalize_game_id(game_id)
        resolved: Platform = normalize_platform(platform) if platform else detect_platform(game_id)

        cached = self.cache.get_game(resolved, game_id) or self.cache.find_game(game_id)
        if cached is not None:
            return cached

        if resolved == "lichess":
            game = await self.lichess.get_game(game_id)
        else:
            if not username:
                raise ValueError(
                    "Chess.com has no single-game endpoint. Pass the player's username, "
                    "or call get_recent_games first so the game is cached."
                )
            found = await self.chesscom.find_game(username, game_id)
            if found is None:
                raise ValueError(
                    f"Game {game_id} was not found in the last 12 months of {username}'s "
                    "Chess.com archives"
                )
            game = found
        self.cache.put_game(game)
        return game

    async def close(self) -> None:
        if self._engine is not None:
            try:
                await self._engine.close()
            except Exception:  # noqa: BLE001 - best effort shutdown
                log.debug("Engine shutdown raised", exc_info=True)
            self._engine = None
        await self.http.aclose()
        self.cache.close()


_services: Services | None = None


def get_services() -> Services:
    global _services
    if _services is None:
        _services = Services.create()
    return _services


def set_services(services: Services | None) -> None:
    global _services
    _services = services


async def close_services() -> None:
    global _services
    if _services is not None:
        await _services.close()
        _services = None

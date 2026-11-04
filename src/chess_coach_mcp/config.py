"""Runtime configuration, read from environment variables.

Every setting has a sensible default so the server runs with zero configuration.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _default_cache_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "chess-coach-mcp"
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "chess-coach-mcp"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _default_threads() -> int:
    """Half the cores, between 1 and 4: fast, without starving the AI client."""
    return max(1, min(4, (os.cpu_count() or 2) // 2))


@dataclass(slots=True)
class Settings:
    """All tunables for the server."""

    cache_dir: Path = field(default_factory=_default_cache_dir)
    stockfish_path: str | None = None
    auto_download_engine: bool = True
    engine_threads: int = field(default_factory=_default_threads)
    engine_hash_mb: int = 128
    default_depth: int = 14
    refine_depth: int = 18
    time_per_position: float = 0.5  # seconds, first pass
    refine_time_per_position: float = 2.0  # seconds, deeper pass on suspicious moves
    lichess_token: str | None = None
    user_agent: str = "chess-coach-mcp/0.1 (+https://github.com/deven1003/chess-coach-mcp)"

    @classmethod
    def from_env(cls) -> Settings:
        cache_dir = os.environ.get("CHESS_COACH_CACHE_DIR")
        return cls(
            cache_dir=Path(cache_dir) if cache_dir else _default_cache_dir(),
            stockfish_path=os.environ.get("STOCKFISH_PATH") or None,
            auto_download_engine=_env_bool("CHESS_COACH_AUTO_DOWNLOAD", True),
            engine_threads=_env_int("CHESS_COACH_ENGINE_THREADS", _default_threads()),
            engine_hash_mb=_env_int("CHESS_COACH_ENGINE_HASH", 128),
            default_depth=_env_int("CHESS_COACH_DEFAULT_DEPTH", 14),
            refine_depth=_env_int("CHESS_COACH_REFINE_DEPTH", 18),
            time_per_position=_env_float("CHESS_COACH_TIME_PER_POSITION", 0.5),
            refine_time_per_position=_env_float("CHESS_COACH_REFINE_TIME_PER_POSITION", 2.0),
            lichess_token=os.environ.get("LICHESS_TOKEN") or None,
        )

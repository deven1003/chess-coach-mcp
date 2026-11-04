"""Engine backends.

* :class:`StockfishEngine` drives a local Stockfish binary over UCI via python-chess.
* :class:`LichessCloudEngine` asks the Lichess cloud-eval API; it only knows positions
  that someone has analysed before, so it is a fallback, not a replacement.
* :func:`find_stockfish` / :func:`download_stockfish` locate or fetch a binary.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

import chess
import chess.engine
import httpx

from .models import PositionEval

log = logging.getLogger(__name__)

STOCKFISH_RELEASES_URL = "https://api.github.com/repos/official-stockfish/Stockfish/releases/latest"


class EngineError(RuntimeError):
    """Raised when no engine can be started."""


class EngineBackend(Protocol):
    """Anything that can evaluate a FEN."""

    name: str
    description: str

    async def evaluate(
        self, fen: str, depth: int, time_limit: float | None = None
    ) -> PositionEval | None:
        """Evaluate ``fen`` to ``depth`` plies, stopping early after ``time_limit`` seconds."""
        ...

    async def close(self) -> None: ...


class StockfishEngine:
    """Local Stockfish over UCI. Thread-safe; evaluations run off the event loop."""

    name = "stockfish"

    def __init__(self, path: str | os.PathLike[str], *, threads: int = 2, hash_mb: int = 128):
        self.path = str(path)
        popen_args: dict[str, Any] = {}
        if sys.platform == "win32":
            popen_args["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(self.path, **popen_args)
        except (OSError, chess.engine.EngineError) as exc:
            raise EngineError(f"Could not start Stockfish at {self.path}: {exc}") from exc
        self._lock = threading.Lock()
        options: dict[str, Any] = {}
        if "Threads" in self._engine.options:
            options["Threads"] = max(1, threads)
        if "Hash" in self._engine.options:
            options["Hash"] = max(16, hash_mb)
        if options:
            self._engine.configure(options)
        engine_name = self._engine.id.get("name", "Stockfish")
        self.description = f"{engine_name} ({self.path})"

    async def evaluate(
        self, fen: str, depth: int, time_limit: float | None = None
    ) -> PositionEval | None:
        return await asyncio.to_thread(self._evaluate_sync, fen, depth, time_limit)

    def _evaluate_sync(self, fen: str, depth: int, time_limit: float | None) -> PositionEval | None:
        board = chess.Board(fen)
        limit = chess.engine.Limit(depth=depth, time=time_limit)
        with self._lock:
            info = self._engine.analyse(board, limit)
        score = info.get("score")
        if score is None:
            return None
        white = score.white()
        pv = [move.uci() for move in info.get("pv", [])]
        return PositionEval(
            cp=white.score(),
            mate=white.mate(),
            best_move=pv[0] if pv else None,
            pv=pv,
            depth=int(info.get("depth", depth)),
            source=self.name,
        )

    async def close(self) -> None:
        def _quit() -> None:
            with self._lock:
                self._engine.quit()

        await asyncio.to_thread(_quit)


CloudEvalFn = Callable[[str], Awaitable[PositionEval | None]]


class LichessCloudEngine:
    """Fallback backend using Lichess cloud evaluations.

    Only positions already in the Lichess cloud database return a result; everything else
    evaluates to ``None`` and is reported as "unknown" in the analysis.
    """

    name = "lichess-cloud"
    description = "Lichess cloud eval (no local Stockfish found; only well-known positions)"

    def __init__(self, cloud_eval: CloudEvalFn):
        self._cloud_eval = cloud_eval

    async def evaluate(
        self, fen: str, depth: int, time_limit: float | None = None
    ) -> PositionEval | None:
        return await self._cloud_eval(fen)

    async def close(self) -> None:
        return None


# --------------------------------------------------------------------------------------
# Locating / downloading a Stockfish binary
# --------------------------------------------------------------------------------------


def _is_executable_candidate(path: Path) -> bool:
    name = path.name.lower()
    if not name.startswith("stockfish"):
        return False
    if sys.platform == "win32":
        return name.endswith(".exe")
    return "." not in name


def find_stockfish(explicit_path: str | None, cache_dir: Path) -> str | None:
    """Return a path to a Stockfish binary, or ``None`` if none is available."""
    if explicit_path:
        if Path(explicit_path).exists():
            return explicit_path
        log.warning(
            "STOCKFISH_PATH=%s does not exist; falling back to auto-detection", explicit_path
        )
    found = shutil.which("stockfish")
    if found:
        return found
    engine_dir = cache_dir / "stockfish"
    if engine_dir.is_dir():
        candidates = sorted(
            (p for p in engine_dir.rglob("*") if p.is_file() and _is_executable_candidate(p)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return str(candidates[0])
    return None


def asset_preferences() -> list[str]:
    """Substrings identifying suitable release assets for this machine, best first."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    arm = machine in {"arm64", "aarch64"}
    if system == "windows":
        if arm:
            return ["windows-arm64"]
        return ["windows-x86-64-universal", "windows-x86-64-avx2", "windows-x86-64"]
    if system == "darwin":
        if arm:
            return ["macos-universal", "macos-m1-apple-silicon"]
        return ["macos-universal", "macos-x86-64-avx2", "macos-x86-64"]
    if system == "linux":
        if arm:
            return ["linux-arm64"]
        return ["linux-x86-64-universal", "ubuntu-x86-64-avx2", "ubuntu-x86-64"]
    return []


def pick_asset(assets: list[dict[str, Any]], preferences: list[str]) -> dict[str, Any] | None:
    """Choose the first release asset whose name matches the earliest preference."""
    for key in preferences:
        for asset in assets:
            name = str(asset.get("name", "")).lower()
            if key in name and name.endswith((".zip", ".tar", ".tar.gz", ".tgz")):
                return asset
    return None


def _extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                target = (dest / member.filename).resolve()
                if not str(target).startswith(str(root)):
                    raise EngineError(f"Unsafe path in archive: {member.filename}")
            zf.extractall(dest)
        return
    with tarfile.open(archive) as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12 has no filter argument
            for member in tf.getmembers():
                target = (dest / member.name).resolve()
                if not str(target).startswith(str(root)):
                    raise EngineError(f"Unsafe path in archive: {member.name}") from None
            tf.extractall(dest)  # noqa: S202


async def download_stockfish(http: httpx.AsyncClient, cache_dir: Path) -> str:
    """Download the latest official Stockfish release into ``cache_dir`` and return its path."""
    preferences = asset_preferences()
    if not preferences:
        raise EngineError(
            f"No Stockfish build available for {platform.system()} {platform.machine()}"
        )

    response = await http.get(
        STOCKFISH_RELEASES_URL, headers={"Accept": "application/vnd.github+json"}
    )
    response.raise_for_status()
    release = response.json()
    asset = pick_asset(release.get("assets", []), preferences)
    if asset is None:
        raise EngineError("Could not find a matching Stockfish asset in the latest release")

    engine_dir = cache_dir / "stockfish"
    engine_dir.mkdir(parents=True, exist_ok=True)
    archive = engine_dir / str(asset["name"])
    log.info("Downloading %s (%s) ...", asset["name"], release.get("tag_name"))
    download_url = str(asset["browser_download_url"])
    async with http.stream("GET", download_url, follow_redirects=True) as stream:
        stream.raise_for_status()
        with archive.open("wb") as fh:
            async for chunk in stream.aiter_bytes(1 << 16):
                fh.write(chunk)

    await asyncio.to_thread(_extract_archive, archive, engine_dir)
    archive.unlink(missing_ok=True)

    binary = find_stockfish(None, cache_dir)
    if binary is None:
        raise EngineError("Stockfish archive downloaded but no executable was found inside it")
    if sys.platform != "win32":
        path = Path(binary)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log.info("Stockfish installed at %s", binary)
    return binary

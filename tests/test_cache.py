from __future__ import annotations

from pathlib import Path

from chess_coach_mcp.core import analysis
from chess_coach_mcp.core.cache import Cache

from .conftest import FakeEngine


def test_game_round_trip(sample_game):
    cache = Cache(None)
    assert cache.get_game("lichess", "abcdefgh") is None
    cache.put_game(sample_game)
    loaded = cache.get_game("lichess", "abcdefgh")
    assert loaded is not None
    assert loaded.to_dict() == sample_game.to_dict()
    assert cache.find_game("abcdefgh") is not None
    assert cache.get_game("chesscom", "abcdefgh") is None


def test_games_for_user_is_case_insensitive(sample_game):
    cache = Cache(None)
    cache.put_game(sample_game)
    assert [g.game_id for g in cache.games_for_user("lichess", "STUDENT")] == ["abcdefgh"]
    assert cache.games_for_user("lichess", "nobody") == []


async def test_report_round_trip(sample_game, tmp_path: Path):
    cache = Cache(tmp_path / "nested" / "cache.db")
    report = await analysis.analyze_game(sample_game, FakeEngine(), depth=10)
    assert cache.get_report("lichess", "abcdefgh", 10, "fake") is None
    cache.put_report(report)
    loaded = cache.get_report("lichess", "abcdefgh", 10, "fake")
    assert loaded is not None
    assert loaded.to_dict() == report.to_dict()
    # Different depth or engine is a different cache entry.
    assert cache.get_report("lichess", "abcdefgh", 14, "fake") is None
    assert cache.get_report("lichess", "abcdefgh", 10, "stockfish") is None
    assert cache.stats() == {"games": 0, "reports": 1}
    cache.close()

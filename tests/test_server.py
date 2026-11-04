"""End-to-end tests through a real MCP client connected in-process to the server."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp.client import Client

from chess_coach_mcp.server import create_server
from chess_coach_mcp.services import detect_platform, normalize_game_id, normalize_platform

from .test_clients import CHESSCOM_GAME, LICHESS_GAME


@pytest.fixture
def server():
    return create_server()


def _json(result) -> dict:
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


async def test_surface_lists_all_primitives(server, services):
    async with Client(server) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        prompts = {p.name for p in (await client.list_prompts()).prompts}
        templates = {
            t.uri_template for t in (await client.list_resource_templates()).resource_templates
        }
    assert tools == {
        "get_recent_games",
        "analyze_game",
        "find_blunders",
        "get_opening_stats",
        "engine_status",
    }
    assert prompts == {"coach_my_last_game", "weekly_review"}
    assert "chess://games/{username}/recent" in templates
    assert "chess://game/{game_id}/pgn" in templates


async def test_analyze_and_find_blunders_from_cache(server, services, sample_game, fake_engine):
    services.cache.put_game(sample_game)
    async with Client(server) as client:
        report = _json(
            await client.call_tool("analyze_game", {"game_id": "abcdefgh", "username": "student"})
        )
        blunders = _json(
            await client.call_tool(
                "find_blunders", {"game_id": "abcdefgh", "username": "student", "only_user": True}
            )
        )
        status = _json(await client.call_tool("engine_status", {}))

    assert report["game"]["user_color"] == "black"
    assert report["game"]["user_result"] == "loss"
    assert report["engine"] == "fake" and report["depth"] == 10
    assert report["black"]["blunders"] == 1
    assert report["critical_moments"][0]["move"] == "3... Nf6"
    assert report["critical_moments"][0]["best_move"]  # the engine's suggestion, in SAN
    assert "fen_before" in report["critical_moments"][0]
    assert len(report["moves"]) == 7
    assert "fen_before" not in report["moves"][0]  # only suspicious moves carry a FEN

    assert blunders["count"] == 1
    assert blunders["blunders"][0]["san"] == "Nf6"
    assert blunders["blunders"][0]["classification"] == "blunder"

    assert status["engine"] == "fake"
    assert status["cache"] == {"games": 1, "reports": 1}
    # The second tool call reused the cached report instead of re-running the engine.
    assert len([c for c in fake_engine.calls if c[1] == 10]) == 7


async def test_find_blunders_only_user_filters_opponent_moves(server, services, sample_game):
    services.cache.put_game(sample_game)
    async with Client(server) as client:
        all_moves = _json(
            await client.call_tool("find_blunders", {"game_id": "abcdefgh", "threshold": 0})
        )
        own = _json(
            await client.call_tool(
                "find_blunders",
                {"game_id": "abcdefgh", "threshold": 0, "username": "coachbot", "only_user": True},
            )
        )
    assert all_moves["count"] == 7
    assert own["count"] == 4
    assert {m["color"] for m in own["blunders"]} == {"white"}


async def test_missing_game_returns_tool_error(server, services):
    async with Client(server) as client:
        result = await client.call_tool("analyze_game", {"game_id": "123456789"})
    assert result.is_error
    assert "username" in result.content[0].text


async def test_pgn_resource(server, services, sample_game):
    services.cache.put_game(sample_game)
    async with Client(server) as client:
        res = await client.read_resource("chess://game/abcdefgh/pgn")
        res2 = await client.read_resource("chess://lichess/game/abcdefgh/pgn")
    assert "1. e4 e5 2. Bc4" in res.contents[0].text
    assert res2.contents[0].text == res.contents[0].text


async def test_recent_games_tool_and_resource_hit_lichess(server, services):
    respx.get("https://lichess.org/api/games/user/bob").mock(
        return_value=httpx.Response(200, text=json.dumps(LICHESS_GAME) + "\n")
    )
    async with Client(server) as client:
        listing = _json(await client.call_tool("get_recent_games", {"username": "bob", "count": 1}))
        resource = await client.read_resource("chess://games/bob/recent")
        # The game is now cached, so analysis can find it without a second request.
        assert services.cache.get_game("lichess", "q7ZvsdUF") is not None
    assert listing["count"] == 1
    game = listing["games"][0]
    assert game["game_id"] == "q7ZvsdUF"
    assert game["user_color"] == "black" and game["user_result"] == "loss"
    assert game["opponent"]["name"] == "Alice"
    assert json.loads(resource.contents[0].text)[0]["game_id"] == "q7ZvsdUF"


async def test_opening_stats(server, services):
    second = {**LICHESS_GAME, "id": "second01", "winner": "black"}
    third = {**LICHESS_GAME, "id": "third001", "winner": "black"}
    body = "\n".join(json.dumps(g) for g in (LICHESS_GAME, second, third)) + "\n"
    respx.get("https://lichess.org/api/games/user/bob").mock(
        return_value=httpx.Response(200, text=body)
    )
    async with Client(server) as client:
        stats = _json(
            await client.call_tool(
                "get_opening_stats", {"username": "bob", "color": "black", "count": 10}
            )
        )
    assert stats["games_considered"] == 3
    row = stats["openings"][0]
    assert row["opening"] == "King's Pawn Game"
    assert (row["wins"], row["losses"], row["games"]) == (2, 1, 3)
    assert row["score_pct"] == 66.7
    assert stats["weakest_openings"][0]["opening"] == "King's Pawn Game"


async def test_chesscom_game_resolved_by_username(server, services):
    base = "https://api.chess.com/pub/player/dave/games"
    respx.get(f"{base}/archives").mock(
        return_value=httpx.Response(200, json={"archives": [f"{base}/2026/10"]})
    )
    respx.get(f"{base}/2026/10").mock(
        return_value=httpx.Response(200, json={"games": [CHESSCOM_GAME]})
    )
    async with Client(server) as client:
        report = _json(
            await client.call_tool(
                "analyze_game",
                {"game_id": "https://www.chess.com/game/live/123456789", "username": "Dave"},
            )
        )
    assert report["game"]["platform"] == "chesscom"
    assert report["game"]["user_color"] == "black"
    assert len(report["moves"]) == 6


async def test_prompts_mention_the_user(server, services):
    async with Client(server) as client:
        coach = await client.get_prompt("coach_my_last_game", {"username": "bob"})
        weekly = await client.get_prompt(
            "weekly_review", {"username": "dave", "platform": "chesscom"}
        )
    assert 'username="bob"' in coach.messages[0].content.text
    assert "get_recent_games" in coach.messages[0].content.text
    assert 'platform="chesscom"' in weekly.messages[0].content.text
    assert "find_blunders" in weekly.messages[0].content.text


def test_id_helpers():
    assert normalize_game_id("https://lichess.org/q7ZvsdUF/black#12") == "q7ZvsdUF"
    assert normalize_game_id("https://www.chess.com/game/live/123456789") == "123456789"
    assert normalize_game_id("https://www.chess.com/game/daily/42") == "42"
    assert normalize_game_id(" q7ZvsdUF ") == "q7ZvsdUF"
    assert detect_platform("123456789") == "chesscom"
    assert detect_platform("q7ZvsdUF") == "lichess"
    assert normalize_platform("Chess.com") == "chesscom"
    assert normalize_platform(None) == "lichess"
    with pytest.raises(ValueError):
        normalize_platform("fics")

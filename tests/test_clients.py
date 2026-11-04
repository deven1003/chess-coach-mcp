from __future__ import annotations

import json

import httpx
import pytest
import respx

from chess_coach_mcp.clients.chesscom import (
    ChessComClient,
    ChessComError,
    opening_name_from_url,
    parse_chesscom_game,
)
from chess_coach_mcp.clients.lichess import LichessClient, LichessError, parse_lichess_game

LICHESS_GAME = {
    "id": "q7ZvsdUF",
    "rated": True,
    "variant": "standard",
    "speed": "blitz",
    "perf": "blitz",
    "createdAt": 1761993600000,
    "lastMoveAt": 1761994000000,
    "status": "mate",
    "players": {
        "white": {"user": {"name": "Alice", "id": "alice"}, "rating": 1650},
        "black": {"user": {"name": "Bob", "id": "bob"}, "rating": 1600},
    },
    "winner": "white",
    "opening": {"eco": "C20", "name": "King's Pawn Game: Wayward Queen Attack", "ply": 5},
    "moves": "e4 e5 Bc4 Nc6 Qh5 Nf6 Qxf7#",
    "pgn": '[Event "Rated blitz game"]\n[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n\n'
    "1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0\n",
    "clock": {"initial": 300, "increment": 0, "totalTime": 300},
}

LICHESS_DRAW = {
    **LICHESS_GAME,
    "id": "draw0001",
    "status": "draw",
    "winner": None,
    "moves": "e4 e5",
    "pgn": "",
}

LICHESS_960 = {**LICHESS_GAME, "id": "chess960", "variant": "chess960"}


def test_parse_lichess_game():
    game = parse_lichess_game(LICHESS_GAME)
    assert game is not None
    s = game.summary
    assert s.game_id == "q7ZvsdUF"
    assert s.platform == "lichess"
    assert s.white.name == "Alice" and s.white.rating == 1650
    assert s.black.name == "Bob"
    assert s.result == "1-0" and s.winner == "white"
    assert s.date == "2025-11-01T10:40:00+00:00"
    assert s.opening.startswith("King's Pawn Game") and s.eco == "C20"
    assert s.time_control == "300+0" and s.speed == "blitz"
    assert s.url == "https://lichess.org/q7ZvsdUF"
    assert game.moves == ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]
    assert s.outcome_for("bob") == "loss"
    assert s.color_of("ALICE") == "white"
    assert s.opponent_of("bob").name == "Alice"


def test_parse_lichess_draw_builds_pgn_when_missing():
    game = parse_lichess_game(LICHESS_DRAW)
    assert game is not None
    assert game.summary.result == "1/2-1/2" and game.summary.winner is None
    assert "1. e4 e5" in game.pgn
    assert game.summary.outcome_for("alice") == "draw"


def test_parse_lichess_skips_variants():
    assert parse_lichess_game(LICHESS_960) is None


def test_parse_lichess_ai_opponent():
    data = {
        **LICHESS_GAME,
        "players": {"white": {"aiLevel": 3}, "black": {"user": {"name": "Bob"}}},
    }
    game = parse_lichess_game(data)
    assert game is not None and game.summary.white.name == "Stockfish level 3"


async def test_lichess_recent_games_ndjson():
    body = "\n".join(json.dumps(g) for g in (LICHESS_GAME, LICHESS_960, LICHESS_DRAW)) + "\n"
    route = respx.get("https://lichess.org/api/games/user/bob").mock(
        return_value=httpx.Response(200, text=body)
    )
    async with httpx.AsyncClient() as http:
        client = LichessClient(http, token="secret")
        games = await client.get_recent_games("bob", count=5)
    assert [g.game_id for g in games] == ["q7ZvsdUF", "draw0001"]
    request = route.calls.last.request
    assert request.headers["Accept"] == "application/x-ndjson"
    assert request.headers["Authorization"] == "Bearer secret"
    assert request.url.params["max"] == "5"
    assert request.url.params["pgnInJson"] == "true"


async def test_lichess_user_not_found():
    respx.get("https://lichess.org/api/games/user/ghost").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as http:
        with pytest.raises(LichessError, match="not found"):
            await LichessClient(http).get_recent_games("ghost")


async def test_lichess_rate_limit():
    respx.get("https://lichess.org/game/export/q7ZvsdUF").mock(return_value=httpx.Response(429))
    async with httpx.AsyncClient() as http:
        with pytest.raises(LichessError, match="rate limit"):
            await LichessClient(http).get_game("q7ZvsdUF")


async def test_lichess_get_game():
    respx.get("https://lichess.org/game/export/q7ZvsdUF").mock(
        return_value=httpx.Response(200, json=LICHESS_GAME)
    )
    async with httpx.AsyncClient() as http:
        game = await LichessClient(http).get_game("q7ZvsdUF")
    assert game.game_id == "q7ZvsdUF"


async def test_lichess_cloud_eval():
    respx.get("https://lichess.org/api/cloud-eval").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"fen": "x", "depth": 40, "pvs": [{"moves": "e2e4 e7e5", "cp": 20}]},
            ),
            httpx.Response(404, json={"error": "Not found"}),
            httpx.Response(200, json={"depth": 50, "pvs": [{"moves": "d8h4", "mate": -1}]}),
        ]
    )
    async with httpx.AsyncClient() as http:
        client = LichessClient(http)
        hit = await client.cloud_eval("start")
        miss = await client.cloud_eval("unknown")
        mate = await client.cloud_eval("mate")
    assert hit is not None and hit.cp == 20 and hit.best_move == "e2e4" and hit.depth == 40
    assert hit.source == "lichess-cloud"
    assert miss is None
    assert mate is not None and mate.mate == -1 and mate.score() == -1000


# ----------------------------------------------------------------------------- chess.com

CHESSCOM_PGN = (
    '[Event "Live Chess"]\n'
    '[Site "Chess.com"]\n'
    '[Date "2026.10.30"]\n'
    '[White "Carol"]\n'
    '[Black "Dave"]\n'
    '[Result "0-1"]\n'
    '[ECO "B01"]\n'
    '[ECOUrl "https://www.chess.com/openings/Scandinavian-Defense-Mieses-Kotrc-Variation-3.Nc3-Qa5"]\n'
    '[Termination "Dave won by resignation"]\n'
    "\n"
    "1. e4 {[%clk 0:09:59]} d5 {[%clk 0:09:58]} 2. exd5 {[%clk 0:09:57]} Qxd5 "
    "{[%clk 0:09:55]} 3. Nc3 {[%clk 0:09:50]} Qa5 {[%clk 0:09:52]} 0-1\n"
)

CHESSCOM_GAME = {
    "url": "https://www.chess.com/game/live/123456789",
    "pgn": CHESSCOM_PGN,
    "time_control": "600",
    "end_time": 1761820800,
    "rated": True,
    "time_class": "rapid",
    "rules": "chess",
    "eco": "https://www.chess.com/openings/Scandinavian-Defense-Mieses-Kotrc-Variation-3.Nc3-Qa5",
    "white": {"rating": 1200, "result": "resigned", "username": "Carol"},
    "black": {"rating": 1250, "result": "win", "username": "Dave"},
}

CHESSCOM_DRAW = {
    **CHESSCOM_GAME,
    "url": "https://www.chess.com/game/live/5",
    "end_time": 1761900000,
    "white": {"rating": 1200, "result": "repetition", "username": "Carol"},
    "black": {"rating": 1250, "result": "repetition", "username": "Dave"},
}

CHESSCOM_BUGHOUSE = {
    **CHESSCOM_GAME,
    "url": "https://www.chess.com/game/live/6",
    "rules": "bughouse",
}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.chess.com/openings/Scandinavian-Defense-Mieses-Kotrc-Variation-3.Nc3-Qa5",
            "Scandinavian Defense Mieses Kotrc Variation",
        ),
        ("https://www.chess.com/openings/Benoni-Defense-Old-Benoni", "Benoni Defense Old Benoni"),
        ("https://www.chess.com/openings/Kings-Pawn-Opening-2.Nf3", "Kings Pawn Opening"),
        ("https://www.chess.com/openings/Sicilian-Defense-e6", "Sicilian Defense"),
        (None, None),
    ],
)
def test_opening_name_from_url(url, expected):
    assert opening_name_from_url(url) == expected


def test_parse_chesscom_game():
    game = parse_chesscom_game(CHESSCOM_GAME)
    assert game is not None
    s = game.summary
    assert s.game_id == "123456789" and s.platform == "chesscom"
    assert s.white.name == "Carol" and s.black.rating == 1250
    assert s.result == "0-1" and s.winner == "black"
    assert s.date == "2025-10-30T10:40:00+00:00"
    assert s.opening == "Scandinavian Defense Mieses Kotrc Variation" and s.eco == "B01"
    assert s.time_control == "600" and s.speed == "rapid"
    assert s.termination == "Dave won by resignation"
    assert game.moves == ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5"]
    assert s.outcome_for("carol") == "loss"


def test_parse_chesscom_draw_and_variants():
    draw = parse_chesscom_game(CHESSCOM_DRAW)
    assert draw is not None and draw.summary.result == "1/2-1/2" and draw.summary.winner is None
    assert parse_chesscom_game(CHESSCOM_BUGHOUSE) is None


async def test_chesscom_recent_games_walks_archives_newest_first():
    base = "https://api.chess.com/pub/player/dave/games"
    respx.get(f"{base}/archives").mock(
        return_value=httpx.Response(200, json={"archives": [f"{base}/2026/09", f"{base}/2026/10"]})
    )
    old = respx.get(f"{base}/2026/09").mock(return_value=httpx.Response(200, json={"games": []}))
    respx.get(f"{base}/2026/10").mock(
        return_value=httpx.Response(200, json={"games": [CHESSCOM_GAME, CHESSCOM_DRAW]})
    )
    async with httpx.AsyncClient() as http:
        client = ChessComClient(http)
        games = await client.get_recent_games("Dave", count=1)
        assert [g.game_id for g in games] == ["5"]  # newest by end_time
        assert not old.called  # stopped once enough games were collected
        found = await client.find_game("Dave", "123456789")
        missing = await client.find_game("Dave", "0")
    assert found is not None and found.game_id == "123456789"
    assert missing is None
    assert old.called  # an exhaustive search walks every archive


async def test_chesscom_user_not_found():
    respx.get("https://api.chess.com/pub/player/ghost/games/archives").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ChessComError, match="not found"):
            await ChessComClient(http).get_recent_games("ghost")

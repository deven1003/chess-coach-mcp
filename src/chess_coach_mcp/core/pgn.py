"""PGN helpers built on python-chess."""

from __future__ import annotations

import io

import chess
import chess.pgn


def parse_pgn(pgn: str) -> tuple[dict[str, str], list[str]]:
    """Return ``(headers, moves_in_san)`` for the first game in ``pgn``.

    Raises ``ValueError`` if the text does not contain a game.
    """
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise ValueError("No game found in PGN text")
    headers = dict(game.headers)
    board = game.board()
    moves: list[str] = []
    for move in game.mainline_moves():
        moves.append(board.san(move))
        board.push(move)
    return headers, moves


def moves_to_pgn(headers: dict[str, str], moves: list[str]) -> str:
    """Build a PGN string from headers and a SAN move list."""
    game = chess.pgn.Game()
    for key, value in headers.items():
        game.headers[key] = value
    node: chess.pgn.GameNode = game
    board = game.board()
    for san in moves:
        move = board.parse_san(san)
        node = node.add_variation(move)
        board.push(move)
    return str(game)

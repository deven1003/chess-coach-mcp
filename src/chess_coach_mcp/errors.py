"""Translate anticipated failures into MCP errors whose message reaches the model.

mcp 2.x deliberately hides the text of unexpected exceptions ("Error executing tool x").
For failures the user can act on (unknown username, rate limit, missing engine, bad
game id) we want the assistant to read the reason, so tools and resources wrap their
bodies in these context managers.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from chess_coach_mcp.clients import ChessComError, LichessError
from chess_coach_mcp.core.engine import EngineError

ANTICIPATED: tuple[type[Exception], ...] = (
    ValueError,
    LichessError,
    ChessComError,
    EngineError,
    httpx.HTTPError,
)


def _message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPError):
        return f"Network error talking to {exc.request.url.host}: {exc}"
    return str(exc) or exc.__class__.__name__


@contextmanager
def tool_errors() -> Iterator[None]:
    """Re-raise anticipated exceptions as ``ToolError`` so the model sees the message."""
    try:
        yield
    except ToolError:
        raise
    except ANTICIPATED as exc:
        raise ToolError(_message(exc)) from exc


@contextmanager
def resource_errors() -> Iterator[None]:
    """Re-raise anticipated exceptions as ``ResourceError``."""
    try:
        yield
    except ResourceError:
        raise
    except ANTICIPATED as exc:
        raise ResourceError(_message(exc)) from exc

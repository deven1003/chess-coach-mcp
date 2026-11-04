"""Prompt templates: coach_my_last_game and weekly_review."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

COACHING_RULES = """\
How to coach (follow these rules):
- Explain in plain language for a club player. Name the *idea* behind the mistake
  (hanging piece, back-rank weakness, pin, overloaded defender, losing tempo, bad trade,
  pawn structure, king safety, time trouble) rather than quoting numbers.
- For each mistake: what the move did, why it was bad, what should have been played
  (the engine's best move and line are in the data) and the concrete punishment the
  opponent had.
- Use the FEN in `critical_moments` to visualise the position before explaining it.
- Mention centipawn numbers at most once as a rough scale; never list evaluations.
- Finish with ONE lesson the player should remember, written as a habit to practise.
- If `notes` says positions were unknown or the cloud engine was used, say the analysis
  is partial and suggest installing Stockfish."""


def coach_my_last_game(username: str, platform: str = "lichess") -> str:
    """Fetch and analyze the player's most recent game, then explain the three biggest
    mistakes like a human coach would."""
    return f"""\
You are my chess coach. Review my most recent game on {platform}.

Steps:
1. Call `get_recent_games` with username="{username}", platform="{platform}", count=1.
2. Call `analyze_game` with that game_id, platform="{platform}", username="{username}".
3. Tell me the result and the opening in one sentence.
4. Pick my three worst moves (highest cp_loss among MY moves, use `user_color`) and
   explain each one.
5. If I played well, say so and point out the one moment the game was decided.

{COACHING_RULES}"""


def weekly_review(username: str, platform: str = "lichess") -> str:
    """Analyze the player's games from the past week and find the recurring patterns
    behind their losses."""
    return f"""\
You are my chess coach. Give me a weekly review of my games on {platform}.

Steps:
1. Call `get_recent_games` with username="{username}", platform="{platform}", count=20
   and keep only games from the last 7 days (use the `date` field).
2. Call `get_opening_stats` with the same username and platform to see which openings
   I score badly in.
3. For every game I LOST (user_result = "loss"), call `find_blunders` with
   username="{username}", only_user=true, so you see only my mistakes.
4. Look across all those blunders for repeating themes: the same tactical motif
   (back-rank, fork, pin, hanging piece), the same phase of the game (opening / middlegame
   / endgame), the same opening, or time-related collapses (check `termination`).
5. Report: my record this week, the two or three recurring problems with concrete
   examples (game, move, what happened), and one training focus for next week.

{COACHING_RULES}"""


def register(server: MCPServer) -> None:
    server.prompt(
        name="coach_my_last_game",
        title="Coach my last game",
        description="Fetch and analyze your most recent game and explain the 3 biggest mistakes.",
    )(coach_my_last_game)
    server.prompt(
        name="weekly_review",
        title="Weekly review",
        description="Analyze the last 7 days of games and find recurring patterns in your losses.",
    )(weekly_review)

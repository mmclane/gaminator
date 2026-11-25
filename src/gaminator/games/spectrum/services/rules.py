"""Pure Spectrum rules: no Discord, no database."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

MIN_PLAYERS = 3
MAX_PLAYERS = 12
DEFAULT_ROUNDS = 1
MAX_ROUNDS = 5
CLUE_LIMIT = 80
IDLE_TO_STOP = 2
ABANDONED_TO_STOP = 3
GAME_OVER = "That game is over."
TURN_OVER = "That turn is over."
LEFT = "You've left the game."
CLUE_OVER = "The clue step for that turn is over."
GUESS_OVER = "Guessing for that turn has closed."
NOT_GIVER = "Only the clue giver can write the clue."
NOT_YOUR_TARGET = "Only **{giver}** can see the target. Hang on for their clue."
CLUE_IS_IN = "Your clue is in. The target stays secret until the reveal."
NOT_PLAYING = "Only players in this game can guess. Join with `/gaminator spectrum join`."
JOINED_LATE = "You joined after this clue. You can guess from the next one."
NO_DIGITS = "Clues can't contain digits. Describe it in words instead."
EMPTY_CLUE = "Your clue can't be empty."
BAD_GUESS = "Enter a whole number from 1 to 100, then press **Guess** again."
GIVER_SITS_OUT = "You gave the clue, so you sit this one out."
DIGIT = re.compile(r"\d")


def min_players(override: int | None = None) -> int:
    """Players needed to start. The MIN_PLAYERS testing override can lower it, never below 2."""
    return max(2, override) if override is not None else MIN_PLAYERS


def too_few_to_continue(active: int, need: int) -> bool:
    return active < need


def next_host(players: Sequence, leaving_id: int):
    """The longest-joined player still in the game, other than the one leaving."""
    remaining = [p for p in players if p["left_at"] is None and p["id"] != leaving_id]
    return min(remaining, key=lambda p: (p["joined_at"], p["id"]), default=None)


def clean_clue(text: str) -> str | None:
    """A clue with whitespace collapsed, cut to CLUE_LIMIT. None when nothing is left."""
    return " ".join(text.split())[:CLUE_LIMIT].strip() or None


def clue_problem(text: str) -> str | None:
    """Why this clue can't be taken, or None. Any digit character counts, in any script."""
    clue = clean_clue(text)
    if clue is None:
        return EMPTY_CLUE
    if DIGIT.search(clue):
        return NO_DIGITS
    return None


def parse_guess(text: str) -> int | None:
    """A guess typed as plain digits, from 1 to 100. None for anything else."""
    digits = text.strip()
    if not digits or not digits.isascii() or not digits.isdecimal():
        return None
    value = int(digits)
    return value if 1 <= value <= 100 else None


def guess_points(guess: int, target: int) -> int:
    return max(0, 100 - 3 * abs(guess - target))


def giver_points(scores: Sequence[int]) -> int:
    """The guessers' average, rounded half up. 0 when nobody guessed."""
    if not scores:
        return 0
    return (2 * sum(scores) + len(scores)) // (2 * len(scores))


def target_problem(ctx) -> str | None:
    """Why this user can't see the target, or None. ``ctx`` is the repo's turn_context row.

    NOT_YOUR_TARGET comes back with its ``{giver}`` placeholder for the caller to fill in.
    """
    if ctx is None or ctx["status"] == "finished":
        return GAME_OVER
    if ctx["closed_at"]:
        return TURN_OVER
    if ctx["player_id"] is None or ctx["player_id"] != ctx["giver_id"]:
        return NOT_YOUR_TARGET
    if ctx["clue"] is not None:
        return CLUE_IS_IN
    return None


def clue_submit_problem(ctx) -> str | None:
    """Why this user can't give the clue for this turn, or None."""
    if ctx is None or ctx["status"] == "finished":
        return GAME_OVER
    if ctx["closed_at"] or ctx["clue"] is not None:
        return CLUE_OVER
    if ctx["player_id"] is None or ctx["player_id"] != ctx["giver_id"]:
        return NOT_GIVER
    if ctx["left_at"] is not None:
        return LEFT
    return None


def guess_problem(ctx) -> str | None:
    """Why this user can't guess on this turn, or None."""
    if ctx is None or ctx["status"] == "finished":
        return GAME_OVER
    if ctx["closed_at"] or ctx["clue"] is None:
        return GUESS_OVER
    if ctx["player_id"] is None:
        return NOT_PLAYING
    if ctx["left_at"] is not None:
        return LEFT
    if ctx["player_id"] == ctx["giver_id"]:
        return GIVER_SITS_OUT
    if not ctx["may_guess"]:
        return JOINED_LATE
    return None


def unguessed_streak(turns: Sequence[tuple[int, int]]) -> bool:
    """True when the last IDLE_TO_STOP revealed turns that had guessers all got no guesses.

    ``turns`` holds (eligible guessers, guesses) per revealed turn in play order.
    """
    counted = [guesses for eligible, guesses in turns if eligible > 0]
    recent = counted[-IDLE_TO_STOP:]
    return len(recent) == IDLE_TO_STOP and not any(recent)


def round_without_clue(no_clue: int, clued: int) -> bool:
    """True for a round where at least one clue was waited for and nobody gave one."""
    return no_clue >= 1 and clued == 0


@dataclass(frozen=True)
class ScoredTurn:
    """A revealed turn. ``giver_id`` is a user id and ``guesses`` holds (user id, value)."""

    giver_id: int
    left: str
    right: str
    target: int
    clue: str
    closed_at: str
    guesses: tuple[tuple[int, int], ...]

    def points(self) -> dict[int, int]:
        """Points by user id: each guesser's, plus the giver's average of them."""
        scores = {user_id: guess_points(value, self.target) for user_id, value in self.guesses}
        scores[self.giver_id] = giver_points(list(scores.values()))
        return scores


def game_points(turns: Iterable[ScoredTurn], players: Iterable[int]) -> list[tuple[int, int]]:
    """(user id, total points) for every listed player, giver and guesser, highest first."""
    totals = dict.fromkeys(players, 0)
    for turn in turns:
        for user_id, points in turn.points().items():
            totals[user_id] = totals.get(user_id, 0) + points
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def game_winners(points: Sequence[tuple[int, int]]) -> tuple[int, ...]:
    if not points or points[0][1] == 0:
        return ()
    top = points[0][1]
    return tuple(i for i, n in points if n == top)


def closest_guess(turns: Sequence[ScoredTurn]) -> tuple[ScoredTurn, int, int] | None:
    """(turn, user id, value) of the closest guess: by distance, then the earliest turn."""
    found = [
        (abs(value - turn.target), turn.closed_at, user_id, value, turn)
        for turn in turns
        for user_id, value in turn.guesses
    ]
    if not found:
        return None
    _, _, user_id, value, turn = min(found, key=lambda f: f[:3])
    return turn, user_id, value


@dataclass(frozen=True)
class Standing:
    user_id: int
    games_won: int
    points: int
    games_played: int


def standings(
    games: Mapping[int, Sequence[ScoredTurn]], played: Mapping[int, int]
) -> list[Standing]:
    """All-time standings, best first, from each finished game's revealed turns."""
    points: Counter[int] = Counter()
    game_wins: Counter[int] = Counter()
    for turns in games.values():
        for turn in turns:
            points.update(turn.points())
        game_wins.update(game_winners(game_points(turns, ())))
    users = set(played) | set(points)
    rows = [Standing(u, game_wins[u], points[u], played.get(u, 0)) for u in users]
    return sorted(rows, key=lambda s: (-s.games_won, -s.points, -s.games_played, s.user_id))


def leaderboard_rows(
    rows: Sequence[Standing], caller_id: int, top: int = 10
) -> list[tuple[int, Standing]]:
    """(rank, standing) for the top rows, plus the caller's own row when they rank lower."""
    ranked = list(enumerate(rows, start=1))
    shown = ranked[:top]
    shown += [(rank, s) for rank, s in ranked[top:] if s.user_id == caller_id]
    return shown

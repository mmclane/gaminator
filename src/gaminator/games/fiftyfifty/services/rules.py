"""Pure Fifty-Fifty rules: no Discord, no database."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from ....core.util import parse_iso

MIN_PLAYERS = 3
MAX_PLAYERS = 12
DEFAULT_ROUNDS = 1
MAX_ROUNDS = 5
OPTION_LIMIT = 80
EXACT_GUESS_POINTS = 50
NEAR_GUESS_POINTS = 20
VOTES_TO_SCORE = 2
VOTES_FOR_NEAR_GUESS = 4
UNVOTED_TO_STOP = 2
JUST_CLOSED_SECONDS = 60
ABANDONED_TO_STOP = 3
CLOSED = "Voting on that question has closed."
WRITING_CLOSED = "Writing has closed, so that question didn't make it in."
GAME_OVER = "That game is over."
WRITE_OVER = "Writing for that round has closed."
LEFT = "You've left the game."
NOT_IN_ROUND = "You're not in this round. If you just joined, you're in from the next one."
VOTE_FIRST = "Pick A or B first, then guess the split."
LEAD_IN = re.compile(r"^would you rather\b[\s,:.]*", re.IGNORECASE)
LEAD_OR = re.compile(r"^or[\s,:.]+(?=\S)", re.IGNORECASE)


def min_players(override: int | None = None) -> int:
    """Players needed to start. The MIN_PLAYERS testing override can lower it, never below 2."""
    return max(2, override) if override is not None else MIN_PLAYERS


def too_few_to_continue(active: int, need: int) -> bool:
    return active < need


def score(a: int, b: int) -> int:
    """Points for the writer: 100 for the most even split the vote count allows, rounded half up."""
    half = (a + b) // 2
    if half == 0:
        return 0
    return (200 * min(a, b) + half) // (2 * half)


def split_text(a: int, b: int) -> str:
    total = a + b
    if total % 2 == 1 and abs(a - b) == 1:
        return f"as even as {total} votes can be"
    if a == b:
        return "a 50/50 split"
    larger = (200 * max(a, b) + total) // (2 * total)
    return f"a {larger}/{100 - larger} split"


def guess_score(guess: int, a: int, b: int) -> int:
    """Points for a voter who guessed ``guess`` votes for A.

    The exact count scores EXACT_GUESS_POINTS. One off scores NEAR_GUESS_POINTS once
    VOTES_FOR_NEAR_GUESS votes were cast. Nothing scores under VOTES_TO_SCORE votes.
    """
    total = a + b
    if total < VOTES_TO_SCORE:
        return 0
    if guess == a:
        return EXACT_GUESS_POINTS
    if abs(guess - a) == 1 and total >= VOTES_FOR_NEAR_GUESS:
        return NEAR_GUESS_POINTS
    return 0


def tidy_option(text: str, second: bool = False) -> str:
    """An option with whitespace collapsed and the words the bot adds itself cut.

    Those are a leading "would you rather", a trailing "?", and a leading "or" on the second
    option, so "tea" and "or coffee?" read as intended.
    """
    words = LEAD_IN.sub("", " ".join(text.split()))
    if second:
        words = LEAD_OR.sub("", words)
    return words.rstrip("?").rstrip()


def clean_option(text: str, second: bool = False) -> str | None:
    """A tidied option cut to OPTION_LIMIT. None when nothing is left."""
    return tidy_option(text, second)[:OPTION_LIMIT].strip() or None


def too_long(text: str, second: bool = False) -> bool:
    return len(tidy_option(text, second)) > OPTION_LIMIT


def split_options(text: str) -> tuple[str, str] | None:
    """The two options in a DM: on two lines, or split by one ``|``. None for anything else."""
    if "|" in text:
        parts = [part.strip() for part in text.split("|")]
    else:
        parts = [line.strip() for line in text.splitlines() if line.strip()]
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def nobody_voted_streak(vote_totals: Sequence[int]) -> bool:
    """True when the last two asked questions both closed with no votes."""
    recent = vote_totals[-UNVOTED_TO_STOP:]
    return len(recent) == UNVOTED_TO_STOP and not any(recent)


def next_host(players: Sequence, leaving_id: int):
    """The longest-joined player still in the game, other than the one leaving."""
    remaining = [p for p in players if p["left_at"] is None and p["id"] != leaving_id]
    return min(remaining, key=lambda p: (p["joined_at"], p["id"]), default=None)


def write_problem(ctx) -> str | None:
    """Why this user can't write in this round, or None. ``ctx`` is the repo's write_context row."""
    if ctx is None or ctx["status"] == "finished":
        return GAME_OVER
    if ctx["round_closed_at"] or ctx["write_closed_at"]:
        return WRITE_OVER
    if ctx["player_id"] is None:
        return "Only players in this game can write. Join with `/gaminator fiftyfifty join`."
    if ctx["left_at"] is not None:
        return LEFT
    if not ctx["on_roster"] or ctx["question_id"] is None:
        return NOT_IN_ROUND
    return None


def dm_action(rows: Sequence, now: datetime) -> tuple[str, object | None]:
    """What to do with a DM: ("open" | "closed" | "ignore", the question row).

    ``rows`` are the sender's dm_candidates. "open" is a question still being written; "closed"
    is one that was sent by DM and whose writing closed in the last minute.
    """
    open_rows = [r for r in rows if r["write_closed_at"] is None]
    if open_rows:
        return "open", open_rows[0]
    for row in rows:
        closed = parse_iso(row["write_closed_at"])
        if (
            row["dm_prompted_at"]
            and closed
            and now - closed <= timedelta(seconds=JUST_CLOSED_SECONDS)
        ):
            return "closed", row
    return "ignore", None


def guess_problem(game, question, voter, on_roster: bool, voted: bool, guess: int) -> str | None:
    """Why this guess can't be taken, or None when it can."""
    problem = vote_problem(game, question, voter, on_roster)
    if problem:
        return problem
    if not voted:
        return VOTE_FIRST
    if not 0 <= guess < MAX_PLAYERS:
        return "That guess is out of range."
    return None


def vote_problem(game, question, voter, on_roster: bool) -> str | None:
    """Why this vote can't be taken, or None when it can."""
    if (
        game is None
        or question is None
        or game["status"] != "active"
        or not question["started_at"]
        or question["closed_at"]
    ):
        return CLOSED
    if voter is None:
        return "Only players in this game can vote. Join with `/gaminator fiftyfifty join`."
    if voter["left_at"] is not None or not on_roster:
        return NOT_IN_ROUND
    if voter["id"] == question["writer_id"]:
        return "You wrote this one, so you sit this vote out."
    return None


@dataclass(frozen=True)
class ScoredQuestion:
    """A closed question with its vote counts. ``writer_id`` is the writer's user id.

    ``answer`` is the filled-in blank of a question from before players wrote both options, and
    None otherwise. ``guesses`` holds (user id, guessed votes for A) for each voter who guessed.
    """

    writer_id: int
    option_a: str
    option_b: str
    answer: str | None
    a: int
    b: int
    closed_at: str
    guesses: tuple[tuple[int, int], ...] = ()

    @property
    def points(self) -> int:
        """The writer's points for the split."""
        return score(self.a, self.b)

    @property
    def guess_points(self) -> list[tuple[int, int]]:
        """(user id, points) for each voter whose guess scored, highest first."""
        scored = [(uid, guess_score(guess, self.a, self.b)) for uid, guess in self.guesses]
        return sorted([(uid, n) for uid, n in scored if n], key=lambda kv: (-kv[1], kv[0]))


def game_points(
    questions: Iterable[ScoredQuestion], players: Iterable[int]
) -> list[tuple[int, int]]:
    """(user id, total points) for every listed player, writer and guesser, highest first."""
    totals = dict.fromkeys(players, 0)
    for q in questions:
        totals[q.writer_id] = totals.get(q.writer_id, 0) + q.points
        for user_id, points in q.guess_points:
            totals[user_id] = totals.get(user_id, 0) + points
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def game_winners(points: Sequence[tuple[int, int]]) -> tuple[int, ...]:
    if not points or points[0][1] == 0:
        return ()
    top = points[0][1]
    return tuple(i for i, n in points if n == top)


def best_question(questions: Sequence[ScoredQuestion]) -> ScoredQuestion | None:
    """The best split: most points, then most votes, then the earliest to close."""
    best = None
    for q in questions:
        if (
            best is None
            or (q.points, q.a + q.b) > (best.points, best.a + best.b)
            or (q.points, q.a + q.b) == (best.points, best.a + best.b)
            and (q.closed_at < best.closed_at)
        ):
            best = q
    return best


@dataclass(frozen=True)
class Standing:
    user_id: int
    games_won: int
    points: int
    questions: int
    games_played: int
    guess_points: int = 0


def standings(
    games: Mapping[int, Sequence[ScoredQuestion]], played: Mapping[int, int]
) -> list[Standing]:
    """All-time standings, best first, from each finished game's revealed questions."""
    points: Counter[int] = Counter()
    guessed: Counter[int] = Counter()
    questions: Counter[int] = Counter()
    game_wins: Counter[int] = Counter()
    for asked in games.values():
        for q in asked:
            points[q.writer_id] += q.points
            questions[q.writer_id] += 1
            for user_id, earned in q.guess_points:
                points[user_id] += earned
                guessed[user_id] += earned
        game_wins.update(game_winners(game_points(asked, ())))
    users = set(played) | set(questions) | set(guessed)
    rows = [
        Standing(u, game_wins[u], points[u], questions[u], played.get(u, 0), guessed[u])
        for u in users
    ]
    return sorted(rows, key=lambda s: (-s.games_won, -s.points, -s.games_played, s.user_id))


def leaderboard_rows(
    rows: Sequence[Standing], caller_id: int, top: int = 10
) -> list[tuple[int, Standing]]:
    """(rank, standing) for the top rows, plus the caller's own row when they rank lower."""
    ranked = list(enumerate(rows, start=1))
    shown = ranked[:top]
    shown += [(rank, s) for rank, s in ranked[top:] if s.user_id == caller_id]
    return shown


def best_questions(
    games: Mapping[int, Sequence[ScoredQuestion]], user_id: int, limit: int = 3
) -> list[ScoredQuestion]:
    """The questions this user wrote that split best: most points first, then the newest."""
    mine = [q for asked in games.values() for q in asked if q.writer_id == user_id]
    newest_first = sorted(mine, key=lambda q: q.closed_at, reverse=True)
    return sorted(newest_first, key=lambda q: -q.points)[:limit]

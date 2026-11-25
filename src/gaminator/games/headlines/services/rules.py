"""Pure Headlines rules: no Discord, no database."""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

MIN_PLAYERS = 3
MAX_PLAYERS = 12
DEFAULT_ROUNDS = 2
MAX_ROUNDS = 5
ANSWER_LIMIT = 100
HEADLINE_LIMIT = 120
WRITER_POINTS = 100
QUOTED_POINTS = 25
ABANDONED_TO_STOP = 3
REVEAL_BUDGET_SECONDS = 20
GAME_OVER = "That game is over."
LEFT = "You've left the game."
NOT_PLAYING = "Only players in this game can do that. Join with `/gaminator headlines join`."
NEXT_ROUND = "You joined during this round, so you're in from the next one."
ANSWER_OVER = "Answering for that round has closed."
WRITE_OVER = "Headline writing for that round has closed."
VOTE_OVER = "Voting for that round has closed."
NO_QUOTE = "You don't have a quote to write for this round. Sit back, the headlines are coming."
OWN_HEADLINE = "You wrote that headline, so you can't vote for it. Pick another one."
EMPTY_ANSWER = "Your answer can't be empty. Press **Answer** to try again."
EMPTY_HEADLINE = "Your headline can't be empty. Press **Write headline** to try again."
BAD_PICK = "Pick a headline from the menu."
OWES_ANSWER = "You haven't answered yet."
OWES_HEADLINE = "You have a quote waiting for a headline."
OWES_VOTE = "You haven't voted yet."
NO_ANSWERS_NOTE = "nobody answered this round"
NO_HEADLINES_NOTE = "no headlines came in this round"
NO_VOTES_NOTE = "nobody voted this round"


def min_players(override: int | None = None) -> int:
    """Players needed to start. The MIN_PLAYERS testing override can lower it, never below 2."""
    return max(2, override) if override is not None else MIN_PLAYERS


def too_few_to_continue(active: int, need: int) -> bool:
    return active < need


def next_host(players: Sequence, leaving_id: int):
    """The longest-joined player still in the game, other than the one leaving."""
    remaining = [p for p in players if p["left_at"] is None and p["id"] != leaving_id]
    return min(remaining, key=lambda p: (p["joined_at"], p["id"]), default=None)


def clean_text(text: str, limit: int) -> str | None:
    """Text with whitespace collapsed, cut to ``limit``. None when nothing is left."""
    return " ".join(text.split())[:limit].strip() or None


def _caller_problem(ctx) -> str | None:
    """Why this caller is not playing this round, or None. Shared by every step."""
    if ctx["player_id"] is None:
        return NOT_PLAYING
    if ctx["left_at"] is not None:
        return LEFT
    if ctx["answer_id"] is None:
        return NEXT_ROUND
    return None


def answer_problem(ctx) -> str | None:
    """Why this user can't answer in this round, or None. ``ctx`` is the round_context row."""
    if ctx is None or ctx["status"] == "finished":
        return GAME_OVER
    if ctx["answer_closed_at"] or ctx["closed_at"]:
        return ANSWER_OVER
    return _caller_problem(ctx)


def write_problem(ctx) -> str | None:
    """Why this user can't write a headline in this round, or None."""
    if ctx is None or ctx["status"] == "finished":
        return GAME_OVER
    if ctx["write_closed_at"] or ctx["closed_at"] or ctx["write_deadline_at"] is None:
        return WRITE_OVER
    problem = _caller_problem(ctx)
    if problem:
        return problem
    if ctx["pairing_id"] is None:
        return NO_QUOTE
    return None


def vote_problem(ctx) -> str | None:
    """Why this user can't vote in this round, or None."""
    if ctx is None or ctx["status"] == "finished":
        return GAME_OVER
    if ctx["vote_deadline_at"] is None or ctx["vote_closed_at"] or ctx["closed_at"]:
        return VOTE_OVER
    return _caller_problem(ctx)


def owed_line(ctx) -> str | None:
    """What a roster player still in the game owes the open step, or None."""
    if ctx is None or ctx["status"] == "finished" or ctx["closed_at"]:
        return None
    if _caller_problem(ctx):
        return None
    if not ctx["answer_closed_at"]:
        return OWES_ANSWER if ctx["answer"] is None else None
    if ctx["write_deadline_at"] and not ctx["write_closed_at"]:
        waiting = ctx["pairing_id"] is not None and ctx["headline"] is None
        return OWES_HEADLINE if waiting else None
    if ctx["vote_deadline_at"] and not ctx["vote_closed_at"]:
        return OWES_VOTE if ctx["can_vote"] and ctx["my_vote"] is None else None
    return None


def assign_quotes(
    answers: Sequence[tuple[int, int]], writers: Sequence[int], rng: random.Random
) -> list[tuple[int, int]]:
    """Hand each answer to a writer: (answer id, writer player id).

    ``answers`` holds (answer id, author player id) and ``writers`` the players still in.
    Answers rotate among the writers who answered, so each of them gets one quote and never
    their own. A lone answering writer's answer goes to one who did not answer. Answers from
    players who left go to writers still without a quote, and are dropped when nobody is free.
    """
    if len(writers) < 2:
        return []
    still_in = set(writers)
    by_author = {author: answer_id for answer_id, author in answers}
    answering = [w for w in writers if w in by_author]
    free = [w for w in writers if w not in by_author]
    leavers = [answer_id for answer_id, author in answers if author not in still_in]
    rng.shuffle(answering)
    rng.shuffle(free)
    rng.shuffle(leavers)
    pairs = []
    if len(answering) >= 2:
        count = len(answering)
        pairs += [(by_author[w], answering[(i + 1) % count]) for i, w in enumerate(answering)]
    elif answering:
        pairs.append((by_author[answering[0]], free.pop(0)))
        free.append(answering[0])
    pairs += list(zip(leavers, free))
    return pairs


def reveal_pause(count: int) -> float:
    """Seconds to wait before each pairing, so a whole reveal pauses 20 seconds at most."""
    from . import game

    if count <= 0:
        return 0
    return min(game.REVEAL_PAUSE, REVEAL_BUDGET_SECONDS / count)


def idle_reason(outcome: str | None, eligible_voters: int | None, votes: int) -> str | None:
    """Why a game should stop after a round nobody played, or None. Stored values only."""
    if outcome == "no_answers":
        return NO_ANSWERS_NOTE
    if outcome == "no_headlines":
        return NO_HEADLINES_NOTE
    if outcome == "voted" and (eligible_voters or 0) > 0 and votes == 0:
        return NO_VOTES_NOTE
    return None


@dataclass(frozen=True)
class ScoredPairing:
    """A headline that was voted on. ``writer_id`` and ``quoted_id`` are user ids."""

    round_number: int
    position: int
    writer_id: int
    quoted_id: int
    headline: str
    answer: str
    votes: int

    def points(self) -> dict[int, int]:
        return {
            self.writer_id: WRITER_POINTS * self.votes,
            self.quoted_id: QUOTED_POINTS * self.votes,
        }


def game_points(pairings: Iterable[ScoredPairing], players: Iterable[int]) -> list[tuple[int, int]]:
    """(user id, total points) for every listed player, writer and quoted player, highest first."""
    totals = dict.fromkeys(players, 0)
    for pairing in pairings:
        for user_id, points in pairing.points().items():
            totals[user_id] = totals.get(user_id, 0) + points
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def game_winners(points: Sequence[tuple[int, int]]) -> tuple[int, ...]:
    if not points or points[0][1] == 0:
        return ()
    top = points[0][1]
    return tuple(i for i, n in points if n == top)


def top_headline(pairings: Sequence[ScoredPairing]) -> ScoredPairing | None:
    """The pairing with the most votes: then the earliest round, then the lowest position."""
    voted = [p for p in pairings if p.votes > 0]
    return min(voted, key=lambda p: (-p.votes, p.round_number, p.position), default=None)


@dataclass(frozen=True)
class Standing:
    user_id: int
    games_won: int
    points: int
    games_played: int


def standings(
    games: Mapping[int, Sequence[ScoredPairing]], played: Mapping[int, int]
) -> list[Standing]:
    """All-time standings, best first, from each finished game's voted pairings."""
    points: Counter[int] = Counter()
    game_wins: Counter[int] = Counter()
    for pairings in games.values():
        for pairing in pairings:
            points.update(pairing.points())
        game_wins.update(game_winners(game_points(pairings, ())))
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

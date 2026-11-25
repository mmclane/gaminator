"""Pure Most Likely To rules: no Discord, no database."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

MIN_PLAYERS = 3
MIN_TO_CONTINUE = 2
MAX_PLAYERS = 25
EMPTY_PROMPTS_TO_STOP = 2
CLOSED = "Voting on that prompt has closed."


def min_players(override: int | None = None) -> int:
    """Players needed to start. The MIN_PLAYERS testing override can lower it, never below 2."""
    return max(2, override) if override is not None else MIN_PLAYERS


def too_few_to_continue(active: int) -> bool:
    return active < MIN_TO_CONTINUE


def nobody_voted_streak(rounds: Sequence[list[int]]) -> bool:
    """True when the last two revealed prompts both closed with no votes."""
    recent = rounds[-EMPTY_PROMPTS_TO_STOP:]
    return len(recent) == EMPTY_PROMPTS_TO_STOP and not any(recent)


@dataclass(frozen=True)
class Tally:
    counts: dict[int, int] = field(default_factory=dict)
    leaders: tuple[int, ...] = ()
    top: int = 0


def tally(targets: Iterable[int]) -> Tally:
    """Count votes per target. ``leaders`` holds everyone tied for the most votes."""
    counts = Counter(targets)
    if not counts:
        return Tally()
    top = max(counts.values())
    leaders = tuple(sorted(i for i, n in counts.items() if n == top))
    return Tally(dict(counts), leaders, top)


def recap(rounds: Iterable[list[int]]) -> list[tuple[int, int]]:
    """Prompt wins per id, most wins first. A tied prompt is a win for each leader."""
    wins: Counter[int] = Counter()
    for targets in rounds:
        wins.update(tally(targets).leaders)
    return sorted(wins.items(), key=lambda kv: (-kv[1], kv[0]))


def game_winners(wins: Sequence[tuple[int, int]]) -> tuple[int, ...]:
    if not wins:
        return ()
    top = wins[0][1]
    return tuple(i for i, n in wins if n == top)


def next_host(players: Sequence, leaving_id: int):
    """The longest-joined player still in the game, other than the one leaving."""
    remaining = [p for p in players if p["left_at"] is None and p["id"] != leaving_id]
    return min(remaining, key=lambda p: (p["joined_at"], p["id"]), default=None)


def vote_problem(game, rnd, voter, voter_on_roster: bool, target_on_roster: bool) -> str | None:
    """Why this vote can't be taken, or None when it can."""
    if (
        game is None
        or rnd is None
        or game["status"] != "active"
        or rnd["closed_at"]
        or rnd["number"] != game["round"]
    ):
        return CLOSED
    if voter is None:
        return "Only players in this game can vote. Join with `/gaminator mostlikely join`."
    if voter["left_at"] is not None or not voter_on_roster:
        return "You're not voting on this prompt. If you just joined, you're in from the next one."
    if not target_on_roster:
        return "That player isn't on this prompt."
    return None


@dataclass(frozen=True)
class ClosedPrompt:
    """A revealed prompt from a finished game. ``targets`` are the voted-for user ids."""

    prompt: str
    closed_at: str
    targets: tuple[int, ...]


@dataclass(frozen=True)
class Standing:
    user_id: int
    games_won: int
    prompts_won: int
    games_played: int


def standings(
    games: Mapping[int, Sequence[ClosedPrompt]], played: Mapping[int, int]
) -> list[Standing]:
    """All-time standings, best first, from each finished game's revealed prompts."""
    prompt_wins: Counter[int] = Counter()
    game_wins: Counter[int] = Counter()
    for prompts in games.values():
        rounds = [list(p.targets) for p in prompts]
        for targets in rounds:
            prompt_wins.update(tally(targets).leaders)
        game_wins.update(game_winners(recap(rounds)))
    users = set(played) | set(prompt_wins)
    rows = [Standing(u, game_wins[u], prompt_wins[u], played.get(u, 0)) for u in users]
    return sorted(rows, key=lambda s: (-s.games_won, -s.prompts_won, -s.games_played, s.user_id))


def leaderboard_rows(
    rows: Sequence[Standing], caller_id: int, top: int = 10
) -> list[tuple[int, Standing]]:
    """(rank, standing) for the top rows, plus the caller's own row when they rank lower."""
    ranked = list(enumerate(rows, start=1))
    shown = ranked[:top]
    shown += [(rank, s) for rank, s in ranked[top:] if s.user_id == caller_id]
    return shown


def recent_prompt_wins(
    games: Mapping[int, Sequence[ClosedPrompt]], user_id: int, limit: int = 5
) -> list[ClosedPrompt]:
    """The prompts this user won or tied, newest first."""
    won = [p for prompts in games.values() for p in prompts if user_id in tally(p.targets).leaders]
    return sorted(won, key=lambda p: p.closed_at, reverse=True)[:limit]

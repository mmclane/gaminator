"""Pure decisions: who the imposters are, how a vote resolves, and who has won."""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

CREW, IMPOSTERS = "crew", "imposters"


def min_players(imposters: int, override: int | None = None) -> int:
    """Enough players that the crew starts with a majority: 3 for one imposter, 5 for two.

    ``override`` (the MIN_PLAYERS setting, for testing) lowers it, but never below one crew
    member per game.
    """
    if override is not None:
        return max(override, imposters + 1)
    return 2 * imposters + 1


def choose_imposters(player_ids: Sequence[int], count: int, rng: random.Random) -> set[int]:
    if count < 1 or count > len(player_ids):
        raise ValueError("imposter count must be between 1 and the number of players")
    return set(rng.sample(list(player_ids), count))


@dataclass(frozen=True)
class Tally:
    counts: dict[int | None, int] = field(default_factory=dict)
    """Votes per target; the key None is the "nobody" option."""
    ejected: int | None = None
    """The player voted out, or None when nobody was."""
    result: str = "no_votes"
    """'ejected' (someone is out), 'tie', 'nobody', or 'no_votes'."""

    @property
    def leaders(self) -> list[int | None]:
        if not self.counts:
            return []
        top = max(self.counts.values())
        return [t for t, n in self.counts.items() if n == top]


def tally(votes: Iterable[int | None]) -> Tally:
    """Plurality wins. Ties (including with "nobody") eject no one."""
    counts = dict(Counter(votes))
    if not counts:
        return Tally()
    top = max(counts.values())
    leaders = [t for t, n in counts.items() if n == top]
    if len(leaders) > 1:
        return Tally(counts, None, "tie")
    if leaders[0] is None:
        return Tally(counts, None, "nobody")
    return Tally(counts, leaders[0], "ejected")


def outcome(crew_alive: int, imposters_alive: int, rounds_done: int, max_rounds: int) -> str | None:
    """Who has won after a round resolves, or None to keep playing.

    Nobody innocent is ever eliminated. The crew wins when every imposter has been caught. The
    imposters win when ``max_rounds`` rounds have finished with any of them still uncaught, or
    when no crew is left to catch them (everyone else left the game).
    """
    if imposters_alive == 0:
        return CREW
    if crew_alive == 0:
        return IMPOSTERS
    if rounds_done >= max_rounds:
        return IMPOSTERS
    return None

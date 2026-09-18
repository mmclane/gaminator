"""Building the target chain."""

from __future__ import annotations

import random
from collections.abc import Sequence

from .words import pick_word


def build_chain(
    player_ids: Sequence[int], rng: random.Random | None = None
) -> list[tuple[int, int]]:
    """Return (player_id, target_id) pairs forming one random cycle over all players.

    Every player has exactly one target and is targeted by exactly one player; nobody targets
    themselves unless they are the only player.
    """
    rng = rng or random.Random()
    order = list(player_ids)
    rng.shuffle(order)
    return [(order[i], order[(i + 1) % len(order)]) for i in range(len(order))]


def build_assignments(
    player_ids: Sequence[int], words: Sequence[str], rng: random.Random | None = None
) -> list[tuple[int, int, str, str]]:
    """(player_id, target_id, poison_word, bait_word) per player, distinct words where possible."""
    rng = rng or random.Random()
    used: set[str] = set()
    out = []
    for pid, tid in build_chain(player_ids, rng):
        word = pick_word(words, rng, exclude=used)
        used.add(word)
        bait = pick_word(words, rng, exclude=used)
        used.add(bait)
        out.append((pid, tid, word, bait))
    return out

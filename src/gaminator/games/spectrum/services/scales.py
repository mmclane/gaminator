"""Scales: loading the deck, picking scales for a round, rendering one."""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import discord
import yaml

log = logging.getLogger(__name__)
PACKAGE = "gaminator.games.spectrum"
END_LIMIT = 24


@dataclass(frozen=True)
class Scale:
    left: str
    right: str


FALLBACK = (
    Scale("Tiny hiccup", "Total disaster"),
    Scale("Snack", "Feast"),
    Scale("Calm", "Chaotic"),
)


def load_scales(path: str | Path | None) -> list[Scale]:
    """Load ``scales:`` from a YAML file; fall back to the packaged deck, then a built-in few."""
    if path:
        candidate = Path(path)
        if candidate.exists():
            try:
                scales = _parse(yaml.safe_load(candidate.read_text()), str(path))
            except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
                log.warning("spectrum: could not read %s: %s", path, e)
                scales = []
            if scales:
                return scales
    try:
        packaged = files(PACKAGE).joinpath("scales.yaml").read_text()
    except (FileNotFoundError, OSError):
        return list(FALLBACK)
    return _parse(yaml.safe_load(packaged), "the packaged scales") or list(FALLBACK)


def _end(value) -> str | None:
    """One end of a scale with whitespace collapsed. None unless it is 1 to END_LIMIT characters."""
    text = " ".join(value.split()) if isinstance(value, str) else ""
    return text if 0 < len(text) <= END_LIMIT else None


def _parse(data, source: str) -> list[Scale]:
    items = data.get("scales") if isinstance(data, dict) else data
    seen: set[tuple[str, str]] = set()
    out: list[Scale] = []
    skipped = 0
    for item in items if isinstance(items, list) else []:
        left = _end(item.get("left")) if isinstance(item, dict) else None
        right = _end(item.get("right")) if isinstance(item, dict) else None
        if left is None or right is None or left.lower() == right.lower():
            skipped += 1
            continue
        key = (left.lower(), right.lower())
        if key not in seen:
            seen.add(key)
            out.append(Scale(left, right))
    if skipped:
        log.warning(
            "spectrum: skipped %d invalid scales in %s (each needs a left and a right end that "
            "differ, of at most %d characters each)",
            skipped,
            source,
            END_LIMIT,
        )
    return out


def pick_scales(
    scales: Sequence[Scale],
    rng: random.Random,
    count: int,
    exclude: set[Scale] | frozenset[Scale] = frozenset(),
) -> list[Scale]:
    """Exactly ``count`` scales: unused ones first in random order, then used ones.

    Scales repeat only when the deck holds fewer than ``count``.
    """
    fresh = [s for s in scales if s not in exclude]
    used = [s for s in scales if s in exclude]
    rng.shuffle(fresh)
    rng.shuffle(used)
    pool = fresh + used
    if not pool:
        raise ValueError("no scales available")
    picked: list[Scale] = []
    while len(picked) < count:
        picked.extend(pool)
    return picked[:count]


def scale_text(scale: Scale) -> str:
    """A scale on one line, with its ends made harmless as markdown."""
    left = discord.utils.escape_markdown(scale.left)
    right = discord.utils.escape_markdown(scale.right)
    return f"{left} ↔ {right}"

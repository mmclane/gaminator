"""Questions: loading the deck and picking questions for a round."""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

import yaml

log = logging.getLogger(__name__)
PACKAGE = "gaminator.games.headlines"
QUESTION_LIMIT = 100
FALLBACK = (
    "What do you do first thing in the morning?",
    "Describe your ideal weekend in a few words.",
    "What's always in your fridge?",
)


def load_questions(path: str | Path | None) -> list[str]:
    """Load ``questions:`` from a YAML file; fall back to the packaged deck, then a built-in few."""
    if path:
        candidate = Path(path)
        if candidate.exists():
            try:
                questions = _parse(yaml.safe_load(candidate.read_text()), str(path))
            except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
                log.warning("headlines: could not read %s: %s", path, e)
                questions = []
            if questions:
                return questions
    try:
        packaged = files(PACKAGE).joinpath("questions.yaml").read_text()
    except (FileNotFoundError, OSError):
        return list(FALLBACK)
    return _parse(yaml.safe_load(packaged), "the packaged questions") or list(FALLBACK)


def _parse(data, source: str) -> list[str]:
    items = data.get("questions") if isinstance(data, dict) else data
    seen: set[str] = set()
    out: list[str] = []
    skipped = 0
    for item in items if isinstance(items, list) else []:
        text = " ".join(item.split()) if isinstance(item, str) else ""
        if not 0 < len(text) <= QUESTION_LIMIT:
            skipped += 1
            continue
        if text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    if skipped:
        log.warning(
            "headlines: skipped %d invalid questions in %s (each needs to be text of at most "
            "%d characters)",
            skipped,
            source,
            QUESTION_LIMIT,
        )
    return out


def pick_questions(
    questions: Sequence[str],
    rng: random.Random,
    count: int,
    exclude: set[str] | frozenset[str] = frozenset(),
) -> list[str]:
    """Exactly ``count`` questions: unused ones first in random order, then used ones.

    Questions repeat only when the deck holds fewer than ``count``.
    """
    fresh = [q for q in questions if q not in exclude]
    used = [q for q in questions if q in exclude]
    rng.shuffle(fresh)
    rng.shuffle(used)
    pool = fresh + used
    if not pool:
        raise ValueError("no questions available")
    picked: list[str] = []
    while len(picked) < count:
        picked.extend(pool)
    return picked[:count]

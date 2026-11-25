"""Topics and questions: loading the topic deck, picking topics for a round, rendering a question."""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

import discord
import yaml

log = logging.getLogger(__name__)
PACKAGE = "gaminator.games.fiftyfifty"
BLANK = "____"
TOPIC_LIMIT = 40
FALLBACK = ("food", "superpowers", "this server", "road trips", "school", "pets")


def load_topics(path: str | Path | None) -> list[str]:
    """Load ``topics:`` from a YAML file; fall back to the packaged topics, then a built-in few."""
    if path:
        candidate = Path(path)
        if candidate.exists():
            try:
                topics = _parse(yaml.safe_load(candidate.read_text()), str(path))
            except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
                log.warning("fiftyfifty: could not read %s: %s", path, e)
                topics = []
            if topics:
                return topics
            log.warning(
                "fiftyfifty: %s has no usable `topics:` list of short strings, using the "
                "built-in topics",
                path,
            )
    try:
        packaged = files(PACKAGE).joinpath("prompts.yaml").read_text()
    except (FileNotFoundError, OSError):
        return list(FALLBACK)
    return _parse(yaml.safe_load(packaged), "the packaged topics") or list(FALLBACK)


def _parse(data, source: str) -> list[str]:
    items = data.get("topics") if isinstance(data, dict) else data
    seen: set[str] = set()
    out: list[str] = []
    skipped = 0
    for item in items if isinstance(items, list) else []:
        topic = " ".join(item.split()) if isinstance(item, str) else ""
        if not topic or len(topic) > TOPIC_LIMIT:
            skipped += 1
            continue
        if topic.lower() not in seen:
            seen.add(topic.lower())
            out.append(topic)
    if skipped:
        log.warning(
            "fiftyfifty: skipped %d invalid topics in %s (each needs to be text of at most %d "
            "characters)",
            skipped,
            source,
            TOPIC_LIMIT,
        )
    return out


def fill(option: str, answer: str | None = None) -> str:
    """An option as markdown, with whatever a player typed made harmless.

    A question from before players wrote both options has a deck option with a blank and the
    player's ``answer``, which is shown in bold in place of the blank.
    """
    if answer is None:
        return discord.utils.escape_markdown(option)
    return option.replace(BLANK, f"**{discord.utils.escape_markdown(answer)}**")


def question_text(option_a: str, option_b: str, answer: str | None = None) -> str:
    return f"Would you rather {fill(option_a, answer)} or {fill(option_b, answer)}?"


def topic_text(topic: str | None) -> str:
    """The topic line shown to a writer. Empty for a question that has no topic."""
    if not topic:
        return ""
    name = discord.utils.escape_markdown(topic)
    return f"Topic idea: **{name}**. Ignore it if you have something better."


def pick_topics(
    topics: Sequence[str],
    rng: random.Random,
    count: int,
    exclude: set[str] | frozenset[str] = frozenset(),
) -> list[str]:
    """Exactly ``count`` topics: unused ones first in random order, then used ones.

    Topics repeat only when the deck holds fewer than ``count``.
    """
    fresh = [t for t in topics if t not in exclude]
    used = [t for t in topics if t in exclude]
    rng.shuffle(fresh)
    rng.shuffle(used)
    pool = fresh + used
    if not pool:
        raise ValueError("no topics available")
    picked: list[str] = []
    while len(picked) < count:
        picked.extend(pool)
    return picked[:count]

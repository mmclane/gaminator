"""Prompts: loading the deck and picking one for a round."""

from __future__ import annotations

import logging
import random
import re
from collections.abc import Iterable, Sequence
from importlib.resources import files
from pathlib import Path

import yaml

log = logging.getLogger(__name__)
PACKAGE = "gaminator.games.mostlikely"
LEAD_IN = re.compile(r"^most likely to\s+", re.IGNORECASE)
FALLBACK = ("give their car a name", "cry at a commercial", "eat dessert before dinner")


def load_prompts(path: str | Path | None) -> list[str]:
    """Load ``prompts:`` from a YAML file; fall back to the packaged deck, then a built-in one."""
    if path:
        candidate = Path(path)
        if candidate.exists():
            try:
                prompts = _parse(yaml.safe_load(candidate.read_text()))
            except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
                log.warning("mostlikely: could not read %s, using the built-in deck: %s", path, e)
                prompts = []
            if prompts:
                return prompts
    try:
        packaged = files(PACKAGE).joinpath("prompts.yaml").read_text()
    except (FileNotFoundError, OSError):
        return list(FALLBACK)
    return _parse(yaml.safe_load(packaged)) or list(FALLBACK)


def _parse(data) -> list[str]:
    items: Iterable = data.get("prompts", []) if isinstance(data, dict) else (data or [])
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = LEAD_IN.sub("", " ".join(str(item).split())).rstrip(".")
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out


def pick_prompt(prompts: Sequence[str], rng: random.Random, exclude: set[str] = frozenset()) -> str:
    """A random prompt not in ``exclude`` when one exists; otherwise any prompt."""
    pool = [p for p in prompts if p not in exclude] or list(prompts)
    if not pool:
        raise ValueError("no prompts available")
    return rng.choice(pool)

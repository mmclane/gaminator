"""Prompts: loading the list and picking one for a round."""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import yaml

PACKAGE = "gaminator.games.imposter"
FALLBACK = (
    ("Name a food you could happily eat every single day.", "It's about food."),
    ("Post exactly one emoji that sums up your morning.", "Answer with a single emoji."),
    ("In three words, describe how you feel about mornings.", "Answer in exactly three words."),
)


@dataclass(frozen=True)
class Prompt:
    text: str
    hint: str | None = None


def load_prompts(path: str | Path | None) -> list[Prompt]:
    """Load ``prompts:`` from a YAML file; fall back to the packaged list, then a tiny built-in one."""
    if path:
        candidate = Path(path)
        if candidate.exists():
            prompts = _parse(yaml.safe_load(candidate.read_text()))
            if prompts:
                return prompts
    try:
        packaged = files(PACKAGE).joinpath("prompts.yaml").read_text()
    except (FileNotFoundError, OSError):
        return [Prompt(t, h) for t, h in FALLBACK]
    return _parse(yaml.safe_load(packaged)) or [Prompt(t, h) for t, h in FALLBACK]


def _parse(data) -> list[Prompt]:
    items: Iterable = data.get("prompts", []) if isinstance(data, dict) else (data or [])
    seen: set[str] = set()
    out: list[Prompt] = []
    for item in items:
        if isinstance(item, dict):
            text = " ".join(str(item.get("prompt", "")).split())
            hint = item.get("hint")
            hint = " ".join(str(hint).split()) or None if hint is not None else None
        else:
            text, hint = " ".join(str(item).split()), None
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(Prompt(text, hint))
    return out


def pick_prompt(
    prompts: Sequence[Prompt], rng: random.Random, exclude: set[str] = frozenset()
) -> Prompt:
    """A random prompt whose text is not in ``exclude`` when one exists; otherwise any prompt."""
    pool = [p for p in prompts if p.text not in exclude] or list(prompts)
    if not pool:
        raise ValueError("no prompts available")
    return rng.choice(pool)

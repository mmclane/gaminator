"""Poison words: loading the list and matching them in messages."""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Sequence
from importlib.resources import files
from pathlib import Path

import yaml

PACKAGE = "gaminator.games.assassin"
FALLBACK_WORDS = ("banana", "coffee", "weekend", "actually", "pizza", "tomorrow", "movie")


def load_words(path: str | Path | None) -> list[str]:
    """Load ``words:`` from a YAML file; fall back to the packaged list, then a tiny built-in one."""
    if path:
        candidate = Path(path)
        if candidate.exists():
            data = yaml.safe_load(candidate.read_text()) or {}
            words = _clean(data.get("words", []) if isinstance(data, dict) else data)
            if words:
                return words
    try:
        packaged = files(PACKAGE).joinpath("poison_words.yaml").read_text()
    except (FileNotFoundError, OSError):
        return list(FALLBACK_WORDS)
    words = _clean((yaml.safe_load(packaged) or {}).get("words", []))
    return words or list(FALLBACK_WORDS)


def _clean(items: Iterable) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        word = " ".join(str(item).strip().lower().split())
        if word and word not in seen:
            seen.add(word)
            out.append(word)
    return out


def pick_word(words: Sequence[str], rng: random.Random, exclude: set[str] = frozenset()) -> str:
    """A random word not in ``exclude`` when one exists; otherwise any word."""
    pool = [w for w in words if w not in exclude] or list(words)
    if not pool:
        raise ValueError("no poison words available")
    return rng.choice(pool)


def contains_word(text: str, word: str) -> bool:
    """True when ``word`` appears in ``text`` as a whole word or phrase, ignoring case.

    Punctuation and markdown around the word do not matter; ``bananas`` does not match
    ``banana``, but ``BANANA!`` and ``*banana*`` do. Multi-word phrases match across any
    whitespace.
    """
    if not word:
        return False
    parts = [re.escape(p) for p in word.lower().split()]
    pattern = r"(?<![\w])" + r"\s+".join(parts) + r"(?![\w])"
    return re.search(pattern, text.lower()) is not None

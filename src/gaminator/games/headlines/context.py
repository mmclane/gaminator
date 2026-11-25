"""Per-bot state for Headlines: the repo, the question deck, and the lobby card locks."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

import discord

from .repo import HeadlinesRepo

KEY = "headlines"


@dataclass
class HeadlinesContext:
    repo: HeadlinesRepo
    questions: list[str]
    lobby_locks: defaultdict[int, asyncio.Lock] = field(
        default_factory=lambda: defaultdict(asyncio.Lock)
    )


def context(client: discord.Client) -> HeadlinesContext:
    return client.games[KEY]  # type: ignore[attr-defined]

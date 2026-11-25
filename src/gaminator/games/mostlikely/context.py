"""Per-bot state for Most Likely To: the repo, the prompt deck, and the lobby card locks."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

import discord

from .repo import MostLikelyRepo

KEY = "mostlikely"


@dataclass
class MostLikelyContext:
    repo: MostLikelyRepo
    prompts: list[str]
    lobby_locks: defaultdict[int, asyncio.Lock] = field(
        default_factory=lambda: defaultdict(asyncio.Lock)
    )


def context(client: discord.Client) -> MostLikelyContext:
    return client.games[KEY]  # type: ignore[attr-defined]

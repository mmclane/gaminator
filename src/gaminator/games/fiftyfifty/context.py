"""Per-bot state for Fifty-Fifty: the repo, the topic deck, and the lobby card locks."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

import discord

from .repo import FiftyFiftyRepo

KEY = "fiftyfifty"


@dataclass
class FiftyFiftyContext:
    repo: FiftyFiftyRepo
    topics: list[str]
    lobby_locks: defaultdict[int, asyncio.Lock] = field(
        default_factory=lambda: defaultdict(asyncio.Lock)
    )


def context(client: discord.Client) -> FiftyFiftyContext:
    return client.games[KEY]  # type: ignore[attr-defined]

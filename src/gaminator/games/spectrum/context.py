"""Per-bot state for Spectrum: the repo, the scale deck, and the lobby card locks."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

import discord

from .repo import SpectrumRepo
from .services.scales import Scale

KEY = "spectrum"


@dataclass
class SpectrumContext:
    repo: SpectrumRepo
    scales: list[Scale]
    lobby_locks: defaultdict[int, asyncio.Lock] = field(
        default_factory=lambda: defaultdict(asyncio.Lock)
    )


def context(client: discord.Client) -> SpectrumContext:
    return client.games[KEY]  # type: ignore[attr-defined]

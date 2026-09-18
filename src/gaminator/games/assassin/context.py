"""Per-bot state for Assassin: the repo and the poison-word list."""

from __future__ import annotations

from dataclasses import dataclass

import discord

from .repo import AssassinRepo

KEY = "assassin"


@dataclass
class AssassinContext:
    repo: AssassinRepo
    words: list[str]


def context(client: discord.Client) -> AssassinContext:
    return client.games[KEY]  # type: ignore[attr-defined]

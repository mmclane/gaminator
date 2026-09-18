"""Per-bot state for Imposter: the repo and the prompt list."""

from __future__ import annotations

from dataclasses import dataclass

import discord

from .repo import ImposterRepo
from .services.prompts import Prompt

KEY = "imposter"


@dataclass
class ImposterContext:
    repo: ImposterRepo
    prompts: list[Prompt]


def context(client: discord.Client) -> ImposterContext:
    return client.games[KEY]  # type: ignore[attr-defined]

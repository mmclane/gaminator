"""The /gaminator headlines group and the context lookup."""

from __future__ import annotations

import discord
from discord import app_commands

from ..context import HeadlinesContext, context
from ..services.game import require_host_or_admin

__all__ = ["ctx", "headlines", "require_host_or_admin"]

headlines = app_commands.Group(
    name="headlines",
    description="Play Headlines: answer a question, then write the headline for someone's answer",
)


def ctx(interaction: discord.Interaction) -> HeadlinesContext:
    return context(interaction.client)

"""The /gaminator fiftyfifty group and the context lookup."""

from __future__ import annotations

import discord
from discord import app_commands

from ..context import FiftyFiftyContext, context
from ..services.game import require_host_or_admin

__all__ = ["ctx", "fiftyfifty", "require_host_or_admin"]

fiftyfifty = app_commands.Group(
    name="fiftyfifty",
    description="Play Fifty-Fifty: write a would-you-rather the group splits evenly",
)


def ctx(interaction: discord.Interaction) -> FiftyFiftyContext:
    return context(interaction.client)

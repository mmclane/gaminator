"""The /gaminator mostlikely group and the context lookup."""

from __future__ import annotations

import discord
from discord import app_commands

from ..context import MostLikelyContext, context
from ..services.game import require_host_or_admin

__all__ = ["ctx", "mostlikely", "require_host_or_admin"]

mostlikely = app_commands.Group(
    name="mostlikely",
    description="Play Most Likely To: vote on who in the group is most likely to do it",
)


def ctx(interaction: discord.Interaction) -> MostLikelyContext:
    return context(interaction.client)

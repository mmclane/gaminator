"""The group objects shared by the command modules, plus the per-interaction context lookup."""

from __future__ import annotations

import discord
from discord import app_commands

from ..context import ImposterContext, context

imposter = app_commands.Group(
    name="imposter",
    description="Play Imposter: everyone gets the same secret prompt, except the imposter",
    guild_only=True,
)
admin = app_commands.Group(
    name="imposter-admin",
    description="Organize the Imposter game",
    default_permissions=discord.Permissions(manage_guild=True),
    guild_only=True,
)


def ctx(interaction: discord.Interaction) -> ImposterContext:
    return context(interaction.client)

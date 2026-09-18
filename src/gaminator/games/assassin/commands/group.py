"""The group objects shared by the command modules, plus the per-interaction context lookup."""

from __future__ import annotations

import discord
from discord import app_commands

from ..context import AssassinContext, context

assassin = app_commands.Group(
    name="assassin",
    description="Play Assassin: targets, poison words, and quick draws",
    guild_only=True,
)
admin = app_commands.Group(
    name="assassin-admin",
    description="Organize the Assassin game",
    default_permissions=discord.Permissions(manage_guild=True),
    guild_only=True,
)


def ctx(interaction: discord.Interaction) -> AssassinContext:
    return context(interaction.client)

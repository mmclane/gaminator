"""The /gaminator spectrum group and the context lookup."""

from __future__ import annotations

import discord
from discord import app_commands

from ..context import SpectrumContext, context
from ..services.game import require_host_or_admin

__all__ = ["ctx", "require_host_or_admin", "spectrum"]

spectrum = app_commands.Group(
    name="spectrum",
    description="Play Spectrum: give a clue for a secret spot on a scale, or guess it",
)


def ctx(interaction: discord.Interaction) -> SpectrumContext:
    return context(interaction.client)

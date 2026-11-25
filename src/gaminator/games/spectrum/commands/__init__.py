"""The /gaminator spectrum commands.

The group is attached to the core ``/gaminator`` group here, in ``setup``, so any member can use
it. Importing ``player`` registers the commands on the group defined in ``group.py``.
"""

from __future__ import annotations

from discord.ext import commands

from ....core.commands import gaminator
from ..guess import GuessButton
from ..lobby import LobbyButton
from ..turn import TargetButton, WriteClueButton
from . import player  # noqa: F401
from .group import spectrum


async def setup(bot: commands.Bot) -> None:
    gaminator.add_command(spectrum)
    bot.add_dynamic_items(LobbyButton, TargetButton, WriteClueButton, GuessButton)


async def teardown(bot: commands.Bot) -> None:
    gaminator.remove_command(spectrum.name)
    bot.remove_dynamic_items(LobbyButton, TargetButton, WriteClueButton, GuessButton)

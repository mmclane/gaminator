"""The /gaminator fiftyfifty commands.

The group is attached to the core ``/gaminator`` group here, in ``setup``, so any member can use
it. Importing ``player`` registers the commands on the group defined in ``group.py``.
"""

from __future__ import annotations

from discord.ext import commands

from ....core.commands import gaminator
from ..dm import DMButton
from ..lobby import LobbyButton
from ..vote import GuessSelect, VoteButton
from ..write import WriteButton
from . import player  # noqa: F401
from .group import fiftyfifty


async def setup(bot: commands.Bot) -> None:
    gaminator.add_command(fiftyfifty)
    bot.add_dynamic_items(LobbyButton, WriteButton, DMButton, VoteButton, GuessSelect)


async def teardown(bot: commands.Bot) -> None:
    gaminator.remove_command(fiftyfifty.name)
    bot.remove_dynamic_items(LobbyButton, WriteButton, DMButton, VoteButton, GuessSelect)

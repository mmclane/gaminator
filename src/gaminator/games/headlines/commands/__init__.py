"""The /gaminator headlines commands.

The group is attached to the core ``/gaminator`` group here, in ``setup``, so any member can use
it. Importing ``player`` registers the commands on the group defined in ``group.py``.
"""

from __future__ import annotations

from discord.ext import commands

from ....core.commands import gaminator
from ..forms import AnswerButton, WriteButton
from ..lobby import LobbyButton
from ..vote import BallotButton, VoteSelect
from . import player  # noqa: F401
from .group import headlines


async def setup(bot: commands.Bot) -> None:
    gaminator.add_command(headlines)
    bot.add_dynamic_items(LobbyButton, AnswerButton, WriteButton, BallotButton, VoteSelect)


async def teardown(bot: commands.Bot) -> None:
    gaminator.remove_command(headlines.name)
    bot.remove_dynamic_items(LobbyButton, AnswerButton, WriteButton, BallotButton, VoteSelect)

"""The /imposter slash-command group.

Player commands live under ``/imposter``; organizer commands under ``/imposter-admin``, a separate
top-level group so Discord can hide it from members without permission.
Importing the submodules registers their commands on the group defined in ``group.py``.
"""

from __future__ import annotations

from discord.ext import commands

from ..vote import VoteSelect
from . import admin, player  # noqa: F401  (registers the commands)
from .group import admin as admin_group
from .group import imposter


async def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(imposter)
    bot.tree.add_command(admin_group)
    bot.add_dynamic_items(VoteSelect)


async def teardown(bot: commands.Bot) -> None:
    bot.tree.remove_command(imposter.name)
    bot.tree.remove_command(admin_group.name)
    bot.remove_dynamic_items(VoteSelect)

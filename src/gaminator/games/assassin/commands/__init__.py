"""The /assassin slash-command group.

Player commands live under ``/assassin``; organizer commands under ``/assassin-admin``, a separate
top-level group so Discord can hide it from members without permission.
Importing the submodules registers their commands on the group defined in ``group.py``.
"""

from __future__ import annotations

from discord.ext import commands

from . import admin, player  # noqa: F401  (registers the commands)
from .group import admin as admin_group
from .group import assassin


async def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(assassin)
    bot.tree.add_command(admin_group)


async def teardown(bot: commands.Bot) -> None:
    bot.tree.remove_command(assassin.name)
    bot.tree.remove_command(admin_group.name)

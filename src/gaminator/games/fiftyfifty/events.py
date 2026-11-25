"""The Fifty-Fifty sweep and listeners.

The sweep closes writing windows and questions at their deadline and restarts stalled games.
One listener saves every submitted question form; the other reads DMs from players as options.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from .dm import handle_direct_message
from .services.game import close_overdue, resume_stalled
from .write import handle_question_submit

log = logging.getLogger(__name__)
SWEEP_SECONDS = 5


class FiftyFiftyEvents(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sweep.start()

    def cog_unload(self) -> None:
        self.sweep.cancel()

    @tasks.loop(seconds=SWEEP_SECONDS)
    async def sweep(self) -> None:
        try:
            await close_overdue(self.bot)
            await resume_stalled(self.bot)
        except Exception:
            log.exception("fiftyfifty sweep failed")

    @sweep.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        await handle_question_submit(interaction)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await handle_direct_message(self.bot, message)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FiftyFiftyEvents(bot))

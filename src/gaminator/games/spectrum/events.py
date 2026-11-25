"""The Spectrum sweep and listener.

The sweep closes clue steps and guessing at their deadline and restarts stalled games. The
listener saves every submitted clue and guess pop-up.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from .services.game import close_overdue, resume_stalled
from .turn import handle_submit

log = logging.getLogger(__name__)
SWEEP_SECONDS = 5


class SpectrumEvents(commands.Cog):
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
            log.exception("spectrum sweep failed")

    @sweep.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        await handle_submit(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SpectrumEvents(bot))

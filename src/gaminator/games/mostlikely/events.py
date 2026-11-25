"""The Most Likely To sweep: closes prompts at their deadline and restarts stalled games."""

from __future__ import annotations

import logging

from discord.ext import commands, tasks

from .services.game import close_overdue, resume_stalled

log = logging.getLogger(__name__)
SWEEP_SECONDS = 5


class MostLikelyEvents(commands.Cog):
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
            log.exception("mostlikely sweep failed")

    @sweep.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MostLikelyEvents(bot))

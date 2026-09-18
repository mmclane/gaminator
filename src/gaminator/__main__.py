import asyncio
import logging
import signal

import discord

from .bot import Gaminator
from .config import Settings
from .core.db import Database

log = logging.getLogger(__name__)


async def run(settings: Settings) -> None:
    bot = Gaminator(settings, Database(settings.database_path))
    loop = asyncio.get_running_loop()

    def request_shutdown(sig: signal.Signals) -> None:
        log.info("received %s, shutting down", sig.name)
        loop.create_task(bot.close())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, request_shutdown, sig)

    async with bot:
        await bot.start(settings.discord_token)


def main() -> None:
    settings = Settings.from_env()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    discord.utils.setup_logging(level=level, root=True)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()

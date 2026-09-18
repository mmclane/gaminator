import logging
from typing import Any

import discord
from discord.ext import commands

from .config import Settings
from .core.db import Database
from .core.repo import GuildSettingsRepo
from .core.util import handle_command_error
from .games import GAMES, prepare_database

log = logging.getLogger(__name__)
CORE_EXTENSIONS = ["gaminator.core.commands"]


class Gaminator(commands.Bot):
    """One bot, many games.

    ``bot.games[key]`` holds each game's context (its repo plus any loaded resources), built by
    the game's ``GameSpec.make_context``. Games reach shared state through ``bot.guild_settings``
    and ``bot.settings``.
    """

    def __init__(self, settings: Settings, db: Database):
        intents = discord.Intents.default()
        intents.message_content = True  # Assassin reads messages for poison words
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.db = db
        self.guild_settings = GuildSettingsRepo(db)
        self.games: dict[str, Any] = {}
        self.tree.error(handle_command_error)

    async def setup_hook(self) -> None:
        await self.db.connect()
        await prepare_database(self.db)
        for ext in CORE_EXTENSIONS:
            await self.load_extension(ext)
        for spec in GAMES:
            self.games[spec.key] = spec.make_context(self.db, self.settings)
            for ext in spec.extensions:
                await self.load_extension(ext)
            log.info("loaded game %s", spec.title)
        await self._sync_commands()

    async def _sync_commands(self) -> None:
        if not self.settings.guild_ids:
            synced = await self.tree.sync()
            log.info("synced %d commands globally", len(synced))
            return
        for gid in self.settings.guild_ids:
            guild = discord.Object(id=gid)
            self.tree.copy_global_to(guild=guild)
            try:
                synced = await self.tree.sync(guild=guild)
            except discord.Forbidden:
                log.warning(
                    "cannot sync commands to guild %s: the bot is not in that server, or was "
                    "invited without the applications.commands scope. Skipping it.",
                    gid,
                )
                continue
            log.info("synced %d commands to guild %s", len(synced), gid)

    async def on_ready(self) -> None:
        log.info("logged in as %s (%s)", self.user, self.user.id if self.user else "?")

    async def close(self) -> None:
        await self.db.close()
        log.info("database closed")
        await super().close()

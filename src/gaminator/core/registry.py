"""How a game plugs into the bot.

Each game is a package under ``gaminator.games`` that exposes a ``SPEC``. A game owns its own
tables (prefixed with its key), its own ``/<key>`` slash-command group, and a context object
the bot stores in ``bot.games[key]`` for the game's commands and services to use. The context
must have a ``repo`` with ``get_current_game(guild_id)`` so the bot can report what is running.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import Settings
from .db import Database


class GameRepo(Protocol):
    async def get_current_game(self, guild_id: int) -> Any:
        """The server's unfinished game, or None. Rows expose ``name`` and ``status``."""


class GameContext(Protocol):
    repo: GameRepo


@dataclass(frozen=True)
class GameSpec:
    key: str
    """Short identifier: the slash-command group, table prefix, and ``bot.games`` key."""
    title: str
    extensions: tuple[str, ...]
    """discord.py extensions to load (command modules, listener cogs)."""
    make_context: Callable[[Database, Settings], GameContext]
    """Builds the per-game context (repo plus any loaded resources)."""
    migrate: Callable[[Database], Awaitable[None]] | None = None
    """Optional one-off upgrades to run before the schema is applied."""

    @property
    def package(self) -> str:
        return f"gaminator.games.{self.key}"

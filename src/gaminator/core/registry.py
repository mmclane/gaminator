"""How a game plugs into the bot.

Each game is a package under ``gaminator.games`` that exposes a ``SPEC``. A game owns its own
tables (prefixed with its key), its own slash commands (top-level ``/<key>`` groups, or a
subgroup of the public ``/gaminator`` group), and a context object the bot stores in
``bot.games[key]`` for the game's commands and services to use. The context must have a ``repo``
with ``get_current_game(guild_id)`` so the bot can report what is running.
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
    """Short identifier: the table prefix and ``bot.games`` key, and usually the command name."""
    title: str
    emoji: str
    """Shown before the title on the ``/gaminator games`` card."""
    summary: str
    """One or two sentences shown on the ``/gaminator games`` card."""
    start_hint: str
    """How to get a game going, shown on the card."""
    players: str
    """Player count and rough length, shown on the card."""
    extensions: tuple[str, ...]
    """discord.py extensions to load (command modules, listener cogs)."""
    make_context: Callable[[Database, Settings], GameContext]
    """Builds the per-game context (repo plus any loaded resources)."""
    migrate: Callable[[Database], Awaitable[None]] | None = None
    """Optional one-off upgrades to run before the schema is applied."""
    listed: bool = True
    """Whether the game appears on the ``/gaminator games`` card."""

    @property
    def package(self) -> str:
        return f"gaminator.games.{self.key}"

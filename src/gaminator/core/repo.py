"""Shared database access: the repo base class and per-server settings."""

from __future__ import annotations

from collections.abc import Iterable

import aiosqlite

from .db import Database


class RepoError(Exception):
    pass


class AlreadyJoined(RepoError):
    pass


class BaseRepo:
    """Rows are aiosqlite.Row; callers index by column name."""

    def __init__(self, db: Database):
        self.db = db

    async def _one(self, sql: str, params: Iterable = ()) -> aiosqlite.Row | None:
        async with self.db.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, params: Iterable = ()) -> list[aiosqlite.Row]:
        async with self.db.conn.execute(sql, tuple(params)) as cur:
            return list(await cur.fetchall())


class GuildSettingsRepo(BaseRepo):
    async def get_admin_role(self, guild_id: int) -> int | None:
        row = await self._one(
            "SELECT admin_role_id FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        return row["admin_role_id"] if row else None

    async def set_admin_role(self, guild_id: int, role_id: int | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO guild_settings (guild_id, admin_role_id) VALUES (?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET admin_role_id = excluded.admin_role_id",
                (guild_id, role_id),
            )

    async def get_announce_channel(self, guild_id: int) -> int | None:
        row = await self._one(
            "SELECT announce_channel_id FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        return row["announce_channel_id"] if row else None

    async def set_announce_channel(self, guild_id: int, channel_id: int | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO guild_settings (guild_id, announce_channel_id) VALUES (?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET "
                "announce_channel_id = excluded.announce_channel_id",
                (guild_id, channel_id),
            )

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files

import aiosqlite


class Database:
    """Single aiosqlite connection with an explicit, serialized transaction context.

    Reads go straight to ``conn`` (autocommit). Every write, including single statements,
    must run inside ``transaction()`` so implicit and explicit transactions never interleave
    across coroutines sharing this one connection.

    ``connect()`` applies the core schema (shared tables). Each game applies its own with
    ``apply_schema``; see ``gaminator.games.prepare_database``.
    """

    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def connect(self) -> None:
        conn = await aiosqlite.connect(self.path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        self._conn = conn
        await self.apply_schema("gaminator.core")

    async def apply_schema(self, package: str, filename: str = "schema.sql") -> None:
        """Run ``package/filename``; schemas use IF NOT EXISTS so this is idempotent."""
        schema = files(package).joinpath(filename).read_text()
        await self.conn.executescript(schema)

    async def table_names(self) -> set[str]:
        async with self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cur:
            return {row["name"] for row in await cur.fetchall()}

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            conn = self.conn
            await conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                await conn.execute("ROLLBACK")
                raise
            else:
                await conn.execute("COMMIT")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

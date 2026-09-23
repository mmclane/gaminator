"""All Assassin database access. Rows are aiosqlite.Row; callers index by column name."""

from __future__ import annotations

import aiosqlite

from ...core.repo import AlreadyJoined, BaseRepo, RepoError
from ...core.util import now_iso

GAME_STATUSES = ("open", "active", "finished")
CAUSES = (
    "poison",
    "quickdraw",
    "arrested",
    "false_reports",
    "trap",
    "inactive",
    "removed",
    "executed",
    "left",
)
SETTING_COLUMNS = (
    "kill_emoji",
    "shield_emoji",
    "bounty_hours",
    "inactivity_hours",
    "max_wrong_reports",
    "reveal_killer",
    "announce_channel_id",
    "mod_channel_id",
)


class AlreadyDead(RepoError):
    pass


class AssassinRepo(BaseRepo):
    # -- games -----------------------------------------------------------------------------

    async def create_game(self, guild_id: int, name: str, created_by: int) -> aiosqlite.Row:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO assassin_games (guild_id, name, status, created_by, created_at) "
                "VALUES (?, ?, 'open', ?, ?)",
                (guild_id, name, created_by, now_iso()),
            )
            game_id = cur.lastrowid
        game = await self.get_game(game_id)
        assert game is not None
        return game

    async def get_game(self, game_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM assassin_games WHERE id = ?", (game_id,))

    async def get_current_game(self, guild_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM assassin_games WHERE guild_id = ? AND status != 'finished'",
            (guild_id,),
        )

    async def list_active_games(self) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM assassin_games WHERE status = 'active'")

    async def update_settings(self, game_id: int, **fields) -> None:
        bad = set(fields) - set(SETTING_COLUMNS)
        if bad:
            raise ValueError(f"unknown settings: {sorted(bad)}")
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        async with self.db.transaction() as conn:
            await conn.execute(
                f"UPDATE assassin_games SET {assignments} WHERE id = ?",
                (*fields.values(), game_id),
            )

    async def start_game(self, game_id: int, assignments: list[tuple[int, int, str, str]]) -> None:
        """Mark the game active and give every player a target, poison word, and bait word.

        ``assignments`` is a list of (player_id, target_player_id, poison_word, bait_word).
        """
        now = now_iso()
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE assassin_games SET status = 'active', started_at = ? WHERE id = ?",
                (now, game_id),
            )
            await conn.execute(
                "UPDATE assassin_players SET last_message_at = ?, inactivity_warned = 0 "
                "WHERE game_id = ?",
                (now, game_id),
            )
            for player_id, target_id, word, bait in assignments:
                await conn.execute(
                    "UPDATE assassin_players SET target_id = ?, poison_word = ?, bait_word = ?, "
                    "last_kill_at = NULL WHERE id = ?",
                    (target_id, word, bait, player_id),
                )
            await conn.execute(
                "INSERT INTO assassin_events (game_id, kind, created_at) VALUES (?, 'start', ?)",
                (game_id, now),
            )

    async def finish_game(self, game_id: int, winner_id: int | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE assassin_games SET status = 'finished', finished_at = ?, winner_id = ? "
                "WHERE id = ?",
                (now_iso(), winner_id, game_id),
            )
            await conn.execute(
                "INSERT INTO assassin_events (game_id, kind, victim_id, created_at) "
                "VALUES (?, 'finish', ?, ?)",
                (game_id, winner_id, now_iso()),
            )

    # -- channels --------------------------------------------------------------------------

    async def add_channel(self, game_id: int, channel_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO assassin_channels (game_id, channel_id) VALUES (?, ?)",
                (game_id, channel_id),
            )

    async def remove_channel(self, game_id: int, channel_id: int) -> bool:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM assassin_channels WHERE game_id = ? AND channel_id = ?",
                (game_id, channel_id),
            )
            return cur.rowcount > 0

    async def clear_channels(self, game_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("DELETE FROM assassin_channels WHERE game_id = ?", (game_id,))

    async def list_channels(self, game_id: int) -> list[int]:
        rows = await self._all(
            "SELECT channel_id FROM assassin_channels WHERE game_id = ? ORDER BY channel_id",
            (game_id,),
        )
        return [r["channel_id"] for r in rows]

    # -- players ---------------------------------------------------------------------------

    async def add_player(self, game_id: int, user_id: int) -> aiosqlite.Row:
        async with self.db.transaction() as conn:
            try:
                await conn.execute(
                    "INSERT INTO assassin_players (game_id, user_id, joined_at) VALUES (?, ?, ?)",
                    (game_id, user_id, now_iso()),
                )
            except aiosqlite.IntegrityError as e:
                raise AlreadyJoined() from e
        player = await self.get_player(game_id, user_id)
        assert player is not None
        return player

    async def delete_player(self, player_id: int) -> None:
        """Remove a player from a game that has not started."""
        async with self.db.transaction() as conn:
            await conn.execute("DELETE FROM assassin_players WHERE id = ?", (player_id,))

    async def get_player(self, game_id: int, user_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM assassin_players WHERE game_id = ? AND user_id = ?", (game_id, user_id)
        )

    async def get_player_by_id(self, player_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM assassin_players WHERE id = ?", (player_id,))

    async def get_hunter(self, player_id: int) -> aiosqlite.Row | None:
        """The living player whose target is ``player_id``."""
        return await self._one(
            "SELECT * FROM assassin_players WHERE target_id = ? AND alive = 1", (player_id,)
        )

    async def list_players(self, game_id: int, alive_only: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM assassin_players WHERE game_id = ?"
        if alive_only:
            sql += " AND alive = 1"
        return await self._all(sql + " ORDER BY joined_at, id", (game_id,))

    async def alive_count(self, game_id: int) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS n FROM assassin_players WHERE game_id = ? AND alive = 1",
            (game_id,),
        )
        return row["n"] if row else 0

    async def touch_activity(self, game_id: int, user_id: int, when: str | None = None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE assassin_players SET last_message_at = ?, inactivity_warned = 0 "
                "WHERE game_id = ? AND user_id = ? AND alive = 1",
                (when or now_iso(), game_id, user_id),
            )

    async def mark_warned(self, player_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE assassin_players SET inactivity_warned = 1 WHERE id = ?", (player_id,)
            )

    async def add_wrong_report(self, player_id: int) -> int:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE assassin_players SET wrong_reports = wrong_reports + 1 WHERE id = ?",
                (player_id,),
            )
        row = await self.get_player_by_id(player_id)
        return row["wrong_reports"] if row else 0

    async def eliminate(
        self,
        victim_id: int,
        by_player_id: int | None,
        cause: str,
        new_word: str,
        new_bait: str | None = None,
        detail: str | None = None,
    ) -> aiosqlite.Row | None:
        """Kill ``victim`` and close the gap in the chain.

        The victim's hunter (the living player targeting them) inherits the victim's target and
        gets ``new_word`` as their poison word. If that would make the hunter their own target,
        the hunter's target is cleared instead: they are the last one standing.

        ``by_player_id`` gets a kill credited if given. Returns the hunter's refreshed row, or
        None when no living hunter exists (game not started, or the victim was the last player).
        """
        if cause not in CAUSES:
            raise ValueError(f"unknown cause {cause!r}")
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT * FROM assassin_players WHERE id = ?", (victim_id,)
            ) as cur:
                victim = await cur.fetchone()
            if victim is None or not victim["alive"]:
                raise AlreadyDead()
            async with conn.execute(
                "SELECT * FROM assassin_players WHERE target_id = ? AND alive = 1", (victim_id,)
            ) as cur:
                hunter = await cur.fetchone()
            await conn.execute(
                "UPDATE assassin_players SET alive = 0, eliminated_at = ?, eliminated_by = ?, "
                "cause = ?, target_id = NULL WHERE id = ?",
                (now_iso(), by_player_id, cause, victim_id),
            )
            if hunter is not None:
                new_target = victim["target_id"]
                if new_target == hunter["id"]:
                    new_target = None
                await conn.execute(
                    "UPDATE assassin_players SET target_id = ?, poison_word = ?, bait_word = ? "
                    "WHERE id = ?",
                    (
                        new_target,
                        new_word if new_target else None,
                        (new_bait or new_word) if new_target else None,
                        hunter["id"],
                    ),
                )
            if by_player_id is not None:
                await conn.execute(
                    "UPDATE assassin_players SET kills = kills + 1, last_kill_at = ? WHERE id = ?",
                    (now_iso(), by_player_id),
                )
            # any elimination clears an open bounty
            await conn.execute(
                "UPDATE assassin_games SET bounty_player_id = NULL WHERE id = ?",
                (victim["game_id"],),
            )
            await conn.execute(
                "INSERT INTO assassin_events "
                "(game_id, kind, actor_id, victim_id, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (victim["game_id"], cause, by_player_id, victim_id, detail, now_iso()),
            )
        return await self.get_player_by_id(hunter["id"]) if hunter is not None else None

    # -- bounty ----------------------------------------------------------------------------

    async def last_action_at(self, game_id: int) -> str | None:
        """When the game last changed: start, an elimination, or a bounty being placed."""
        row = await self._one(
            "SELECT created_at FROM assassin_events "
            "WHERE game_id = ? AND kind != 'false_report' ORDER BY id DESC LIMIT 1",
            (game_id,),
        )
        return row["created_at"] if row else None

    async def bounty_candidate(self, game_id: int) -> aiosqlite.Row | None:
        """The living player who has gone longest without a kill."""
        return await self._one(
            "SELECT * FROM assassin_players WHERE game_id = ? AND alive = 1 "
            "ORDER BY COALESCE(last_kill_at, ''), joined_at, id LIMIT 1",
            (game_id,),
        )

    async def set_bounty(self, game_id: int, player_id: int | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE assassin_games SET bounty_player_id = ? WHERE id = ?", (player_id, game_id)
            )
            await conn.execute(
                "INSERT INTO assassin_events (game_id, kind, victim_id, created_at) "
                "VALUES (?, 'bounty', ?, ?)",
                (game_id, player_id, now_iso()),
            )

    # -- events ----------------------------------------------------------------------------

    async def log_event(
        self,
        game_id: int,
        kind: str,
        actor_id: int | None = None,
        victim_id: int | None = None,
        detail: str | None = None,
    ) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO assassin_events "
                "(game_id, kind, actor_id, victim_id, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (game_id, kind, actor_id, victim_id, detail, now_iso()),
            )

    async def list_events(self, game_id: int, limit: int = 25) -> list[aiosqlite.Row]:
        return await self._all(
            "SELECT * FROM assassin_events WHERE game_id = ? ORDER BY id DESC LIMIT ?",
            (game_id, limit),
        )

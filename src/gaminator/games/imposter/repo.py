"""All Imposter database access. Rows are aiosqlite.Row; callers index by column name."""

from __future__ import annotations

from collections.abc import Iterable

import aiosqlite

from ...core.repo import AlreadyJoined, BaseRepo, RepoError
from ...core.util import now_iso

SETTING_COLUMNS = ("imposters", "max_rounds", "imposter_hint")
DEFAULT_MAX_ROUNDS = 5
RESULTS = ("imposter", "innocent", "tie", "nobody", "no_votes")
CAUSES = ("caught", "left", "removed")


class AlreadyOut(RepoError):
    pass


class ImposterRepo(BaseRepo):
    # -- games -----------------------------------------------------------------------------

    async def create_game(self, guild_id: int, name: str, created_by: int) -> aiosqlite.Row:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO imposter_games "
                "(guild_id, name, status, max_rounds, created_by, created_at) "
                "VALUES (?, ?, 'open', ?, ?, ?)",
                (guild_id, name, DEFAULT_MAX_ROUNDS, created_by, now_iso()),
            )
            game_id = cur.lastrowid
        game = await self.get_game(game_id)
        assert game is not None
        return game

    async def get_game(self, game_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM imposter_games WHERE id = ?", (game_id,))

    async def get_current_game(self, guild_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM imposter_games WHERE guild_id = ? AND status != 'finished'",
            (guild_id,),
        )

    async def update_settings(self, game_id: int, **fields) -> None:
        bad = set(fields) - set(SETTING_COLUMNS)
        if bad:
            raise ValueError(f"unknown settings: {sorted(bad)}")
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        async with self.db.transaction() as conn:
            await conn.execute(
                f"UPDATE imposter_games SET {assignments} WHERE id = ?",
                (*fields.values(), game_id),
            )

    async def set_channel(self, game_id: int, channel_id: int | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE imposter_games SET channel_id = ? WHERE id = ?", (channel_id, game_id)
            )

    async def set_next_prompt(self, game_id: int, prompt: str | None, hint: str | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE imposter_games SET next_prompt = ?, next_hint = ? WHERE id = ?",
                (prompt, hint, game_id),
            )

    async def start_game(self, game_id: int, channel_id: int, imposter_ids: Iterable[int]) -> None:
        """Mark the game active in ``channel_id`` and flag the chosen players as imposters."""
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE imposter_games SET status = 'active', phase = 'discussion', "
                "started_at = ?, channel_id = ? WHERE id = ?",
                (now_iso(), channel_id, game_id),
            )
            await conn.execute(
                "UPDATE imposter_players SET imposter = 0 WHERE game_id = ?", (game_id,)
            )
            for pid in imposter_ids:
                await conn.execute(
                    "UPDATE imposter_players SET imposter = 1 WHERE id = ? AND game_id = ?",
                    (pid, game_id),
                )

    async def finish_game(self, game_id: int, winner: str | None = None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE imposter_games SET status = 'finished', phase = 'over', finished_at = ?, "
                "winner = ? WHERE id = ?",
                (now_iso(), winner, game_id),
            )

    # -- rounds ----------------------------------------------------------------------------

    async def begin_round(self, game_id: int, prompt: str, hint: str | None) -> aiosqlite.Row:
        """Open the next round with ``prompt`` and clear any organizer override."""
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT round FROM imposter_games WHERE id = ?", (game_id,)
            ) as cur:
                row = await cur.fetchone()
            number = (row["round"] if row else 0) + 1
            cur = await conn.execute(
                "INSERT INTO imposter_rounds (game_id, number, prompt, hint, started_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (game_id, number, prompt, hint, now_iso()),
            )
            round_id = cur.lastrowid
            await conn.execute(
                "UPDATE imposter_games SET round = ?, phase = 'discussion', "
                "next_prompt = NULL, next_hint = NULL WHERE id = ?",
                (number, game_id),
            )
        rnd = await self.get_round(round_id)
        assert rnd is not None
        return rnd

    async def reroll_round(self, round_id: int, prompt: str, hint: str | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE imposter_rounds SET prompt = ?, hint = ? WHERE id = ?",
                (prompt, hint, round_id),
            )

    async def get_round(self, round_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM imposter_rounds WHERE id = ?", (round_id,))

    async def get_current_round(self, game_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM imposter_rounds WHERE game_id = ? ORDER BY number DESC LIMIT 1",
            (game_id,),
        )

    async def list_rounds(self, game_id: int) -> list[aiosqlite.Row]:
        return await self._all(
            "SELECT * FROM imposter_rounds WHERE game_id = ? ORDER BY number", (game_id,)
        )

    async def used_prompts(self, game_id: int) -> set[str]:
        rows = await self._all("SELECT prompt FROM imposter_rounds WHERE game_id = ?", (game_id,))
        return {r["prompt"] for r in rows}

    async def open_voting(self, round_id: int, channel_id: int, message_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE imposter_rounds SET vote_channel_id = ?, vote_message_id = ?, "
                "voting_opened_at = ? WHERE id = ?",
                (channel_id, message_id, now_iso(), round_id),
            )
            await conn.execute(
                "UPDATE imposter_games SET phase = 'voting' "
                "WHERE id = (SELECT game_id FROM imposter_rounds WHERE id = ?)",
                (round_id,),
            )

    async def close_round(self, round_id: int, ejected_id: int | None, result: str) -> bool:
        """Record the outcome. False when the round was already closed (lost a race)."""
        if result not in RESULTS:
            raise ValueError(f"unknown result {result!r}")
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE imposter_rounds SET closed_at = ?, ejected_id = ?, result = ? "
                "WHERE id = ? AND closed_at IS NULL",
                (now_iso(), ejected_id, result, round_id),
            )
            if cur.rowcount == 0:
                return False
            await conn.execute(
                "UPDATE imposter_games SET phase = 'discussion' "
                "WHERE id = (SELECT game_id FROM imposter_rounds WHERE id = ?)",
                (round_id,),
            )
            return True

    # -- votes -----------------------------------------------------------------------------

    async def cast_vote(self, round_id: int, voter_id: int, target_id: int | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO imposter_votes (round_id, voter_id, target_id, cast_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(round_id, voter_id) DO UPDATE SET "
                "target_id = excluded.target_id, cast_at = excluded.cast_at",
                (round_id, voter_id, target_id, now_iso()),
            )

    async def list_votes(self, round_id: int) -> list[aiosqlite.Row]:
        return await self._all(
            "SELECT * FROM imposter_votes WHERE round_id = ? ORDER BY cast_at", (round_id,)
        )

    async def get_vote(self, round_id: int, voter_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM imposter_votes WHERE round_id = ? AND voter_id = ?",
            (round_id, voter_id),
        )

    async def living_vote_count(self, round_id: int) -> int:
        """Votes cast by players who are still alive (a voter eliminated mid-vote drops out)."""
        row = await self._one(
            "SELECT COUNT(*) AS n FROM imposter_votes v "
            "JOIN imposter_players p ON p.id = v.voter_id "
            "WHERE v.round_id = ? AND p.alive = 1",
            (round_id,),
        )
        return row["n"] if row else 0

    # -- players ---------------------------------------------------------------------------

    async def add_player(self, game_id: int, user_id: int) -> aiosqlite.Row:
        async with self.db.transaction() as conn:
            try:
                await conn.execute(
                    "INSERT INTO imposter_players (game_id, user_id, joined_at) VALUES (?, ?, ?)",
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
            await conn.execute("DELETE FROM imposter_players WHERE id = ?", (player_id,))

    async def get_player(self, game_id: int, user_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM imposter_players WHERE game_id = ? AND user_id = ?", (game_id, user_id)
        )

    async def get_player_by_id(self, player_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM imposter_players WHERE id = ?", (player_id,))

    async def list_players(self, game_id: int, alive_only: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM imposter_players WHERE game_id = ?"
        if alive_only:
            sql += " AND alive = 1"
        return await self._all(sql + " ORDER BY joined_at, id", (game_id,))

    async def alive_counts(self, game_id: int) -> tuple[int, int]:
        """(living crew, living imposters)."""
        row = await self._one(
            "SELECT SUM(CASE WHEN imposter = 0 THEN 1 ELSE 0 END) AS crew, "
            "SUM(CASE WHEN imposter = 1 THEN 1 ELSE 0 END) AS imposters "
            "FROM imposter_players WHERE game_id = ? AND alive = 1",
            (game_id,),
        )
        if row is None:
            return 0, 0
        return row["crew"] or 0, row["imposters"] or 0

    async def eliminate(self, player_id: int, round_number: int | None, cause: str) -> None:
        if cause not in CAUSES:
            raise ValueError(f"unknown cause {cause!r}")
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE imposter_players SET alive = 0, eliminated_round = ?, cause = ? "
                "WHERE id = ? AND alive = 1",
                (round_number, cause, player_id),
            )
            if cur.rowcount == 0:
                raise AlreadyOut()

"""All Most Likely To database access. Rows are aiosqlite.Row; callers index by column name."""

from __future__ import annotations

import aiosqlite

from ...core.repo import AlreadyJoined, BaseRepo
from ...core.util import GameError, now_iso
from .services.rules import MAX_PLAYERS, ClosedPrompt

DEFAULT_MAX_ROUNDS = 10


class GameFull(GameError):
    def __init__(self) -> None:
        super().__init__(f"The game is full ({MAX_PLAYERS} players).")


class MostLikelyRepo(BaseRepo):
    async def create_game(
        self, guild_id: int, name: str, created_by: int, channel_id: int
    ) -> aiosqlite.Row:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO mostlikely_games "
                "(guild_id, name, status, max_rounds, channel_id, created_by, host_id, created_at) "
                "VALUES (?, ?, 'open', ?, ?, ?, ?, ?)",
                (guild_id, name, DEFAULT_MAX_ROUNDS, channel_id, created_by, created_by, now_iso()),
            )
            game_id = cur.lastrowid
        game = await self.get_game(game_id)
        assert game is not None
        return game

    async def get_game(self, game_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM mostlikely_games WHERE id = ?", (game_id,))

    async def get_current_game(self, guild_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM mostlikely_games WHERE guild_id = ? AND status != 'finished'",
            (guild_id,),
        )

    async def start_game(self, game_id: int, max_rounds: int) -> bool:
        """Mark an open game active. False when it was not open (already started or ended)."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE mostlikely_games SET status = 'active', started_at = ?, max_rounds = ? "
                "WHERE id = ? AND status = 'open'",
                (now_iso(), max_rounds, game_id),
            )
            return cur.rowcount == 1

    async def set_host(self, game_id: int, user_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE mostlikely_games SET host_id = ? WHERE id = ?", (user_id, game_id)
            )

    async def set_lobby_message(self, game_id: int, message_id: int | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE mostlikely_games SET lobby_message_id = ? WHERE id = ?",
                (message_id, game_id),
            )

    async def finish_game(self, game_id: int) -> bool:
        """Finish the game and abandon any open round. False when it was already finished."""
        stamp = now_iso()
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE mostlikely_games SET status = 'finished', finished_at = ? "
                "WHERE id = ? AND status != 'finished'",
                (stamp, game_id),
            )
            if cur.rowcount == 0:
                return False
            await conn.execute(
                "UPDATE mostlikely_rounds SET closed_at = ?, abandoned = 1 "
                "WHERE game_id = ? AND closed_at IS NULL",
                (stamp, game_id),
            )
            return True

    async def join(self, game_id: int, user_id: int) -> tuple[aiosqlite.Row, bool]:
        """Add a player, or bring back one who left. The bool is True for a rejoin.

        Raises AlreadyJoined when the player is in the game, GameFull at the player cap.
        """
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT * FROM mostlikely_players WHERE game_id = ? AND user_id = ?",
                (game_id, user_id),
            ) as cur:
                existing = await cur.fetchone()
            if existing is not None and existing["left_at"] is None:
                raise AlreadyJoined()
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM mostlikely_players "
                "WHERE game_id = ? AND left_at IS NULL",
                (game_id,),
            ) as cur:
                active = (await cur.fetchone())["n"]
            if active >= MAX_PLAYERS:
                raise GameFull()
            if existing is None:
                await conn.execute(
                    "INSERT INTO mostlikely_players (game_id, user_id, joined_at) VALUES (?, ?, ?)",
                    (game_id, user_id, now_iso()),
                )
            else:
                await conn.execute(
                    "UPDATE mostlikely_players SET left_at = NULL, joined_at = ? WHERE id = ?",
                    (now_iso(), existing["id"]),
                )
        player = await self.get_player(game_id, user_id)
        assert player is not None
        return player, existing is not None

    async def leave(self, player_id: int) -> bool:
        """Mark a player as out. False when they had already left."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE mostlikely_players SET left_at = ? WHERE id = ? AND left_at IS NULL",
                (now_iso(), player_id),
            )
            return cur.rowcount == 1

    async def get_player(self, game_id: int, user_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM mostlikely_players WHERE game_id = ? AND user_id = ?",
            (game_id, user_id),
        )

    async def get_player_by_id(self, player_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM mostlikely_players WHERE id = ?", (player_id,))

    async def list_players(self, game_id: int, active_only: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM mostlikely_players WHERE game_id = ?"
        if active_only:
            sql += " AND left_at IS NULL"
        return await self._all(sql + " ORDER BY joined_at, id", (game_id,))

    async def begin_round(
        self, game_id: int, prompt: str, deadline_at: str
    ) -> aiosqlite.Row | None:
        """Open the next prompt and record who is on it.

        Returns None, writing nothing, unless the game is active and has no open round.
        """
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT g.round FROM mostlikely_games g WHERE g.id = ? AND g.status = 'active' "
                "AND NOT EXISTS (SELECT 1 FROM mostlikely_rounds r "
                "WHERE r.game_id = g.id AND r.closed_at IS NULL)",
                (game_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            number = row["round"] + 1
            cur = await conn.execute(
                "INSERT INTO mostlikely_rounds (game_id, number, prompt, started_at, deadline_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (game_id, number, prompt, now_iso(), deadline_at),
            )
            round_id = cur.lastrowid
            await conn.execute(
                "INSERT INTO mostlikely_round_players (round_id, player_id) "
                "SELECT ?, id FROM mostlikely_players WHERE game_id = ? AND left_at IS NULL",
                (round_id, game_id),
            )
            await conn.execute(
                "UPDATE mostlikely_games SET round = ? WHERE id = ?", (number, game_id)
            )
        rnd = await self.get_round(round_id)
        assert rnd is not None
        return rnd

    async def get_round(self, round_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM mostlikely_rounds WHERE id = ?", (round_id,))

    async def get_current_round(self, game_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM mostlikely_rounds WHERE game_id = ? ORDER BY number DESC LIMIT 1",
            (game_id,),
        )

    async def used_prompts(self, game_id: int) -> set[str]:
        rows = await self._all("SELECT prompt FROM mostlikely_rounds WHERE game_id = ?", (game_id,))
        return {r["prompt"] for r in rows}

    async def round_roster(self, round_id: int) -> list[aiosqlite.Row]:
        """Players who were in the game when the prompt was posted, whether or not they left."""
        return await self._all(
            "SELECT p.* FROM mostlikely_round_players rp "
            "JOIN mostlikely_players p ON p.id = rp.player_id "
            "WHERE rp.round_id = ? ORDER BY p.joined_at, p.id",
            (round_id,),
        )

    async def on_roster(self, round_id: int, player_id: int) -> bool:
        row = await self._one(
            "SELECT 1 FROM mostlikely_round_players WHERE round_id = ? AND player_id = ?",
            (round_id, player_id),
        )
        return row is not None

    async def set_vote_message(self, round_id: int, channel_id: int, message_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE mostlikely_rounds SET vote_channel_id = ?, vote_message_id = ? "
                "WHERE id = ?",
                (channel_id, message_id, round_id),
            )

    async def close_round(self, round_id: int) -> bool:
        """Close a prompt for its reveal. False when it was already closed (lost a race)."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE mostlikely_rounds SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
                (now_iso(), round_id),
            )
            return cur.rowcount == 1

    async def abandon_round(self, round_id: int) -> bool:
        """Close a prompt without a reveal, so it counts for nothing. False if already closed."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE mostlikely_rounds SET closed_at = ?, abandoned = 1 "
                "WHERE id = ? AND closed_at IS NULL",
                (now_iso(), round_id),
            )
            return cur.rowcount == 1

    async def list_overdue_rounds(self, now: str) -> list[aiosqlite.Row]:
        """Open prompts of running games whose deadline is at or before ``now``."""
        return await self._all(
            "SELECT r.* FROM mostlikely_rounds r JOIN mostlikely_games g ON g.id = r.game_id "
            "WHERE r.closed_at IS NULL AND r.deadline_at <= ? AND g.status = 'active' "
            "AND r.number = g.round",
            (now,),
        )

    async def list_stalled_games(self, before: str) -> list[aiosqlite.Row]:
        """Running games with no open prompt since ``before``: a closed one, or none yet."""
        return await self._all(
            "SELECT g.* FROM mostlikely_games g "
            "LEFT JOIN mostlikely_rounds r ON r.game_id = g.id AND r.number = g.round "
            "WHERE g.status = 'active' AND (r.id IS NULL OR r.closed_at IS NOT NULL) "
            "AND COALESCE(r.closed_at, g.started_at) <= ?",
            (before,),
        )

    async def cast_vote(self, round_id: int, voter_id: int, target_id: int) -> bool:
        """Record or change a vote. False, writing nothing, when the prompt has closed."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO mostlikely_votes (round_id, voter_id, target_id, cast_at) "
                "SELECT ?, ?, ?, ? WHERE EXISTS "
                "(SELECT 1 FROM mostlikely_rounds WHERE id = ? AND closed_at IS NULL) "
                "ON CONFLICT(round_id, voter_id) DO UPDATE SET "
                "target_id = excluded.target_id, cast_at = excluded.cast_at",
                (round_id, voter_id, target_id, now_iso(), round_id),
            )
            return cur.rowcount == 1

    async def get_vote(self, round_id: int, voter_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM mostlikely_votes WHERE round_id = ? AND voter_id = ?",
            (round_id, voter_id),
        )

    async def list_votes(self, round_id: int) -> list[aiosqlite.Row]:
        return await self._all(
            "SELECT * FROM mostlikely_votes WHERE round_id = ? ORDER BY cast_at", (round_id,)
        )

    async def vote_progress(self, round_id: int) -> tuple[int, int]:
        """(votes in, voters expected), counting only roster players who are still in the game."""
        row = await self._one(
            "SELECT COUNT(*) AS eligible, COUNT(v.voter_id) AS voted "
            "FROM mostlikely_round_players rp "
            "JOIN mostlikely_players p ON p.id = rp.player_id "
            "LEFT JOIN mostlikely_votes v ON v.round_id = rp.round_id AND v.voter_id = rp.player_id "
            "WHERE rp.round_id = ? AND p.left_at IS NULL",
            (round_id,),
        )
        return (row["voted"], row["eligible"]) if row else (0, 0)

    async def closed_round_votes(self, game_id: int) -> list[list[int]]:
        """Voted-for player ids per revealed prompt, in prompt order. Abandoned prompts are left out."""
        rows = await self._all(
            "SELECT r.id AS round_id, v.target_id FROM mostlikely_rounds r "
            "LEFT JOIN mostlikely_votes v ON v.round_id = r.id "
            "WHERE r.game_id = ? AND r.closed_at IS NOT NULL AND r.abandoned = 0 "
            "ORDER BY r.number",
            (game_id,),
        )
        by_round: dict[int, list[int]] = {}
        for row in rows:
            targets = by_round.setdefault(row["round_id"], [])
            if row["target_id"] is not None:
                targets.append(row["target_id"])
        return list(by_round.values())

    async def finished_prompts(self, guild_id: int) -> dict[int, list[ClosedPrompt]]:
        """Revealed prompts of this server's finished games, by game id, with voted-for user ids."""
        rows = await self._all(
            "SELECT g.id AS game_id, r.id AS round_id, r.prompt, r.closed_at, p.user_id "
            "FROM mostlikely_games g "
            "JOIN mostlikely_rounds r ON r.game_id = g.id "
            "AND r.closed_at IS NOT NULL AND r.abandoned = 0 "
            "LEFT JOIN mostlikely_votes v ON v.round_id = r.id "
            "LEFT JOIN mostlikely_players p ON p.id = v.target_id "
            "WHERE g.guild_id = ? AND g.status = 'finished' "
            "ORDER BY g.id, r.number",
            (guild_id,),
        )
        targets: dict[int, list[int]] = {}
        info: dict[int, tuple[int, str, str]] = {}
        for row in rows:
            info[row["round_id"]] = (row["game_id"], row["prompt"], row["closed_at"])
            votes = targets.setdefault(row["round_id"], [])
            if row["user_id"] is not None:
                votes.append(row["user_id"])
        games: dict[int, list[ClosedPrompt]] = {}
        for round_id, (game_id, prompt, closed_at) in info.items():
            games.setdefault(game_id, []).append(
                ClosedPrompt(prompt, closed_at, tuple(targets[round_id]))
            )
        return games

    async def games_played(self, guild_id: int) -> dict[int, int]:
        """Finished games per user id, counting a game when the user was on a revealed prompt."""
        rows = await self._all(
            "SELECT p.user_id, COUNT(DISTINCT g.id) AS played "
            "FROM mostlikely_games g "
            "JOIN mostlikely_rounds r ON r.game_id = g.id "
            "AND r.closed_at IS NOT NULL AND r.abandoned = 0 "
            "JOIN mostlikely_round_players rp ON rp.round_id = r.id "
            "JOIN mostlikely_players p ON p.id = rp.player_id "
            "WHERE g.guild_id = ? AND g.status = 'finished' "
            "GROUP BY p.user_id",
            (guild_id,),
        )
        return {r["user_id"]: r["played"] for r in rows}

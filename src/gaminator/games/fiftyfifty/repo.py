"""All Fifty-Fifty database access. Rows are aiosqlite.Row; callers index by column name."""

from __future__ import annotations

import random
from collections.abc import Sequence

import aiosqlite

from ...core.repo import AlreadyJoined, BaseRepo
from ...core.util import GameError, now_iso
from .services.rules import DEFAULT_ROUNDS, MAX_PLAYERS, ScoredQuestion

QUESTION = (
    "SELECT q.*, r.number AS round_number, r.game_id FROM fiftyfifty_questions q "
    "JOIN fiftyfifty_rounds r ON r.id = q.round_id "
)
ELIGIBLE_VOTERS = (
    "(SELECT COUNT(*) FROM fiftyfifty_round_players rp "
    "JOIN fiftyfifty_players p ON p.id = rp.player_id "
    "WHERE rp.round_id = q.round_id AND p.left_at IS NULL AND p.id != q.writer_id)"
)
VOTES = "(SELECT COUNT(*) FROM fiftyfifty_votes v WHERE v.question_id = q.id{choice})"


def _scored(row, guesses: dict[int, tuple[tuple[int, int], ...]]) -> ScoredQuestion:
    return ScoredQuestion(
        row["user_id"],
        row["option_a"],
        row["option_b"],
        row["answer"],
        row["a"],
        row["b"],
        row["closed_at"],
        guesses.get(row["id"], ()),
    )


class GameFull(GameError):
    def __init__(self) -> None:
        super().__init__(f"The game is full ({MAX_PLAYERS} players).")


class FiftyFiftyRepo(BaseRepo):
    async def create_game(
        self, guild_id: int, name: str, created_by: int, channel_id: int
    ) -> aiosqlite.Row:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO fiftyfifty_games "
                "(guild_id, name, status, max_rounds, channel_id, created_by, host_id, created_at) "
                "VALUES (?, ?, 'open', ?, ?, ?, ?, ?)",
                (guild_id, name, DEFAULT_ROUNDS, channel_id, created_by, created_by, now_iso()),
            )
            game_id = cur.lastrowid
        game = await self.get_game(game_id)
        assert game is not None
        return game

    async def get_game(self, game_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM fiftyfifty_games WHERE id = ?", (game_id,))

    async def get_current_game(self, guild_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM fiftyfifty_games WHERE guild_id = ? AND status != 'finished'",
            (guild_id,),
        )

    async def start_game(self, game_id: int, max_rounds: int) -> bool:
        """Mark an open game active. False when it was not open (already started or ended)."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE fiftyfifty_games SET status = 'active', started_at = ?, max_rounds = ? "
                "WHERE id = ? AND status = 'open'",
                (now_iso(), max_rounds, game_id),
            )
            return cur.rowcount == 1

    async def set_host(self, game_id: int, user_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE fiftyfifty_games SET host_id = ? WHERE id = ?", (user_id, game_id)
            )

    async def set_lobby_message(self, game_id: int, message_id: int | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE fiftyfifty_games SET lobby_message_id = ? WHERE id = ?",
                (message_id, game_id),
            )

    async def finish_game(self, game_id: int) -> bool:
        """Finish the game, abandon an open question and close an open round.

        False when it was already finished.
        """
        stamp = now_iso()
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE fiftyfifty_games SET status = 'finished', finished_at = ? "
                "WHERE id = ? AND status != 'finished'",
                (stamp, game_id),
            )
            if cur.rowcount == 0:
                return False
            await conn.execute(
                "UPDATE fiftyfifty_questions SET closed_at = ?, abandoned = 1 "
                "WHERE started_at IS NOT NULL AND closed_at IS NULL "
                "AND round_id IN (SELECT id FROM fiftyfifty_rounds WHERE game_id = ?)",
                (stamp, game_id),
            )
            await conn.execute(
                "UPDATE fiftyfifty_rounds SET closed_at = ?, "
                "write_closed_at = COALESCE(write_closed_at, ?) "
                "WHERE game_id = ? AND closed_at IS NULL",
                (stamp, stamp, game_id),
            )
            return True

    async def join(self, game_id: int, user_id: int) -> tuple[aiosqlite.Row, bool]:
        """Add a player, or bring back one who left. The bool is True for a rejoin.

        Raises AlreadyJoined when the player is in the game, GameFull at the player cap.
        """
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT * FROM fiftyfifty_players WHERE game_id = ? AND user_id = ?",
                (game_id, user_id),
            ) as cur:
                existing = await cur.fetchone()
            if existing is not None and existing["left_at"] is None:
                raise AlreadyJoined()
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM fiftyfifty_players "
                "WHERE game_id = ? AND left_at IS NULL",
                (game_id,),
            ) as cur:
                active = (await cur.fetchone())["n"]
            if active >= MAX_PLAYERS:
                raise GameFull()
            if existing is None:
                await conn.execute(
                    "INSERT INTO fiftyfifty_players (game_id, user_id, joined_at) VALUES (?, ?, ?)",
                    (game_id, user_id, now_iso()),
                )
            else:
                await conn.execute(
                    "UPDATE fiftyfifty_players SET left_at = NULL, joined_at = ? WHERE id = ?",
                    (now_iso(), existing["id"]),
                )
        player = await self.get_player(game_id, user_id)
        assert player is not None
        return player, existing is not None

    async def leave(self, player_id: int) -> bool:
        """Mark a player as out. False when they had already left."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE fiftyfifty_players SET left_at = ? WHERE id = ? AND left_at IS NULL",
                (now_iso(), player_id),
            )
            return cur.rowcount == 1

    async def get_player(self, game_id: int, user_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM fiftyfifty_players WHERE game_id = ? AND user_id = ?",
            (game_id, user_id),
        )

    async def get_player_by_id(self, player_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM fiftyfifty_players WHERE id = ?", (player_id,))

    async def list_players(self, game_id: int, active_only: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM fiftyfifty_players WHERE game_id = ?"
        if active_only:
            sql += " AND left_at IS NULL"
        return await self._all(sql + " ORDER BY joined_at, id", (game_id,))

    async def begin_round(
        self, game_id: int, topics: Sequence[str], write_deadline_at: str
    ) -> aiosqlite.Row | None:
        """Open the next round: its roster, and one unwritten question per roster player.

        Each question gets a topic, in join order.

        Returns None, writing nothing, unless the game is active and has no open round.
        """
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT (SELECT COUNT(*) FROM fiftyfifty_rounds n "
                "WHERE n.game_id = g.id AND n.abandoned = 0) AS played "
                "FROM fiftyfifty_games g WHERE g.id = ? AND g.status = 'active' "
                "AND NOT EXISTS (SELECT 1 FROM fiftyfifty_rounds r "
                "WHERE r.game_id = g.id AND r.closed_at IS NULL)",
                (game_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "INSERT INTO fiftyfifty_rounds (game_id, number, started_at, write_deadline_at) "
                "VALUES (?, ?, ?, ?)",
                (game_id, row["played"] + 1, now_iso(), write_deadline_at),
            )
            round_id = cur.lastrowid
            async with conn.execute(
                "SELECT id FROM fiftyfifty_players WHERE game_id = ? AND left_at IS NULL "
                "ORDER BY joined_at, id",
                (game_id,),
            ) as cur:
                players = await cur.fetchall()
            for index, player in enumerate(players):
                topic = topics[index % len(topics)]
                await conn.execute(
                    "INSERT INTO fiftyfifty_round_players (round_id, player_id) VALUES (?, ?)",
                    (round_id, player["id"]),
                )
                await conn.execute(
                    "INSERT INTO fiftyfifty_questions "
                    "(round_id, writer_id, option_a, option_b, topic) VALUES (?, ?, '', '', ?)",
                    (round_id, player["id"], topic),
                )
        rnd = await self.get_round(round_id)
        assert rnd is not None
        return rnd

    async def get_round(self, round_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM fiftyfifty_rounds WHERE id = ?", (round_id,))

    async def get_current_round(self, game_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM fiftyfifty_rounds WHERE game_id = ? ORDER BY id DESC LIMIT 1",
            (game_id,),
        )

    async def rounds_played(self, game_id: int) -> int:
        """Rounds that were played to their end. Abandoned rounds don't count."""
        row = await self._one(
            "SELECT COUNT(*) AS n FROM fiftyfifty_rounds "
            "WHERE game_id = ? AND closed_at IS NOT NULL AND abandoned = 0",
            (game_id,),
        )
        return row["n"] if row else 0

    async def used_topics(self, game_id: int) -> set[str]:
        rows = await self._all(
            "SELECT DISTINCT q.topic FROM fiftyfifty_questions q "
            "JOIN fiftyfifty_rounds r ON r.id = q.round_id "
            "WHERE r.game_id = ? AND q.topic IS NOT NULL",
            (game_id,),
        )
        return {r["topic"] for r in rows}

    async def set_write_message(self, round_id: int, channel_id: int, message_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE fiftyfifty_rounds SET write_channel_id = ?, write_message_id = ? "
                "WHERE id = ?",
                (channel_id, message_id, round_id),
            )

    async def abandon_round(self, round_id: int) -> bool:
        """Close a round that never got going, so it counts for nothing. False if already closed."""
        stamp = now_iso()
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE fiftyfifty_rounds SET closed_at = ?, "
                "write_closed_at = COALESCE(write_closed_at, ?), abandoned = 1 "
                "WHERE id = ? AND closed_at IS NULL",
                (stamp, stamp, round_id),
            )
            return cur.rowcount == 1

    async def round_roster(self, round_id: int) -> list[aiosqlite.Row]:
        """Players who were in the game when the round started, whether or not they left."""
        return await self._all(
            "SELECT p.* FROM fiftyfifty_round_players rp "
            "JOIN fiftyfifty_players p ON p.id = rp.player_id "
            "WHERE rp.round_id = ? ORDER BY p.joined_at, p.id",
            (round_id,),
        )

    async def on_roster(self, round_id: int, player_id: int) -> bool:
        row = await self._one(
            "SELECT 1 FROM fiftyfifty_round_players WHERE round_id = ? AND player_id = ?",
            (round_id, player_id),
        )
        return row is not None

    async def get_question(self, question_id: int) -> aiosqlite.Row | None:
        """A question, with its round's ``round_number`` and ``game_id``."""
        return await self._one(QUESTION + "WHERE q.id = ?", (question_id,))

    async def write_context(self, round_id: int, user_id: int) -> aiosqlite.Row | None:
        """One read of everything a write needs: game, round, player, roster place, question.

        None when the round doesn't exist. Player and question columns are NULL for a user who
        isn't in the game or has no question in this round.
        """
        return await self._one(
            "SELECT g.id AS game_id, g.status, g.guild_id, g.name AS game_name, "
            "r.id AS round_id, r.write_closed_at, r.closed_at AS round_closed_at, "
            "p.id AS player_id, p.left_at, rp.player_id IS NOT NULL AS on_roster, "
            "q.id AS question_id, q.option_a, q.option_b, q.topic, q.answer, q.answered_at, "
            "q.dm_prompted_at "
            "FROM fiftyfifty_rounds r "
            "JOIN fiftyfifty_games g ON g.id = r.game_id "
            "LEFT JOIN fiftyfifty_players p ON p.game_id = g.id AND p.user_id = ? "
            "LEFT JOIN fiftyfifty_round_players rp ON rp.round_id = r.id AND rp.player_id = p.id "
            "LEFT JOIN fiftyfifty_questions q ON q.round_id = r.id AND q.writer_id = p.id "
            "WHERE r.id = ?",
            (user_id, round_id),
        )

    async def write_options(
        self, question_id: int, option_a: str, option_b: str, via_dm: bool = False
    ) -> bool:
        """Save or replace both options. False, writing nothing, once writing has closed.

        A save from the pop-up form also clears the question's DM flag.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE fiftyfifty_questions SET option_a = ?, option_b = ?, answer = NULL, "
                "answered_at = ?, "
                "dm_prompted_at = CASE WHEN ? THEN dm_prompted_at ELSE NULL END "
                "WHERE id = ? AND EXISTS (SELECT 1 FROM fiftyfifty_rounds r "
                "JOIN fiftyfifty_games g ON g.id = r.game_id "
                "WHERE r.id = fiftyfifty_questions.round_id AND r.write_closed_at IS NULL "
                "AND r.closed_at IS NULL AND g.status = 'active')",
                (option_a, option_b, now_iso(), 1 if via_dm else 0, question_id),
            )
            return cur.rowcount == 1

    async def dm_candidates(self, user_id: int) -> list[aiosqlite.Row]:
        """The user's question in the current round of every running game they are in.

        Open writing windows come first, then the most recently DM-prompted, then the newest.
        """
        return await self._all(
            "SELECT q.id AS question_id, q.round_id, q.option_a, q.option_b, q.topic, q.answer, "
            "q.answered_at, q.dm_prompted_at, r.write_closed_at, g.id AS game_id, g.name AS game_name, "
            "g.guild_id "
            "FROM fiftyfifty_players p "
            "JOIN fiftyfifty_games g ON g.id = p.game_id AND g.status = 'active' "
            "JOIN fiftyfifty_rounds r "
            "ON r.id = (SELECT MAX(id) FROM fiftyfifty_rounds WHERE game_id = g.id) "
            "AND r.closed_at IS NULL "
            "JOIN fiftyfifty_round_players rp ON rp.round_id = r.id AND rp.player_id = p.id "
            "JOIN fiftyfifty_questions q ON q.round_id = r.id AND q.writer_id = p.id "
            "WHERE p.user_id = ? AND p.left_at IS NULL "
            "ORDER BY r.write_closed_at IS NOT NULL, q.dm_prompted_at IS NULL, "
            "q.dm_prompted_at DESC, q.id DESC",
            (user_id,),
        )

    async def mark_dm_prompted(self, question_id: int) -> bool:
        """Note that the question was sent by DM, so the player's next DM is their two options.

        False, writing nothing, when it was already noted or the round's writing has closed.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE fiftyfifty_questions SET dm_prompted_at = ? "
                "WHERE id = ? AND dm_prompted_at IS NULL "
                "AND EXISTS (SELECT 1 FROM fiftyfifty_rounds r "
                "JOIN fiftyfifty_games g ON g.id = r.game_id "
                "WHERE r.id = fiftyfifty_questions.round_id AND r.write_closed_at IS NULL "
                "AND r.closed_at IS NULL AND g.status = 'active')",
                (now_iso(), question_id),
            )
            return cur.rowcount == 1

    async def clear_dm_prompted(self, question_id: int) -> None:
        """Forget that the question was sent by DM, for when the DM could not be delivered."""
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE fiftyfifty_questions SET dm_prompted_at = NULL WHERE id = ?",
                (question_id,),
            )

    async def write_progress(self, round_id: int) -> tuple[int, int]:
        """(questions written, writers expected), counting only roster players still in the game."""
        row = await self._one(
            "SELECT COUNT(*) AS expected, COUNT(q.answered_at) AS written "
            "FROM fiftyfifty_round_players rp "
            "JOIN fiftyfifty_players p ON p.id = rp.player_id "
            "LEFT JOIN fiftyfifty_questions q "
            "ON q.round_id = rp.round_id AND q.writer_id = rp.player_id "
            "WHERE rp.round_id = ? AND p.left_at IS NULL",
            (round_id,),
        )
        return (row["written"], row["expected"]) if row else (0, 0)

    async def close_writing(self, round_id: int, rng: random.Random) -> bool:
        """Close the writing window and put the written questions in a random asking order.

        False when writing had already closed (lost a race).
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE fiftyfifty_rounds SET write_closed_at = ? "
                "WHERE id = ? AND write_closed_at IS NULL AND closed_at IS NULL",
                (now_iso(), round_id),
            )
            if cur.rowcount == 0:
                return False
            async with conn.execute(
                "SELECT id FROM fiftyfifty_questions "
                "WHERE round_id = ? AND answered_at IS NOT NULL ORDER BY id",
                (round_id,),
            ) as cur:
                ids = [row["id"] for row in await cur.fetchall()]
            rng.shuffle(ids)
            for position, question_id in enumerate(ids, start=1):
                await conn.execute(
                    "UPDATE fiftyfifty_questions SET position = ? WHERE id = ?",
                    (position, question_id),
                )
            return True

    async def question_count(self, round_id: int) -> int:
        """How many questions the round asks: the ones that were written."""
        row = await self._one(
            "SELECT COUNT(*) AS n FROM fiftyfifty_questions "
            "WHERE round_id = ? AND position IS NOT NULL",
            (round_id,),
        )
        return row["n"] if row else 0

    async def unwritten_user_ids(self, round_id: int) -> list[int]:
        """User ids of the round's players who wrote no question."""
        rows = await self._all(
            "SELECT p.user_id FROM fiftyfifty_questions q "
            "JOIN fiftyfifty_players p ON p.id = q.writer_id "
            "WHERE q.round_id = ? AND q.answered_at IS NULL ORDER BY p.user_id",
            (round_id,),
        )
        return [r["user_id"] for r in rows]

    async def next_question(self, round_id: int) -> aiosqlite.Row | None:
        """The next written question that hasn't been asked yet."""
        return await self._one(
            QUESTION + "WHERE q.round_id = ? AND q.position IS NOT NULL "
            "AND q.started_at IS NULL AND q.closed_at IS NULL ORDER BY q.position LIMIT 1",
            (round_id,),
        )

    async def open_question(self, question_id: int, deadline_at: str) -> bool:
        """Open a question for votes.

        False, writing nothing, unless it is waiting, its round is voting, the game is running
        and no other question in the round is open.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE fiftyfifty_questions SET started_at = ?, deadline_at = ? "
                "WHERE id = ? AND started_at IS NULL AND closed_at IS NULL "
                "AND position IS NOT NULL "
                "AND EXISTS (SELECT 1 FROM fiftyfifty_rounds r "
                "JOIN fiftyfifty_games g ON g.id = r.game_id "
                "WHERE r.id = fiftyfifty_questions.round_id AND r.write_closed_at IS NOT NULL "
                "AND r.closed_at IS NULL AND g.status = 'active') "
                "AND NOT EXISTS (SELECT 1 FROM fiftyfifty_questions o "
                "WHERE o.round_id = fiftyfifty_questions.round_id "
                "AND o.started_at IS NOT NULL AND o.closed_at IS NULL)",
                (now_iso(), deadline_at, question_id),
            )
            return cur.rowcount == 1

    async def current_question(self, round_id: int) -> aiosqlite.Row | None:
        """The question that is open for votes, or None."""
        return await self._one(
            QUESTION + "WHERE q.round_id = ? AND q.started_at IS NOT NULL AND q.closed_at IS NULL",
            (round_id,),
        )

    async def set_vote_message(self, question_id: int, channel_id: int, message_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE fiftyfifty_questions SET vote_channel_id = ?, vote_message_id = ? "
                "WHERE id = ?",
                (channel_id, message_id, question_id),
            )

    async def abandon_question(self, question_id: int) -> bool:
        """Close a question without a reveal, so it scores nothing. False if already closed."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE fiftyfifty_questions SET closed_at = ?, abandoned = 1 "
                "WHERE id = ? AND closed_at IS NULL",
                (now_iso(), question_id),
            )
            return cur.rowcount == 1

    async def cast_vote(self, question_id: int, voter_id: int, choice: str) -> bool:
        """Record or change a vote. False, writing nothing, unless the question is open."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO fiftyfifty_votes (question_id, voter_id, choice, cast_at) "
                "SELECT ?, ?, ?, ? WHERE EXISTS "
                "(SELECT 1 FROM fiftyfifty_questions "
                "WHERE id = ? AND started_at IS NOT NULL AND closed_at IS NULL) "
                "ON CONFLICT(question_id, voter_id) DO UPDATE SET "
                "choice = excluded.choice, cast_at = excluded.cast_at",
                (question_id, voter_id, choice, now_iso(), question_id),
            )
            return cur.rowcount == 1

    async def get_vote(self, question_id: int, voter_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM fiftyfifty_votes WHERE question_id = ? AND voter_id = ?",
            (question_id, voter_id),
        )

    async def vote_counts(self, question_id: int) -> tuple[int, int]:
        """(votes for A, votes for B)."""
        row = await self._one(
            "SELECT COALESCE(SUM(choice = 'a'), 0) AS a, COALESCE(SUM(choice = 'b'), 0) AS b "
            "FROM fiftyfifty_votes WHERE question_id = ?",
            (question_id,),
        )
        return (row["a"], row["b"]) if row else (0, 0)

    async def vote_progress(self, question_id: int) -> tuple[int, int]:
        """(votes in, voters expected): roster players still in the game, other than the writer."""
        row = await self._one(
            "SELECT COUNT(*) AS eligible, COUNT(v.voter_id) AS voted "
            "FROM fiftyfifty_questions q "
            "JOIN fiftyfifty_round_players rp ON rp.round_id = q.round_id "
            "JOIN fiftyfifty_players p ON p.id = rp.player_id "
            "LEFT JOIN fiftyfifty_votes v ON v.question_id = q.id AND v.voter_id = p.id "
            "WHERE q.id = ? AND p.left_at IS NULL AND p.id != q.writer_id",
            (question_id,),
        )
        return (row["voted"], row["eligible"]) if row else (0, 0)

    async def set_guess(self, question_id: int, voter_id: int, a_votes: int) -> bool:
        """Record or change a guess at the number of A votes.

        False, writing nothing, unless the question is open and the voter has voted on it.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO fiftyfifty_guesses (question_id, voter_id, a_votes, made_at) "
                "SELECT ?, ?, ?, ? WHERE EXISTS "
                "(SELECT 1 FROM fiftyfifty_questions "
                "WHERE id = ? AND started_at IS NOT NULL AND closed_at IS NULL) "
                "AND EXISTS (SELECT 1 FROM fiftyfifty_votes WHERE question_id = ? AND voter_id = ?) "
                "ON CONFLICT(question_id, voter_id) DO UPDATE SET "
                "a_votes = excluded.a_votes, made_at = excluded.made_at",
                (question_id, voter_id, a_votes, now_iso(), question_id, question_id, voter_id),
            )
            return cur.rowcount == 1

    async def get_guess(self, question_id: int, voter_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM fiftyfifty_guesses WHERE question_id = ? AND voter_id = ?",
            (question_id, voter_id),
        )

    async def guess_progress(self, question_id: int) -> tuple[int, int]:
        """(guesses in, voters expected), over the same voters as ``vote_progress``."""
        row = await self._one(
            "SELECT COUNT(*) AS eligible, COUNT(g.voter_id) AS guessed "
            "FROM fiftyfifty_questions q "
            "JOIN fiftyfifty_round_players rp ON rp.round_id = q.round_id "
            "JOIN fiftyfifty_players p ON p.id = rp.player_id "
            "LEFT JOIN fiftyfifty_guesses g ON g.question_id = q.id AND g.voter_id = p.id "
            "WHERE q.id = ? AND p.left_at IS NULL AND p.id != q.writer_id",
            (question_id,),
        )
        return (row["guessed"], row["eligible"]) if row else (0, 0)

    async def most_votes(self, question_id: int) -> int:
        """The most votes the question can still end with: voters in the game, plus the votes
        of those who left."""
        row = await self._one(
            "SELECT COUNT(*) AS n FROM fiftyfifty_questions q "
            "JOIN fiftyfifty_round_players rp ON rp.round_id = q.round_id "
            "JOIN fiftyfifty_players p ON p.id = rp.player_id "
            "LEFT JOIN fiftyfifty_votes v ON v.question_id = q.id AND v.voter_id = p.id "
            "WHERE q.id = ? AND p.id != q.writer_id "
            "AND (p.left_at IS NULL OR v.voter_id IS NOT NULL)",
            (question_id,),
        )
        return row["n"] if row else 0

    async def voters(self, question_id: int) -> list[aiosqlite.Row]:
        """Who voted for what: ``user_id`` and ``choice``, in the order the votes stand."""
        return await self._all(
            "SELECT p.user_id, v.choice FROM fiftyfifty_votes v "
            "JOIN fiftyfifty_players p ON p.id = v.voter_id "
            "WHERE v.question_id = ? ORDER BY v.cast_at, p.id",
            (question_id,),
        )

    async def guesses(self, question_id: int) -> tuple[tuple[int, int], ...]:
        """(user id, guessed votes for A) for each voter who guessed."""
        rows = await self._all(
            "SELECT p.user_id, g.a_votes FROM fiftyfifty_guesses g "
            "JOIN fiftyfifty_players p ON p.id = g.voter_id "
            "WHERE g.question_id = ? ORDER BY p.user_id",
            (question_id,),
        )
        return tuple((r["user_id"], r["a_votes"]) for r in rows)

    async def close_question(self, question_id: int) -> bool:
        """Close an open question for its reveal. False when it wasn't open (lost a race)."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE fiftyfifty_questions SET closed_at = ? "
                "WHERE id = ? AND started_at IS NOT NULL AND closed_at IS NULL",
                (now_iso(), question_id),
            )
            return cur.rowcount == 1

    async def close_round(self, round_id: int) -> bool:
        """Close a round. False when it was already closed (lost a race)."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE fiftyfifty_rounds SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
                (now_iso(), round_id),
            )
            return cur.rowcount == 1

    async def list_overdue_writing(self, now: str) -> list[aiosqlite.Row]:
        """Rounds of running games still writing at or past their deadline."""
        return await self._all(
            "SELECT r.* FROM fiftyfifty_rounds r JOIN fiftyfifty_games g ON g.id = r.game_id "
            "WHERE r.write_closed_at IS NULL AND r.closed_at IS NULL "
            "AND r.write_deadline_at <= ? AND g.status = 'active'",
            (now,),
        )

    async def list_overdue_questions(self, now: str) -> list[aiosqlite.Row]:
        """Open questions of running games at or past their deadline."""
        return await self._all(
            QUESTION + "JOIN fiftyfifty_games g ON g.id = r.game_id "
            "WHERE q.started_at IS NOT NULL AND q.closed_at IS NULL "
            "AND q.deadline_at <= ? AND g.status = 'active'",
            (now,),
        )

    async def list_stalled_games(self, before: str) -> list[aiosqlite.Row]:
        """Running games with no open step since ``before``.

        That is a game with no round yet, a closed round, or a voting round with no open question.
        """
        return await self._all(
            "SELECT g.* FROM fiftyfifty_games g "
            "LEFT JOIN fiftyfifty_rounds r "
            "ON r.id = (SELECT MAX(id) FROM fiftyfifty_rounds WHERE game_id = g.id) "
            "WHERE g.status = 'active' "
            "AND (r.id IS NULL OR r.write_closed_at IS NOT NULL) "
            "AND NOT EXISTS (SELECT 1 FROM fiftyfifty_questions q "
            "WHERE q.round_id = r.id AND q.started_at IS NOT NULL AND q.closed_at IS NULL) "
            "AND COALESCE(r.closed_at, "
            "(SELECT MAX(q.closed_at) FROM fiftyfifty_questions q WHERE q.round_id = r.id), "
            "r.write_closed_at, g.started_at) <= ?",
            (before,),
        )

    async def closed_questions(
        self, game_id: int, round_id: int | None = None
    ) -> list[ScoredQuestion]:
        """Revealed questions in the order they were asked, with their vote counts.

        Abandoned questions are left out. ``round_id`` narrows it to one round.
        """
        sql = (
            "SELECT q.id, p.user_id, q.option_a, q.option_b, q.answer, q.closed_at, "
            f"{VOTES.format(choice=" AND v.choice = 'a'")} AS a, "
            f"{VOTES.format(choice=" AND v.choice = 'b'")} AS b "
            "FROM fiftyfifty_questions q "
            "JOIN fiftyfifty_rounds r ON r.id = q.round_id "
            "JOIN fiftyfifty_players p ON p.id = q.writer_id "
            "WHERE r.game_id = ? AND q.closed_at IS NOT NULL AND q.abandoned = 0 "
            "AND q.answered_at IS NOT NULL"
        )
        params: tuple[int, ...] = (game_id,)
        if round_id is not None:
            sql += " AND r.id = ?"
            params = (game_id, round_id)
        rows = await self._all(sql + " ORDER BY r.id, q.position, q.id", params)
        guesses = await self._guesses_by_question(
            "JOIN fiftyfifty_rounds r ON r.id = q.round_id WHERE r.game_id = ?", game_id
        )
        return [_scored(r, guesses) for r in rows]

    async def _guesses_by_question(
        self, scope: str, key: int
    ) -> dict[int, tuple[tuple[int, int], ...]]:
        """Every guess on the questions that ``scope`` selects, as (user id, guess) by question."""
        rows = await self._all(
            "SELECT g.question_id, p.user_id, g.a_votes FROM fiftyfifty_guesses g "
            "JOIN fiftyfifty_players p ON p.id = g.voter_id "
            "JOIN fiftyfifty_questions q ON q.id = g.question_id "
            f"{scope} ORDER BY g.question_id, p.user_id",
            (key,),
        )
        found: dict[int, tuple[tuple[int, int], ...]] = {}
        for r in rows:
            found[r["question_id"]] = (
                *found.get(r["question_id"], ()),
                (r["user_id"], r["a_votes"]),
            )
        return found

    async def finished_questions(self, guild_id: int) -> dict[int, list[ScoredQuestion]]:
        """Revealed questions of this server's finished games, by game id, in the order asked."""
        rows = await self._all(
            "SELECT q.id, r.game_id, p.user_id, q.option_a, q.option_b, q.answer, q.closed_at, "
            f"{VOTES.format(choice=" AND v.choice = 'a'")} AS a, "
            f"{VOTES.format(choice=" AND v.choice = 'b'")} AS b "
            "FROM fiftyfifty_games g "
            "JOIN fiftyfifty_rounds r ON r.game_id = g.id "
            "JOIN fiftyfifty_questions q ON q.round_id = r.id "
            "JOIN fiftyfifty_players p ON p.id = q.writer_id "
            "WHERE g.guild_id = ? AND g.status = 'finished' "
            "AND q.closed_at IS NOT NULL AND q.abandoned = 0 AND q.answered_at IS NOT NULL "
            "ORDER BY g.id, r.id, q.position, q.id",
            (guild_id,),
        )
        guesses = await self._guesses_by_question(
            "JOIN fiftyfifty_rounds r ON r.id = q.round_id "
            "JOIN fiftyfifty_games g2 ON g2.id = r.game_id "
            "WHERE g2.guild_id = ? AND g2.status = 'finished'",
            guild_id,
        )
        games: dict[int, list[ScoredQuestion]] = {}
        for r in rows:
            games.setdefault(r["game_id"], []).append(_scored(r, guesses))
        return games

    async def games_played(self, guild_id: int) -> dict[int, int]:
        """Finished games per user id.

        A game counts for a user who was on the roster of a round in which at least one question
        was asked and revealed, so a game ended during writing counts for nobody.
        """
        rows = await self._all(
            "SELECT p.user_id, COUNT(DISTINCT g.id) AS played "
            "FROM fiftyfifty_games g "
            "JOIN fiftyfifty_rounds r ON r.game_id = g.id AND r.abandoned = 0 "
            "AND EXISTS (SELECT 1 FROM fiftyfifty_questions q WHERE q.round_id = r.id "
            "AND q.started_at IS NOT NULL AND q.closed_at IS NOT NULL AND q.abandoned = 0) "
            "JOIN fiftyfifty_round_players rp ON rp.round_id = r.id "
            "JOIN fiftyfifty_players p ON p.id = rp.player_id "
            "WHERE g.guild_id = ? AND g.status = 'finished' "
            "GROUP BY p.user_id",
            (guild_id,),
        )
        return {r["user_id"]: r["played"] for r in rows}

    async def asked_vote_totals(self, game_id: int) -> list[int]:
        """Votes per revealed question in the order asked, for the idle rule.

        A question nobody could vote on (no vote, and no roster player other than the writer
        still in the game) is left out.
        """
        rows = await self._all(
            f"SELECT {VOTES.format(choice='')} AS votes, {ELIGIBLE_VOTERS} AS eligible "
            "FROM fiftyfifty_questions q "
            "JOIN fiftyfifty_rounds r ON r.id = q.round_id "
            "WHERE r.game_id = ? AND q.closed_at IS NOT NULL AND q.abandoned = 0 "
            "AND q.answered_at IS NOT NULL ORDER BY r.id, q.position, q.id",
            (game_id,),
        )
        return [r["votes"] for r in rows if r["votes"] or r["eligible"]]

    async def abandoned_streak(self, game_id: int) -> int:
        """How many rounds in a row were abandoned since the last one that went ahead."""
        row = await self._one(
            "SELECT COUNT(*) AS n FROM fiftyfifty_rounds "
            "WHERE game_id = ? AND abandoned = 1 "
            "AND id > COALESCE("
            "(SELECT MAX(id) FROM fiftyfifty_rounds WHERE game_id = ? AND abandoned = 0), 0)",
            (game_id, game_id),
        )
        return row["n"] if row else 0

    async def last_round_answers(self, game_id: int) -> int | None:
        """Questions written in the newest finished round that wasn't abandoned. None without one."""
        row = await self._one(
            "SELECT (SELECT COUNT(*) FROM fiftyfifty_questions q "
            "WHERE q.round_id = r.id AND q.answered_at IS NOT NULL) AS n "
            "FROM fiftyfifty_rounds r "
            "WHERE r.game_id = ? AND r.closed_at IS NOT NULL AND r.abandoned = 0 "
            "ORDER BY r.id DESC LIMIT 1",
            (game_id,),
        )
        return row["n"] if row else None

    async def round_user_ids(self, game_id: int) -> list[int]:
        """User ids of everyone who was on the roster of a round that wasn't abandoned."""
        rows = await self._all(
            "SELECT DISTINCT p.user_id FROM fiftyfifty_rounds r "
            "JOIN fiftyfifty_round_players rp ON rp.round_id = r.id "
            "JOIN fiftyfifty_players p ON p.id = rp.player_id "
            "WHERE r.game_id = ? AND r.abandoned = 0 ORDER BY p.user_id",
            (game_id,),
        )
        return [r["user_id"] for r in rows]

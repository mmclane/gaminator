"""All Headlines database access. Rows are aiosqlite.Row; callers index by column name."""

from __future__ import annotations

import random
from collections.abc import Sequence

import aiosqlite

from ...core.repo import AlreadyJoined, BaseRepo
from ...core.util import GameError, now_iso
from .services.rules import DEFAULT_ROUNDS, MAX_PLAYERS, ScoredPairing, assign_quotes

ROUND_OPEN = (
    "SELECT 1 FROM headlines_rounds r JOIN headlines_games g ON g.id = r.game_id "
    "WHERE r.closed_at IS NULL AND g.status = 'active' "
)
IN_REVEAL = "write_closed_at IS NOT NULL AND vote_deadline_at IS NULL AND closed_at IS NULL"
GAME_ACTIVE = (
    "EXISTS (SELECT 1 FROM headlines_games g "
    "WHERE g.id = headlines_rounds.game_id AND g.status = 'active')"
)
ELIGIBLE = (
    "FROM headlines_answers a "
    "JOIN headlines_players p ON p.id = a.player_id "
    "LEFT JOIN headlines_votes v ON v.round_id = a.round_id AND v.voter_id = p.id "
    "WHERE a.round_id = {round} AND p.left_at IS NULL "
    "AND EXISTS (SELECT 1 FROM headlines_pairings w "
    "WHERE w.round_id = a.round_id AND w.position IS NOT NULL AND w.writer_id != p.id)"
)
OVERDUE = (
    "SELECT r.* FROM headlines_rounds r JOIN headlines_games g ON g.id = r.game_id "
    "WHERE g.status = 'active' AND r.closed_at IS NULL AND "
)
SCORED = (
    "SELECT r.game_id, r.number AS round_number, w.position, wp.user_id AS writer_user_id, "
    "qp.user_id AS quoted_user_id, w.headline, a.answer, "
    "(SELECT COUNT(*) FROM headlines_votes v WHERE v.pairing_id = w.id) AS votes "
    "FROM headlines_pairings w "
    "JOIN headlines_rounds r ON r.id = w.round_id "
    "JOIN headlines_games g ON g.id = r.game_id "
    "JOIN headlines_answers a ON a.id = w.answer_id "
    "JOIN headlines_players wp ON wp.id = w.writer_id "
    "JOIN headlines_players qp ON qp.id = a.player_id "
    "WHERE r.outcome = 'voted' AND w.position IS NOT NULL "
)


def _scored(row) -> ScoredPairing:
    return ScoredPairing(
        row["round_number"],
        row["position"],
        row["writer_user_id"],
        row["quoted_user_id"],
        row["headline"],
        row["answer"],
        row["votes"],
    )


class GameFull(GameError):
    def __init__(self) -> None:
        super().__init__(f"The game is full ({MAX_PLAYERS} players).")


class HeadlinesRepo(BaseRepo):
    async def create_game(
        self, guild_id: int, name: str, created_by: int, channel_id: int
    ) -> aiosqlite.Row:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO headlines_games "
                "(guild_id, name, status, max_rounds, channel_id, created_by, host_id, created_at) "
                "VALUES (?, ?, 'open', ?, ?, ?, ?, ?)",
                (guild_id, name, DEFAULT_ROUNDS, channel_id, created_by, created_by, now_iso()),
            )
            game_id = cur.lastrowid
        game = await self.get_game(game_id)
        assert game is not None
        return game

    async def get_game(self, game_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM headlines_games WHERE id = ?", (game_id,))

    async def get_current_game(self, guild_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM headlines_games WHERE guild_id = ? AND status != 'finished'",
            (guild_id,),
        )

    async def start_game(self, game_id: int, max_rounds: int) -> bool:
        """Mark an open game active. False when it was not open (already started or ended)."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_games SET status = 'active', started_at = ?, max_rounds = ? "
                "WHERE id = ? AND status = 'open'",
                (now_iso(), max_rounds, game_id),
            )
            return cur.rowcount == 1

    async def set_host(self, game_id: int, user_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE headlines_games SET host_id = ? WHERE id = ?", (user_id, game_id)
            )

    async def set_lobby_message(self, game_id: int, message_id: int | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE headlines_games SET lobby_message_id = ? WHERE id = ?",
                (message_id, game_id),
            )

    async def finish_game(self, game_id: int) -> bool:
        """Finish the game and abandon a round that is still open. False when already finished."""
        stamp = now_iso()
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_games SET status = 'finished', finished_at = ? "
                "WHERE id = ? AND status != 'finished'",
                (stamp, game_id),
            )
            if cur.rowcount == 0:
                return False
            await conn.execute(
                "UPDATE headlines_rounds SET closed_at = ?, outcome = 'abandoned' "
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
                "SELECT * FROM headlines_players WHERE game_id = ? AND user_id = ?",
                (game_id, user_id),
            ) as cur:
                existing = await cur.fetchone()
            if existing is not None and existing["left_at"] is None:
                raise AlreadyJoined()
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM headlines_players WHERE game_id = ? AND left_at IS NULL",
                (game_id,),
            ) as cur:
                active = (await cur.fetchone())["n"]
            if active >= MAX_PLAYERS:
                raise GameFull()
            if existing is None:
                await conn.execute(
                    "INSERT INTO headlines_players (game_id, user_id, joined_at) VALUES (?, ?, ?)",
                    (game_id, user_id, now_iso()),
                )
            else:
                await conn.execute(
                    "UPDATE headlines_players SET left_at = NULL, joined_at = ? WHERE id = ?",
                    (now_iso(), existing["id"]),
                )
        player = await self.get_player(game_id, user_id)
        assert player is not None
        return player, existing is not None

    async def leave(self, player_id: int) -> bool:
        """Mark a player as out. False when they had already left."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_players SET left_at = ? WHERE id = ? AND left_at IS NULL",
                (now_iso(), player_id),
            )
            return cur.rowcount == 1

    async def get_player(self, game_id: int, user_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM headlines_players WHERE game_id = ? AND user_id = ?",
            (game_id, user_id),
        )

    async def get_player_by_id(self, player_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM headlines_players WHERE id = ?", (player_id,))

    async def list_players(self, game_id: int, active_only: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM headlines_players WHERE game_id = ?"
        if active_only:
            sql += " AND left_at IS NULL"
        return await self._all(sql + " ORDER BY joined_at, id", (game_id,))

    async def begin_round(
        self, game_id: int, questions: Sequence[str], answer_deadline_at: str
    ) -> aiosqlite.Row | None:
        """Open the next round with one unanswered question per player in the game.

        Those rows are the round's roster. Questions are dealt in join order.

        Returns None, writing nothing, unless the game is active and has no open round.
        """
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT (SELECT COUNT(*) FROM headlines_rounds n "
                "WHERE n.game_id = g.id AND n.outcome IS NOT 'abandoned') AS played "
                "FROM headlines_games g WHERE g.id = ? AND g.status = 'active' "
                "AND NOT EXISTS (SELECT 1 FROM headlines_rounds r "
                "WHERE r.game_id = g.id AND r.closed_at IS NULL)",
                (game_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "INSERT INTO headlines_rounds (game_id, number, started_at, answer_deadline_at) "
                "VALUES (?, ?, ?, ?)",
                (game_id, row["played"] + 1, now_iso(), answer_deadline_at),
            )
            round_id = cur.lastrowid
            async with conn.execute(
                "SELECT id FROM headlines_players WHERE game_id = ? AND left_at IS NULL "
                "ORDER BY joined_at, id",
                (game_id,),
            ) as cur:
                players = await cur.fetchall()
            for index, player in enumerate(players):
                await conn.execute(
                    "INSERT INTO headlines_answers (round_id, player_id, question) "
                    "VALUES (?, ?, ?)",
                    (round_id, player["id"], questions[index % len(questions)]),
                )
        rnd = await self.get_round(round_id)
        assert rnd is not None
        return rnd

    async def get_round(self, round_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM headlines_rounds WHERE id = ?", (round_id,))

    async def get_current_round(self, game_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM headlines_rounds WHERE game_id = ? ORDER BY id DESC LIMIT 1",
            (game_id,),
        )

    async def rounds_played(self, game_id: int) -> int:
        """Closed rounds, leaving out the abandoned ones."""
        row = await self._one(
            "SELECT COUNT(*) AS n FROM headlines_rounds "
            "WHERE game_id = ? AND closed_at IS NOT NULL AND outcome IS NOT 'abandoned'",
            (game_id,),
        )
        return row["n"] if row else 0

    async def used_questions(self, game_id: int) -> set[str]:
        rows = await self._all(
            "SELECT DISTINCT a.question FROM headlines_answers a "
            "JOIN headlines_rounds r ON r.id = a.round_id WHERE r.game_id = ?",
            (game_id,),
        )
        return {r["question"] for r in rows}

    async def round_context(self, round_id: int, user_id: int) -> aiosqlite.Row | None:
        """One read of everything a button, a submit, a vote or a status needs.

        None when the round doesn't exist. ``player_id`` is NULL for a user who isn't in the
        game, and ``answer_id`` is NULL for a player who isn't on this round's roster.
        """
        return await self._one(
            "SELECT g.id AS game_id, g.status, g.guild_id, g.channel_id, g.max_rounds, "
            "r.id AS round_id, r.number AS round_number, r.answer_deadline_at, "
            "r.answer_closed_at, r.write_deadline_at, r.write_closed_at, r.vote_deadline_at, "
            "r.vote_closed_at, r.closed_at, "
            "p.id AS player_id, p.left_at, "
            "a.id AS answer_id, a.question, a.answer, "
            "w.id AS pairing_id, qa.answer AS quote, w.headline, "
            "v.pairing_id AS my_vote, "
            "EXISTS (SELECT 1 FROM headlines_pairings o "
            "WHERE o.round_id = r.id AND o.position IS NOT NULL AND o.writer_id != p.id"
            ") AS can_vote "
            "FROM headlines_rounds r "
            "JOIN headlines_games g ON g.id = r.game_id "
            "LEFT JOIN headlines_players p ON p.game_id = g.id AND p.user_id = ? "
            "LEFT JOIN headlines_answers a ON a.round_id = r.id AND a.player_id = p.id "
            "LEFT JOIN headlines_pairings w ON w.round_id = r.id AND w.writer_id = p.id "
            "LEFT JOIN headlines_answers qa ON qa.id = w.answer_id "
            "LEFT JOIN headlines_votes v ON v.round_id = r.id AND v.voter_id = p.id "
            "WHERE r.id = ?",
            (user_id, round_id),
        )

    async def _set_message(self, column: str, round_id: int, message_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                f"UPDATE headlines_rounds SET {column} = ? WHERE id = ?", (message_id, round_id)
            )

    async def set_answer_message(self, round_id: int, message_id: int) -> None:
        await self._set_message("answer_message_id", round_id, message_id)

    async def set_write_message(self, round_id: int, message_id: int) -> None:
        await self._set_message("write_message_id", round_id, message_id)

    async def set_vote_message(self, round_id: int, message_id: int) -> None:
        await self._set_message("vote_message_id", round_id, message_id)

    async def save_answer(self, round_id: int, player_id: int, text: str) -> bool:
        """Save or change an answer.

        False, writing nothing, unless answering is open, the game is running, and the player
        is on the round's roster and still in the game.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_answers SET answer = ? WHERE round_id = ? AND player_id = ? "
                f"AND EXISTS ({ROUND_OPEN}AND r.id = headlines_answers.round_id "
                "AND r.answer_closed_at IS NULL) "
                "AND EXISTS (SELECT 1 FROM headlines_players p "
                "WHERE p.id = headlines_answers.player_id AND p.left_at IS NULL)",
                (text, round_id, player_id),
            )
            return cur.rowcount == 1

    async def answer_progress(self, round_id: int) -> tuple[int, int]:
        """(answers in, answers expected), counting only roster players still in the game."""
        row = await self._one(
            "SELECT COUNT(*) AS expected, COUNT(a.answer) AS answered "
            "FROM headlines_answers a JOIN headlines_players p ON p.id = a.player_id "
            "WHERE a.round_id = ? AND p.left_at IS NULL",
            (round_id,),
        )
        return (row["answered"], row["expected"]) if row else (0, 0)

    async def close_answers(
        self, round_id: int, rng: random.Random, write_deadline_at: str
    ) -> tuple[int, int] | None:
        """Close answering and hand the answers out. Returns (answers, pairings).

        With no pairing to write the round closes as ``no_answers``; otherwise headline writing
        opens. None, writing nothing, when answering was already closed.
        """
        stamp = now_iso()
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_rounds SET answer_closed_at = ? "
                "WHERE id = ? AND answer_closed_at IS NULL AND closed_at IS NULL",
                (stamp, round_id),
            )
            if cur.rowcount == 0:
                return None
            async with conn.execute(
                "SELECT a.id, a.player_id, a.answer, p.left_at FROM headlines_answers a "
                "JOIN headlines_players p ON p.id = a.player_id "
                "WHERE a.round_id = ? ORDER BY a.id",
                (round_id,),
            ) as cur:
                roster = await cur.fetchall()
            answers = [(r["id"], r["player_id"]) for r in roster if r["answer"] is not None]
            writers = [r["player_id"] for r in roster if r["left_at"] is None]
            pairs = assign_quotes(answers, writers, rng)
            for answer_id, writer_id in pairs:
                await conn.execute(
                    "INSERT INTO headlines_pairings (round_id, answer_id, writer_id) "
                    "VALUES (?, ?, ?)",
                    (round_id, answer_id, writer_id),
                )
            if pairs:
                await conn.execute(
                    "UPDATE headlines_rounds SET write_deadline_at = ? WHERE id = ?",
                    (write_deadline_at, round_id),
                )
            else:
                await conn.execute(
                    "UPDATE headlines_rounds SET closed_at = ?, outcome = 'no_answers' "
                    "WHERE id = ?",
                    (stamp, round_id),
                )
            return len(answers), len(pairs)

    async def save_headline(self, pairing_id: int, writer_id: int, text: str) -> bool:
        """Save or change a headline.

        False, writing nothing, unless writing is open, the game is running, and the pairing is
        this player's and they are still in the game.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_pairings SET headline = ? WHERE id = ? AND writer_id = ? "
                f"AND EXISTS ({ROUND_OPEN}AND r.id = headlines_pairings.round_id "
                "AND r.write_closed_at IS NULL) "
                "AND EXISTS (SELECT 1 FROM headlines_players p "
                "WHERE p.id = headlines_pairings.writer_id AND p.left_at IS NULL)",
                (text, pairing_id, writer_id),
            )
            return cur.rowcount == 1

    async def write_progress(self, round_id: int) -> tuple[int, int]:
        """(headlines in, headlines expected), counting only writers still in the game."""
        row = await self._one(
            "SELECT COUNT(*) AS expected, COUNT(w.headline) AS written "
            "FROM headlines_pairings w JOIN headlines_players p ON p.id = w.writer_id "
            "WHERE w.round_id = ? AND p.left_at IS NULL",
            (round_id,),
        )
        return (row["written"], row["expected"]) if row else (0, 0)

    async def close_writing(self, round_id: int, rng: random.Random) -> int | None:
        """Close headline writing and put the written pairings in a random order.

        Returns how many there are; with none the round closes as ``no_headlines``. None,
        writing nothing, unless the round was open for headlines.
        """
        stamp = now_iso()
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_rounds SET write_closed_at = ? WHERE id = ? "
                "AND write_deadline_at IS NOT NULL AND write_closed_at IS NULL "
                "AND closed_at IS NULL",
                (stamp, round_id),
            )
            if cur.rowcount == 0:
                return None
            async with conn.execute(
                "SELECT id FROM headlines_pairings "
                "WHERE round_id = ? AND headline IS NOT NULL ORDER BY id",
                (round_id,),
            ) as cur:
                written = [row["id"] for row in await cur.fetchall()]
            rng.shuffle(written)
            for position, pairing_id in enumerate(written, start=1):
                await conn.execute(
                    "UPDATE headlines_pairings SET position = ? WHERE id = ?",
                    (position, pairing_id),
                )
            if not written:
                await conn.execute(
                    "UPDATE headlines_rounds SET closed_at = ?, outcome = 'no_headlines' "
                    "WHERE id = ?",
                    (stamp, round_id),
                )
            return len(written)

    async def touch_reveal(self, round_id: int) -> bool:
        """Stamp a reveal as still running. False means the reveal must stop.

        That is the case once the round has closed, its vote has opened or the game has ended.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_rounds SET reveal_active_at = ? "
                f"WHERE id = ? AND {IN_REVEAL} AND {GAME_ACTIVE}",
                (now_iso(), round_id),
            )
            return cur.rowcount == 1

    async def reveal_queue(self, round_id: int) -> list[aiosqlite.Row]:
        """The pairings still to be read out, in order, with the quoted player's user id."""
        return await self._all(
            "SELECT w.id, w.position, w.headline, a.answer, p.user_id AS quoted_user_id "
            "FROM headlines_pairings w "
            "JOIN headlines_answers a ON a.id = w.answer_id "
            "JOIN headlines_players p ON p.id = a.player_id "
            "WHERE w.round_id = ? AND w.position IS NOT NULL AND w.shown_at IS NULL "
            "ORDER BY w.position",
            (round_id,),
        )

    async def mark_shown(self, pairing_id: int) -> bool:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_pairings SET shown_at = ? WHERE id = ? AND shown_at IS NULL",
                (now_iso(), pairing_id),
            )
            return cur.rowcount == 1

    async def vote_options(self, round_id: int) -> list[aiosqlite.Row]:
        """Every pairing that has a headline, in the order it is read out."""
        return await self._all(
            "SELECT w.id, w.position, w.writer_id, w.headline, a.answer FROM headlines_pairings w "
            "JOIN headlines_answers a ON a.id = w.answer_id "
            "WHERE w.round_id = ? AND w.position IS NOT NULL ORDER BY w.position",
            (round_id,),
        )

    async def close_unvoted(self, round_id: int) -> bool:
        """Close a round in its reveal with no vote. False when it was not in its reveal."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_rounds SET closed_at = ?, outcome = 'one_headline' "
                f"WHERE id = ? AND {IN_REVEAL}",
                (now_iso(), round_id),
            )
            return cur.rowcount == 1

    async def open_vote(self, round_id: int, vote_deadline_at: str) -> bool:
        """Open the vote and store how many players may vote now.

        False, writing nothing, unless the round is in its reveal and the game is running.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_rounds SET vote_deadline_at = ?, eligible_voters = "
                f"(SELECT COUNT(*) {ELIGIBLE.format(round='headlines_rounds.id')}) "
                f"WHERE id = ? AND {IN_REVEAL} AND {GAME_ACTIVE}",
                (vote_deadline_at, round_id),
            )
            return cur.rowcount == 1

    async def get_pairing(self, pairing_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT id, round_id, writer_id, position FROM headlines_pairings WHERE id = ?",
            (pairing_id,),
        )

    async def cast_vote(self, round_id: int, voter_id: int, pairing_id: int) -> bool:
        """Record or change a vote.

        False, writing nothing, unless the vote is open, the pairing is one of this round's
        headlines by someone else, and the voter is on the roster and still in the game.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO headlines_votes (round_id, voter_id, pairing_id, cast_at) "
                "SELECT ?, ?, ?, ? "
                f"WHERE EXISTS ({ROUND_OPEN}AND r.id = ? AND r.vote_deadline_at IS NOT NULL "
                "AND r.vote_closed_at IS NULL) "
                "AND EXISTS (SELECT 1 FROM headlines_pairings w "
                "WHERE w.id = ? AND w.round_id = ? AND w.position IS NOT NULL "
                "AND w.writer_id != ?) "
                "AND EXISTS (SELECT 1 FROM headlines_answers a "
                "JOIN headlines_players p ON p.id = a.player_id "
                "WHERE a.round_id = ? AND a.player_id = ? AND p.left_at IS NULL) "
                "ON CONFLICT(round_id, voter_id) DO UPDATE SET "
                "pairing_id = excluded.pairing_id, cast_at = excluded.cast_at",
                (
                    round_id,
                    voter_id,
                    pairing_id,
                    now_iso(),
                    round_id,
                    pairing_id,
                    round_id,
                    voter_id,
                    round_id,
                    voter_id,
                ),
            )
            return cur.rowcount == 1

    async def vote_progress(self, round_id: int) -> tuple[int, int]:
        """(votes in, voters expected).

        A voter is a roster player still in the game with at least one headline they did not
        write.
        """
        row = await self._one(
            f"SELECT COUNT(*) AS eligible, COUNT(v.voter_id) AS voted {ELIGIBLE.format(round='?')}",
            (round_id,),
        )
        return (row["voted"], row["eligible"]) if row else (0, 0)

    async def close_vote(self, round_id: int) -> bool:
        """Close the vote and the round. False when the vote was not open."""
        stamp = now_iso()
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_rounds SET vote_closed_at = ?, closed_at = ?, "
                "outcome = 'voted' WHERE id = ? AND vote_deadline_at IS NOT NULL "
                "AND vote_closed_at IS NULL AND closed_at IS NULL",
                (stamp, stamp, round_id),
            )
            return cur.rowcount == 1

    async def abandon_round(self, round_id: int) -> bool:
        """Close an open round so that it counts for nothing. False when it had closed."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE headlines_rounds SET closed_at = ?, outcome = 'abandoned' "
                "WHERE id = ? AND closed_at IS NULL",
                (now_iso(), round_id),
            )
            return cur.rowcount == 1

    async def list_overdue_answering(self, now: str) -> list[aiosqlite.Row]:
        """Rounds of running games still open for answers at or past the deadline."""
        return await self._all(
            OVERDUE + "r.answer_closed_at IS NULL AND r.answer_deadline_at <= ?", (now,)
        )

    async def list_overdue_writing(self, now: str) -> list[aiosqlite.Row]:
        """Rounds of running games still open for headlines at or past the deadline."""
        return await self._all(
            OVERDUE + "r.write_deadline_at IS NOT NULL AND r.write_closed_at IS NULL "
            "AND r.write_deadline_at <= ?",
            (now,),
        )

    async def list_overdue_voting(self, now: str) -> list[aiosqlite.Row]:
        """Rounds of running games still open for votes at or past the deadline."""
        return await self._all(
            OVERDUE + "r.vote_deadline_at IS NOT NULL AND r.vote_closed_at IS NULL "
            "AND r.vote_deadline_at <= ?",
            (now,),
        )

    async def list_stalled_games(self, before: str) -> list[aiosqlite.Row]:
        """Running games with nothing to wait for since ``before``, with ``round_id``.

        That is a game with no round yet, a closed round, or a round in its reveal that nobody
        has touched.
        """
        return await self._all(
            "SELECT g.*, r.id AS round_id FROM headlines_games g "
            "LEFT JOIN headlines_rounds r "
            "ON r.id = (SELECT MAX(id) FROM headlines_rounds WHERE game_id = g.id) "
            "WHERE g.status = 'active' "
            "AND (r.id IS NULL OR r.closed_at IS NOT NULL "
            "OR (r.write_closed_at IS NOT NULL AND r.vote_deadline_at IS NULL)) "
            "AND COALESCE(r.closed_at, r.reveal_active_at, r.write_closed_at, r.started_at, "
            "g.started_at) <= ?",
            (before,),
        )

    async def abandoned_streak(self, game_id: int) -> int:
        """How many rounds in a row were abandoned since the last one that closed another way."""
        row = await self._one(
            "SELECT COUNT(*) AS n FROM headlines_rounds "
            "WHERE game_id = ? AND outcome = 'abandoned' "
            "AND id > COALESCE((SELECT MAX(id) FROM headlines_rounds "
            "WHERE game_id = ? AND outcome IS NOT NULL AND outcome != 'abandoned'), 0)",
            (game_id, game_id),
        )
        return row["n"] if row else 0

    async def last_round_activity(self, game_id: int) -> aiosqlite.Row | None:
        """The newest closed round that was not abandoned: outcome, eligible voters, votes."""
        return await self._one(
            "SELECT r.id, r.number, r.outcome, r.eligible_voters, "
            "(SELECT COUNT(*) FROM headlines_votes v WHERE v.round_id = r.id) AS votes "
            "FROM headlines_rounds r WHERE r.game_id = ? AND r.closed_at IS NOT NULL "
            "AND r.outcome IS NOT 'abandoned' ORDER BY r.id DESC LIMIT 1",
            (game_id,),
        )

    async def scored_pairings(
        self, game_id: int, round_id: int | None = None
    ) -> list[ScoredPairing]:
        """The headlines of voted rounds, in the order read out. ``round_id`` narrows it."""
        sql = SCORED + "AND r.game_id = ?"
        params: tuple[int, ...] = (game_id,)
        if round_id is not None:
            sql += " AND r.id = ?"
            params = (game_id, round_id)
        rows = await self._all(sql + " ORDER BY r.id, w.position", params)
        return [_scored(r) for r in rows]

    async def game_user_ids(self, game_id: int, round_id: int | None = None) -> list[int]:
        """User ids of everyone on the roster of a voted round, leavers included.

        ``round_id`` narrows it to one round.
        """
        sql = (
            "SELECT DISTINCT p.user_id FROM headlines_answers a "
            "JOIN headlines_rounds r ON r.id = a.round_id "
            "JOIN headlines_players p ON p.id = a.player_id "
            "WHERE r.outcome = 'voted' AND r.game_id = ?"
        )
        params: tuple[int, ...] = (game_id,)
        if round_id is not None:
            sql += " AND r.id = ?"
            params = (game_id, round_id)
        return [r["user_id"] for r in await self._all(sql + " ORDER BY 1", params)]

    async def finished_pairings(self, guild_id: int) -> dict[int, list[ScoredPairing]]:
        """The headlines of voted rounds in this server's finished games, by game id."""
        rows = await self._all(
            SCORED + "AND g.guild_id = ? AND g.status = 'finished' ORDER BY g.id, r.id, w.position",
            (guild_id,),
        )
        games: dict[int, list[ScoredPairing]] = {}
        for r in rows:
            games.setdefault(r["game_id"], []).append(_scored(r))
        return games

    async def games_played(self, guild_id: int) -> dict[int, int]:
        """Finished games per user id.

        A game counts for a user who was on the roster of at least one voted round, so a game
        ended during its first answer step counts for nobody.
        """
        rows = await self._all(
            "SELECT p.user_id, COUNT(DISTINCT r.game_id) AS played FROM headlines_answers a "
            "JOIN headlines_rounds r ON r.id = a.round_id "
            "JOIN headlines_games g ON g.id = r.game_id "
            "JOIN headlines_players p ON p.id = a.player_id "
            "WHERE g.guild_id = ? AND g.status = 'finished' AND r.outcome = 'voted' "
            "GROUP BY p.user_id",
            (guild_id,),
        )
        return {r["user_id"]: r["played"] for r in rows}

"""All Spectrum database access. Rows are aiosqlite.Row; callers index by column name."""

from __future__ import annotations

import random
from collections.abc import Sequence

import aiosqlite

from ...core.repo import AlreadyJoined, BaseRepo
from ...core.util import GameError, now_iso
from .services.rules import DEFAULT_ROUNDS, MAX_PLAYERS, ScoredTurn
from .services.scales import Scale

TURN = (
    "SELECT t.*, r.number AS round_number, r.game_id, g.channel_id, "
    "(SELECT COUNT(*) FROM spectrum_turns c WHERE c.round_id = t.round_id) AS turn_count, "
    "gp.user_id AS giver_user_id "
    "FROM spectrum_turns t "
    "JOIN spectrum_rounds r ON r.id = t.round_id "
    "JOIN spectrum_games g ON g.id = r.game_id "
    "JOIN spectrum_players gp ON gp.id = t.giver_id "
)
CLOSE_GUARDS = {
    "revealed": "clue IS NOT NULL AND closed_at IS NULL",
    "no_clue": "clue IS NULL AND closed_at IS NULL AND started_at IS NOT NULL",
    "skipped": "clue IS NULL AND closed_at IS NULL",
    "abandoned": "closed_at IS NULL",
}
SCORED = (
    "SELECT t.id, r.game_id, gp.user_id, t.left_end, t.right_end, t.target, t.clue, t.closed_at "
    "FROM spectrum_turns t "
    "JOIN spectrum_rounds r ON r.id = t.round_id "
    "JOIN spectrum_games g ON g.id = r.game_id "
    "JOIN spectrum_players gp ON gp.id = t.giver_id "
    "WHERE t.outcome = 'revealed' "
)
GUESSES = (
    "SELECT gs.turn_id, p.user_id, gs.value FROM spectrum_guesses gs "
    "JOIN spectrum_players p ON p.id = gs.guesser_id "
    "JOIN spectrum_turns t ON t.id = gs.turn_id "
    "JOIN spectrum_rounds r ON r.id = t.round_id "
    "JOIN spectrum_games g ON g.id = r.game_id "
)


def _scored(row, guesses: dict[int, tuple[tuple[int, int], ...]]) -> ScoredTurn:
    return ScoredTurn(
        row["user_id"],
        row["left_end"],
        row["right_end"],
        row["target"],
        row["clue"],
        row["closed_at"],
        guesses.get(row["id"], ()),
    )


class GameFull(GameError):
    def __init__(self) -> None:
        super().__init__(f"The game is full ({MAX_PLAYERS} players).")


class SpectrumRepo(BaseRepo):
    async def create_game(
        self, guild_id: int, name: str, created_by: int, channel_id: int
    ) -> aiosqlite.Row:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO spectrum_games "
                "(guild_id, name, status, max_rounds, channel_id, created_by, host_id, created_at) "
                "VALUES (?, ?, 'open', ?, ?, ?, ?, ?)",
                (guild_id, name, DEFAULT_ROUNDS, channel_id, created_by, created_by, now_iso()),
            )
            game_id = cur.lastrowid
        game = await self.get_game(game_id)
        assert game is not None
        return game

    async def get_game(self, game_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM spectrum_games WHERE id = ?", (game_id,))

    async def get_current_game(self, guild_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM spectrum_games WHERE guild_id = ? AND status != 'finished'",
            (guild_id,),
        )

    async def start_game(self, game_id: int, max_rounds: int) -> bool:
        """Mark an open game active. False when it was not open (already started or ended)."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE spectrum_games SET status = 'active', started_at = ?, max_rounds = ? "
                "WHERE id = ? AND status = 'open'",
                (now_iso(), max_rounds, game_id),
            )
            return cur.rowcount == 1

    async def set_host(self, game_id: int, user_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE spectrum_games SET host_id = ? WHERE id = ?", (user_id, game_id)
            )

    async def set_lobby_message(self, game_id: int, message_id: int | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE spectrum_games SET lobby_message_id = ? WHERE id = ?",
                (message_id, game_id),
            )

    async def finish_game(self, game_id: int) -> bool:
        """Finish the game, abandon a turn that is still open and close an open round.

        False when it was already finished.
        """
        stamp = now_iso()
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE spectrum_games SET status = 'finished', finished_at = ? "
                "WHERE id = ? AND status != 'finished'",
                (stamp, game_id),
            )
            if cur.rowcount == 0:
                return False
            await conn.execute(
                "UPDATE spectrum_turns SET closed_at = ?, outcome = 'abandoned' "
                "WHERE started_at IS NOT NULL AND closed_at IS NULL "
                "AND round_id IN (SELECT id FROM spectrum_rounds WHERE game_id = ?)",
                (stamp, game_id),
            )
            await conn.execute(
                "UPDATE spectrum_rounds SET closed_at = ? WHERE game_id = ? AND closed_at IS NULL",
                (stamp, game_id),
            )
            return True

    async def join(self, game_id: int, user_id: int) -> tuple[aiosqlite.Row, bool]:
        """Add a player, or bring back one who left. The bool is True for a rejoin.

        Raises AlreadyJoined when the player is in the game, GameFull at the player cap.
        """
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT * FROM spectrum_players WHERE game_id = ? AND user_id = ?",
                (game_id, user_id),
            ) as cur:
                existing = await cur.fetchone()
            if existing is not None and existing["left_at"] is None:
                raise AlreadyJoined()
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM spectrum_players WHERE game_id = ? AND left_at IS NULL",
                (game_id,),
            ) as cur:
                active = (await cur.fetchone())["n"]
            if active >= MAX_PLAYERS:
                raise GameFull()
            if existing is None:
                await conn.execute(
                    "INSERT INTO spectrum_players (game_id, user_id, joined_at) VALUES (?, ?, ?)",
                    (game_id, user_id, now_iso()),
                )
            else:
                await conn.execute(
                    "UPDATE spectrum_players SET left_at = NULL, joined_at = ? WHERE id = ?",
                    (now_iso(), existing["id"]),
                )
        player = await self.get_player(game_id, user_id)
        assert player is not None
        return player, existing is not None

    async def leave(self, player_id: int) -> bool:
        """Mark a player as out. False when they had already left."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE spectrum_players SET left_at = ? WHERE id = ? AND left_at IS NULL",
                (now_iso(), player_id),
            )
            return cur.rowcount == 1

    async def get_player(self, game_id: int, user_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM spectrum_players WHERE game_id = ? AND user_id = ?",
            (game_id, user_id),
        )

    async def get_player_by_id(self, player_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM spectrum_players WHERE id = ?", (player_id,))

    async def list_players(self, game_id: int, active_only: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM spectrum_players WHERE game_id = ?"
        if active_only:
            sql += " AND left_at IS NULL"
        return await self._all(sql + " ORDER BY joined_at, id", (game_id,))

    async def begin_round(
        self, game_id: int, scales: Sequence[Scale], rng: random.Random
    ) -> aiosqlite.Row | None:
        """Open the next round with one waiting turn per player in the game, in a random order.

        Each turn gets a scale and its secret target now, so the target never changes.

        Returns None, writing nothing, unless the game is active and has no open round.
        """
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT (SELECT COUNT(*) FROM spectrum_rounds n WHERE n.game_id = g.id) AS played "
                "FROM spectrum_games g WHERE g.id = ? AND g.status = 'active' "
                "AND NOT EXISTS (SELECT 1 FROM spectrum_rounds r "
                "WHERE r.game_id = g.id AND r.closed_at IS NULL)",
                (game_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "INSERT INTO spectrum_rounds (game_id, number, started_at) VALUES (?, ?, ?)",
                (game_id, row["played"] + 1, now_iso()),
            )
            round_id = cur.lastrowid
            async with conn.execute(
                "SELECT id FROM spectrum_players WHERE game_id = ? AND left_at IS NULL "
                "ORDER BY joined_at, id",
                (game_id,),
            ) as cur:
                player_ids = [player["id"] for player in await cur.fetchall()]
            rng.shuffle(player_ids)
            for index, player_id in enumerate(player_ids):
                scale = scales[index % len(scales)]
                await conn.execute(
                    "INSERT INTO spectrum_turns "
                    "(round_id, giver_id, position, left_end, right_end, target) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (round_id, player_id, index + 1, scale.left, scale.right, rng.randint(1, 100)),
                )
        rnd = await self.get_round(round_id)
        assert rnd is not None
        return rnd

    async def add_turn(self, round_id: int, player_id: int, scale: Scale, target: int) -> bool:
        """Give a late joiner a turn at the end of an open round.

        False, writing nothing, when the round has closed or the player already has a turn in it.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO spectrum_turns "
                "(round_id, giver_id, position, left_end, right_end, target) "
                "SELECT r.id, ?, "
                "COALESCE((SELECT MAX(position) FROM spectrum_turns WHERE round_id = r.id), 0) + 1, "
                "?, ?, ? FROM spectrum_rounds r WHERE r.id = ? AND r.closed_at IS NULL "
                "ON CONFLICT(round_id, giver_id) DO NOTHING",
                (player_id, scale.left, scale.right, target, round_id),
            )
            return cur.rowcount == 1

    async def has_turn(self, round_id: int, player_id: int) -> bool:
        row = await self._one(
            "SELECT 1 FROM spectrum_turns WHERE round_id = ? AND giver_id = ?",
            (round_id, player_id),
        )
        return row is not None

    async def waiting_turn(self, round_id: int, player_id: int) -> bool:
        """True when the player's turn in this round has not come up yet."""
        row = await self._one(
            "SELECT 1 FROM spectrum_turns WHERE round_id = ? AND giver_id = ? "
            "AND started_at IS NULL AND closed_at IS NULL",
            (round_id, player_id),
        )
        return row is not None

    async def get_round(self, round_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM spectrum_rounds WHERE id = ?", (round_id,))

    async def get_current_round(self, game_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM spectrum_rounds WHERE game_id = ? ORDER BY id DESC LIMIT 1",
            (game_id,),
        )

    async def last_closed_round(self, game_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM spectrum_rounds WHERE game_id = ? AND closed_at IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (game_id,),
        )

    async def rounds_played(self, game_id: int) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS n FROM spectrum_rounds WHERE game_id = ? AND closed_at IS NOT NULL",
            (game_id,),
        )
        return row["n"] if row else 0

    async def used_scales(self, game_id: int) -> set[Scale]:
        rows = await self._all(
            "SELECT DISTINCT t.left_end, t.right_end FROM spectrum_turns t "
            "JOIN spectrum_rounds r ON r.id = t.round_id WHERE r.game_id = ?",
            (game_id,),
        )
        return {Scale(r["left_end"], r["right_end"]) for r in rows}

    async def get_turn(self, turn_id: int) -> aiosqlite.Row | None:
        """A turn, with its round's number, its game and channel, and the giver's user id."""
        return await self._one(TURN + "WHERE t.id = ?", (turn_id,))

    async def turn_context(self, turn_id: int, user_id: int) -> aiosqlite.Row | None:
        """One read of everything a button or a submit needs: game, round, turn, giver, caller.

        None when the turn doesn't exist. ``player_id`` is NULL for a user who isn't in the game.
        """
        return await self._one(
            "SELECT g.id AS game_id, g.status, g.guild_id, g.channel_id, "
            "r.id AS round_id, r.number AS round_number, r.closed_at AS round_closed_at, "
            "t.id AS turn_id, t.giver_id, t.left_end, t.right_end, t.target, t.clue, "
            "t.started_at, t.clue_deadline_at, t.guess_deadline_at, t.closed_at, "
            "gp.user_id AS giver_user_id, gp.left_at AS giver_left_at, "
            "p.id AS player_id, p.left_at, "
            "tg.player_id IS NOT NULL AS may_guess, "
            "gs.value AS my_guess "
            "FROM spectrum_turns t "
            "JOIN spectrum_rounds r ON r.id = t.round_id "
            "JOIN spectrum_games g ON g.id = r.game_id "
            "JOIN spectrum_players gp ON gp.id = t.giver_id "
            "LEFT JOIN spectrum_players p ON p.game_id = g.id AND p.user_id = ? "
            "LEFT JOIN spectrum_turn_guessers tg ON tg.turn_id = t.id AND tg.player_id = p.id "
            "LEFT JOIN spectrum_guesses gs ON gs.turn_id = t.id AND gs.guesser_id = p.id "
            "WHERE t.id = ?",
            (user_id, turn_id),
        )

    async def next_turn(self, round_id: int) -> aiosqlite.Row | None:
        """The next turn of the round that hasn't come up yet."""
        return await self._one(
            TURN + "WHERE t.round_id = ? AND t.started_at IS NULL AND t.closed_at IS NULL "
            "ORDER BY t.position LIMIT 1",
            (round_id,),
        )

    async def open_turn(self, turn_id: int, clue_deadline_at: str) -> bool:
        """Start a turn's clue step.

        False, writing nothing, unless it is waiting, its round is open, the game is running and
        no other turn in the round is open.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE spectrum_turns SET started_at = ?, clue_deadline_at = ? "
                "WHERE id = ? AND started_at IS NULL AND closed_at IS NULL "
                "AND EXISTS (SELECT 1 FROM spectrum_rounds r "
                "JOIN spectrum_games g ON g.id = r.game_id "
                "WHERE r.id = spectrum_turns.round_id AND r.closed_at IS NULL "
                "AND g.status = 'active') "
                "AND NOT EXISTS (SELECT 1 FROM spectrum_turns o "
                "WHERE o.round_id = spectrum_turns.round_id "
                "AND o.started_at IS NOT NULL AND o.closed_at IS NULL)",
                (now_iso(), clue_deadline_at, turn_id),
            )
            return cur.rowcount == 1

    async def current_turn(self, round_id: int) -> aiosqlite.Row | None:
        """The turn that is waiting for its clue or its guesses, or None."""
        return await self._one(
            TURN + "WHERE t.round_id = ? AND t.started_at IS NOT NULL AND t.closed_at IS NULL",
            (round_id,),
        )

    async def set_turn_message(self, turn_id: int, message_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE spectrum_turns SET turn_message_id = ? WHERE id = ?", (message_id, turn_id)
            )

    async def set_guess_message(self, turn_id: int, message_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE spectrum_turns SET guess_message_id = ? WHERE id = ?", (message_id, turn_id)
            )

    async def save_clue(
        self, turn_id: int, giver_player_id: int, clue: str, guess_deadline_at: str
    ) -> bool:
        """Save the clue, open guessing and record who may guess: the others in the game now.

        False, writing nothing, unless the turn is waiting for its clue, the game is running and
        the giver is this player and still in the game.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE spectrum_turns SET clue = ?, clue_at = ?, guess_deadline_at = ? "
                "WHERE id = ? AND giver_id = ? AND started_at IS NOT NULL AND clue IS NULL "
                "AND closed_at IS NULL "
                "AND EXISTS (SELECT 1 FROM spectrum_rounds r "
                "JOIN spectrum_games g ON g.id = r.game_id "
                "WHERE r.id = spectrum_turns.round_id AND g.status = 'active') "
                "AND EXISTS (SELECT 1 FROM spectrum_players p "
                "WHERE p.id = spectrum_turns.giver_id AND p.left_at IS NULL)",
                (clue, now_iso(), guess_deadline_at, turn_id, giver_player_id),
            )
            if cur.rowcount == 0:
                return False
            cur = await conn.execute(
                "INSERT INTO spectrum_turn_guessers (turn_id, player_id) "
                "SELECT t.id, p.id FROM spectrum_turns t "
                "JOIN spectrum_rounds r ON r.id = t.round_id "
                "JOIN spectrum_players p ON p.game_id = r.game_id "
                "WHERE t.id = ? AND p.left_at IS NULL AND p.id != t.giver_id",
                (turn_id,),
            )
            await conn.execute(
                "UPDATE spectrum_turns SET eligible_guessers = ? WHERE id = ?",
                (cur.rowcount, turn_id),
            )
            return True

    async def save_guess(self, turn_id: int, guesser_id: int, value: int) -> bool:
        """Record or change a guess.

        False, writing nothing, unless the turn is open for guesses and the player was in the
        game when its clue was saved.
        """
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO spectrum_guesses (turn_id, guesser_id, value, made_at) "
                "SELECT ?, ?, ?, ? WHERE EXISTS "
                "(SELECT 1 FROM spectrum_turns "
                "WHERE id = ? AND clue IS NOT NULL AND closed_at IS NULL) "
                "AND EXISTS (SELECT 1 FROM spectrum_turn_guessers "
                "WHERE turn_id = ? AND player_id = ?) "
                "ON CONFLICT(turn_id, guesser_id) DO UPDATE SET "
                "value = excluded.value, made_at = excluded.made_at",
                (turn_id, guesser_id, value, now_iso(), turn_id, turn_id, guesser_id),
            )
            return cur.rowcount == 1

    async def get_guess(self, turn_id: int, guesser_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM spectrum_guesses WHERE turn_id = ? AND guesser_id = ?",
            (turn_id, guesser_id),
        )

    async def guess_progress(self, turn_id: int) -> tuple[int, int]:
        """(guesses in, guessers expected), counting only the turn's guessers still in the game."""
        row = await self._one(
            "SELECT COUNT(*) AS expected, COUNT(gs.guesser_id) AS guessed "
            "FROM spectrum_turn_guessers tg "
            "JOIN spectrum_players p ON p.id = tg.player_id "
            "LEFT JOIN spectrum_guesses gs "
            "ON gs.turn_id = tg.turn_id AND gs.guesser_id = tg.player_id "
            "WHERE tg.turn_id = ? AND p.left_at IS NULL",
            (turn_id,),
        )
        return (row["guessed"], row["expected"]) if row else (0, 0)

    async def turn_guesses(self, turn_id: int) -> list[aiosqlite.Row]:
        """Every stored guess, leavers' included: ``user_id`` and ``value``, lowest value first."""
        return await self._all(
            "SELECT p.user_id, gs.value FROM spectrum_guesses gs "
            "JOIN spectrum_players p ON p.id = gs.guesser_id "
            "WHERE gs.turn_id = ? ORDER BY gs.value, p.user_id",
            (turn_id,),
        )

    async def close_turn(self, turn_id: int, outcome: str) -> bool:
        """Close a turn with an outcome. False when its state doesn't allow that (lost a race).

        ``revealed`` needs a clue. ``no_clue`` and ``skipped`` need there to be none, so neither
        can undo a clue saved a moment earlier. ``abandoned`` closes any open turn.
        """
        if outcome not in CLOSE_GUARDS:
            raise ValueError(outcome)
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE spectrum_turns SET closed_at = ?, outcome = ? "
                f"WHERE id = ? AND {CLOSE_GUARDS[outcome]}",
                (now_iso(), outcome, turn_id),
            )
            return cur.rowcount == 1

    async def close_round(self, round_id: int) -> bool:
        """Close a round. False when it was already closed or still has a turn to play."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE spectrum_rounds SET closed_at = ? WHERE id = ? AND closed_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM spectrum_turns t "
                "WHERE t.round_id = spectrum_rounds.id AND t.closed_at IS NULL)",
                (now_iso(), round_id),
            )
            return cur.rowcount == 1

    async def list_overdue_clues(self, now: str) -> list[aiosqlite.Row]:
        """Turns of running games still waiting for a clue at or past the deadline."""
        return await self._all(
            TURN + "WHERE t.started_at IS NOT NULL AND t.clue IS NULL AND t.closed_at IS NULL "
            "AND t.clue_deadline_at <= ? AND g.status = 'active'",
            (now,),
        )

    async def list_overdue_guessing(self, now: str) -> list[aiosqlite.Row]:
        """Turns of running games still open for guesses at or past the deadline."""
        return await self._all(
            TURN + "WHERE t.clue IS NOT NULL AND t.closed_at IS NULL "
            "AND t.guess_deadline_at <= ? AND g.status = 'active'",
            (now,),
        )

    async def list_stalled_games(self, before: str) -> list[aiosqlite.Row]:
        """Running games with no open turn since ``before``.

        That is a game with no round yet, a closed round, or a round between two turns.
        """
        return await self._all(
            "SELECT g.* FROM spectrum_games g "
            "LEFT JOIN spectrum_rounds r "
            "ON r.id = (SELECT MAX(id) FROM spectrum_rounds WHERE game_id = g.id) "
            "WHERE g.status = 'active' "
            "AND NOT EXISTS (SELECT 1 FROM spectrum_turns t "
            "WHERE t.round_id = r.id AND t.started_at IS NOT NULL AND t.closed_at IS NULL) "
            "AND COALESCE(r.closed_at, "
            "(SELECT MAX(t.closed_at) FROM spectrum_turns t WHERE t.round_id = r.id), "
            "r.started_at, g.started_at) <= ?",
            (before,),
        )

    async def scored_turns(self, game_id: int, round_id: int | None = None) -> list[ScoredTurn]:
        """Revealed turns in play order, with their guesses. ``round_id`` narrows it to a round."""
        sql = SCORED + "AND r.game_id = ?"
        params: tuple[int, ...] = (game_id,)
        if round_id is not None:
            sql += " AND r.id = ?"
            params = (game_id, round_id)
        rows = await self._all(sql + " ORDER BY t.id", params)
        guesses = await self._guesses_by_turn("WHERE r.game_id = ?", game_id)
        return [_scored(r, guesses) for r in rows]

    async def _guesses_by_turn(
        self, scope: str, key: int
    ) -> dict[int, tuple[tuple[int, int], ...]]:
        """Every guess on the turns that ``scope`` selects, as (user id, value) by turn id."""
        rows = await self._all(f"{GUESSES}{scope} ORDER BY gs.turn_id, p.user_id", (key,))
        found: dict[int, tuple[tuple[int, int], ...]] = {}
        for r in rows:
            found[r["turn_id"]] = (*found.get(r["turn_id"], ()), (r["user_id"], r["value"]))
        return found

    async def revealed_guess_counts(self, game_id: int) -> list[tuple[int, int]]:
        """(guessers when the clue was saved, guesses stored) per revealed turn, in play order."""
        rows = await self._all(
            "SELECT COALESCE(t.eligible_guessers, 0) AS eligible, "
            "(SELECT COUNT(*) FROM spectrum_guesses gs WHERE gs.turn_id = t.id) AS guesses "
            "FROM spectrum_turns t JOIN spectrum_rounds r ON r.id = t.round_id "
            "WHERE r.game_id = ? AND t.outcome = 'revealed' ORDER BY t.id",
            (game_id,),
        )
        return [(r["eligible"], r["guesses"]) for r in rows]

    async def round_clue_counts(self, round_id: int) -> tuple[int, int]:
        """(turns that ran out of time with no clue, turns that got a clue) in a round."""
        row = await self._one(
            "SELECT COALESCE(SUM(outcome = 'no_clue'), 0) AS no_clue, COUNT(clue) AS clued "
            "FROM spectrum_turns WHERE round_id = ?",
            (round_id,),
        )
        return (row["no_clue"], row["clued"]) if row else (0, 0)

    async def abandoned_streak(self, game_id: int) -> int:
        """How many turns in a row were abandoned since the last one that closed another way."""
        row = await self._one(
            "SELECT COUNT(*) AS n FROM spectrum_turns t "
            "JOIN spectrum_rounds r ON r.id = t.round_id "
            "WHERE r.game_id = ? AND t.outcome = 'abandoned' "
            "AND t.id > COALESCE("
            "(SELECT MAX(t2.id) FROM spectrum_turns t2 "
            "JOIN spectrum_rounds r2 ON r2.id = t2.round_id "
            "WHERE r2.game_id = ? AND t2.outcome IS NOT NULL AND t2.outcome != 'abandoned'), 0)",
            (game_id, game_id),
        )
        return row["n"] if row else 0

    async def game_user_ids(self, game_id: int, round_id: int | None = None) -> list[int]:
        """User ids of everyone who gave a clue or could guess on one, leavers included.

        ``round_id`` narrows it to one round.
        """
        scope = "r.game_id = ?"
        params: tuple[int, ...] = (game_id,)
        if round_id is not None:
            scope += " AND r.id = ?"
            params = (game_id, round_id)
        rows = await self._all(
            "SELECT p.user_id FROM spectrum_turns t "
            "JOIN spectrum_rounds r ON r.id = t.round_id "
            "JOIN spectrum_players p ON p.id = t.giver_id "
            f"WHERE {scope} AND t.clue IS NOT NULL "
            "UNION "
            "SELECT p.user_id FROM spectrum_turn_guessers tg "
            "JOIN spectrum_turns t ON t.id = tg.turn_id "
            "JOIN spectrum_rounds r ON r.id = t.round_id "
            "JOIN spectrum_players p ON p.id = tg.player_id "
            f"WHERE {scope} ORDER BY 1",
            params + params,
        )
        return [r["user_id"] for r in rows]

    async def finished_turns(self, guild_id: int) -> dict[int, list[ScoredTurn]]:
        """Revealed turns of this server's finished games, by game id, in play order."""
        rows = await self._all(
            SCORED + "AND g.guild_id = ? AND g.status = 'finished' ORDER BY g.id, t.id",
            (guild_id,),
        )
        guesses = await self._guesses_by_turn(
            "WHERE g.guild_id = ? AND g.status = 'finished'", guild_id
        )
        games: dict[int, list[ScoredTurn]] = {}
        for r in rows:
            games.setdefault(r["game_id"], []).append(_scored(r, guesses))
        return games

    async def games_played(self, guild_id: int) -> dict[int, int]:
        """Finished games per user id.

        A game counts for a user who gave the clue on, or could guess on, at least one revealed
        turn, so a game ended during its first clue step counts for nobody.
        """
        rows = await self._all(
            "SELECT user_id, COUNT(DISTINCT game_id) AS played FROM ("
            "SELECT p.user_id, r.game_id FROM spectrum_turns t "
            "JOIN spectrum_rounds r ON r.id = t.round_id "
            "JOIN spectrum_games g ON g.id = r.game_id "
            "JOIN spectrum_players p ON p.id = t.giver_id "
            "WHERE g.guild_id = ? AND g.status = 'finished' AND t.outcome = 'revealed' "
            "UNION "
            "SELECT p.user_id, r.game_id FROM spectrum_turn_guessers tg "
            "JOIN spectrum_turns t ON t.id = tg.turn_id "
            "JOIN spectrum_rounds r ON r.id = t.round_id "
            "JOIN spectrum_games g ON g.id = r.game_id "
            "JOIN spectrum_players p ON p.id = tg.player_id "
            "WHERE g.guild_id = ? AND g.status = 'finished' AND t.outcome = 'revealed'"
            ") GROUP BY user_id",
            (guild_id, guild_id),
        )
        return {r["user_id"]: r["played"] for r in rows}

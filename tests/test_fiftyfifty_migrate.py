"""A database from before players wrote both options keeps working after the upgrade."""

import pytest
from fiftyfifty_fakes import CHANNEL_ID, click_for, make_bot

from gaminator.core.db import Database
from gaminator.games import prepare_database
from gaminator.games.fiftyfifty import SPEC, migrate
from gaminator.games.fiftyfifty.services import game as flow
from gaminator.games.fiftyfifty.services.rules import Standing
from gaminator.games.fiftyfifty.write import handle_write_click

OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS fiftyfifty_games (
  id INTEGER PRIMARY KEY,
  guild_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open','active','finished')),
  max_rounds INTEGER NOT NULL DEFAULT 1,
  channel_id INTEGER NOT NULL,
  lobby_message_id INTEGER,
  created_by INTEGER NOT NULL,
  host_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS fiftyfifty_one_current_game_per_guild
  ON fiftyfifty_games(guild_id) WHERE status != 'finished';

CREATE TABLE IF NOT EXISTS fiftyfifty_players (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES fiftyfifty_games(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  joined_at TEXT NOT NULL,
  left_at TEXT,
  UNIQUE(game_id, user_id)
);
CREATE INDEX IF NOT EXISTS fiftyfifty_players_by_user
  ON fiftyfifty_players(user_id);

CREATE TABLE IF NOT EXISTS fiftyfifty_rounds (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES fiftyfifty_games(id) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  write_deadline_at TEXT NOT NULL,
  write_channel_id INTEGER,
  write_message_id INTEGER,
  write_closed_at TEXT,
  closed_at TEXT,
  abandoned INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS fiftyfifty_round_number
  ON fiftyfifty_rounds(game_id, number) WHERE abandoned = 0;
CREATE INDEX IF NOT EXISTS fiftyfifty_open_writing
  ON fiftyfifty_rounds(write_deadline_at) WHERE write_closed_at IS NULL;

CREATE TABLE IF NOT EXISTS fiftyfifty_round_players (
  round_id INTEGER NOT NULL REFERENCES fiftyfifty_rounds(id) ON DELETE CASCADE,
  player_id INTEGER NOT NULL REFERENCES fiftyfifty_players(id) ON DELETE CASCADE,
  PRIMARY KEY (round_id, player_id)
);

CREATE TABLE IF NOT EXISTS fiftyfifty_questions (
  id INTEGER PRIMARY KEY,
  round_id INTEGER NOT NULL REFERENCES fiftyfifty_rounds(id) ON DELETE CASCADE,
  writer_id INTEGER NOT NULL REFERENCES fiftyfifty_players(id) ON DELETE CASCADE,
  option_a TEXT NOT NULL,
  option_b TEXT NOT NULL,
  answer TEXT,
  answered_at TEXT,
  dm_prompted_at TEXT,
  position INTEGER,
  started_at TEXT,
  deadline_at TEXT,
  vote_channel_id INTEGER,
  vote_message_id INTEGER,
  closed_at TEXT,
  abandoned INTEGER NOT NULL DEFAULT 0,
  UNIQUE(round_id, writer_id)
);
CREATE INDEX IF NOT EXISTS fiftyfifty_open_questions
  ON fiftyfifty_questions(deadline_at) WHERE started_at IS NOT NULL AND closed_at IS NULL;

CREATE TABLE IF NOT EXISTS fiftyfifty_votes (
  question_id INTEGER NOT NULL REFERENCES fiftyfifty_questions(id) ON DELETE CASCADE,
  voter_id INTEGER NOT NULL REFERENCES fiftyfifty_players(id) ON DELETE CASCADE,
  choice TEXT NOT NULL CHECK (choice IN ('a','b')),
  cast_at TEXT NOT NULL,
  PRIMARY KEY (question_id, voter_id)
);
"""
T = "2026-11-20T20:0{}:00+00:00"
LATER = "2999-01-01T00:00:00+00:00"
OLD_ROWS = f"""
INSERT INTO fiftyfifty_games VALUES
  (1, 1, 'Old game', 'finished', 1, {CHANNEL_ID}, NULL, 11, 11, '{T.format(0)}',
   '{T.format(1)}', '{T.format(5)}'),
  (2, 2, 'Mid game', 'active', 1, {CHANNEL_ID}, NULL, 21, 21, '{T.format(0)}',
   '{T.format(1)}', NULL);
INSERT INTO fiftyfifty_players VALUES
  (1, 1, 11, '{T.format(0)}', NULL),
  (2, 1, 12, '{T.format(0)}', NULL),
  (3, 1, 13, '{T.format(0)}', NULL),
  (4, 2, 21, '{T.format(0)}', NULL),
  (5, 2, 22, '{T.format(0)}', NULL),
  (6, 2, 23, '{T.format(0)}', NULL);
INSERT INTO fiftyfifty_rounds VALUES
  (1, 1, 1, '{T.format(1)}', '{T.format(3)}', NULL, NULL, '{T.format(2)}', '{T.format(5)}', 0),
  (2, 2, 1, '{T.format(1)}', '{LATER}', NULL, NULL, NULL, NULL, 0);
INSERT INTO fiftyfifty_round_players VALUES (1, 1), (1, 2), (1, 3), (2, 4), (2, 5), (2, 6);
INSERT INTO fiftyfifty_questions VALUES
  (1, 1, 1, 'eat ____ every day', 'never eat pizza again', 'cold *soup*', '{T.format(1)}',
   NULL, 1, '{T.format(2)}', '{T.format(4)}', NULL, NULL, '{T.format(3)}', 0),
  (2, 1, 2, 'have a theme song', '____', 'nap', '{T.format(1)}',
   NULL, 2, '{T.format(3)}', '{T.format(5)}', NULL, NULL, '{T.format(4)}', 0),
  (3, 1, 3, 'live next door to ____', 'live far away', NULL, NULL,
   NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0),
  (4, 2, 4, 'be famous for ____', 'be rich and unknown', 'juggling', '{T.format(1)}',
   NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0),
  (5, 2, 5, 'only travel by ____', 'only travel on foot', NULL, NULL,
   NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0),
  (6, 2, 6, 'give up ____ for a year', 'give up your phone', NULL, NULL,
   NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
INSERT INTO fiftyfifty_votes VALUES
  (1, 2, 'a', '{T.format(2)}'),
  (1, 3, 'b', '{T.format(2)}'),
  (2, 1, 'a', '{T.format(3)}'),
  (2, 3, 'a', '{T.format(3)}');
"""
OLD_SOUP = "Would you rather eat **cold \\*soup\\*** every day or never eat pizza again?"


async def columns(db, table):
    async with db.conn.execute(f"PRAGMA table_info({table})") as cur:
        return [row["name"] for row in await cur.fetchall()]


@pytest.fixture
async def old_db(tmp_path):
    """A database as the bot left it before the upgrade, with one finished and one running game."""
    database = Database(str(tmp_path / "old.db"))
    await database.connect()
    await database.conn.executescript(OLD_SCHEMA)
    await database.conn.executescript(OLD_ROWS)
    assert "topic" not in await columns(database, "fiftyfifty_questions")
    assert "fiftyfifty_guesses" not in await database.table_names()
    yield database
    await database.close()


async def test_migrate_adds_the_topic_column_and_the_guesses_table(old_db):
    assert SPEC.migrate is migrate
    await prepare_database(old_db)
    await prepare_database(old_db)
    assert (await columns(old_db, "fiftyfifty_questions")).count("topic") == 1
    assert await columns(old_db, "fiftyfifty_guesses") == [
        "question_id",
        "voter_id",
        "a_votes",
        "made_at",
    ]
    async with old_db.conn.execute(
        "SELECT id, option_a, answer, topic FROM fiftyfifty_questions ORDER BY id LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    assert tuple(row) == (1, "eat ____ every day", "cold *soup*", None)


async def test_migrate_on_a_fresh_database_does_nothing(tmp_path):
    database = Database(str(tmp_path / "fresh.db"))
    await database.connect()
    await migrate(database)
    assert "fiftyfifty_questions" not in await database.table_names()
    await prepare_database(database)
    assert "topic" in await columns(database, "fiftyfifty_questions")
    await database.close()


async def test_old_finished_game_still_scores_and_renders(old_db):
    await prepare_database(old_db)
    bot = make_bot(old_db)
    repo = bot.repo
    first, second = await repo.closed_questions(1)
    assert (first.writer_id, first.a, first.b, first.points) == (11, 1, 1, 100)
    assert (first.answer, first.guesses, first.guess_points) == ("cold *soup*", (), [])
    assert (second.writer_id, second.a, second.b, second.points) == (12, 2, 0, 0)
    assert await repo.unwritten_user_ids(1) == [13]
    assert await repo.last_round_answers(1) == 2
    assert await repo.asked_vote_totals(1) == [2, 2]
    assert await repo.question_count(1) == 2
    assert await repo.write_progress(1) == (2, 3)

    games = await repo.finished_questions(1)
    assert list(games) == [1] and games[1] == [first, second]
    assert await repo.games_played(1) == {11: 1, 12: 1, 13: 1}
    board = (await flow.leaderboard_reply(bot, 1, 11)).split("\n")
    assert board == [
        "🏆 **Fifty-Fifty: all-time standings**",
        "`1.` **U11**: 1 game won, 100 points, 1 played",
        "`2.` **U12**: 0 games won, 0 points, 1 played",
        "`3.` **U13**: 0 games won, 0 points, 1 played",
    ]
    member = (await flow.leaderboard_reply(bot, 1, 11, member_id=11)).split("\n")
    assert member[:3] == [
        "**U11**: 1 game won, 100 points, 1 played",
        "1 question written, 100 points on average",
        "Best splits:",
    ]
    assert member[3].startswith(f"• 100: {OLD_SOUP} (1 to 1, <t:") and len(member) == 4
    other = await flow.leaderboard_reply(bot, 1, 11, member_id=12)
    assert "• 0: Would you rather have a theme song or **nap**? (2 to 0, <t:" in other

    game = await repo.get_game(1)
    reveal = await flow.reveal_for(bot, game, await repo.get_question(1))
    assert reveal.split("\n") == [
        f"📊 **Question 1**: {OLD_SOUP}",
        "**A 1, B 1**: a 50/50 split.",
        "🅰️ **U12**",
        "🅱️ **U13**",
        "✍️ Written by **U11**: **100** points.",
        "🎯 Nobody guessed the split.",
    ]
    names = {11: "U11", 12: "U12", 13: "U13"}
    recap = flow.recap_text(game, [(11, 100), (12, 0), (13, 0)], names, 1, first)
    assert f"⚖️ Best split: {OLD_SOUP} (1 to 1, by U11)" in recap


async def test_old_and_new_games_share_the_standings(old_db):
    await prepare_database(old_db)
    bot = make_bot(old_db)
    game = await bot.repo.create_game(1, "New game", 11, CHANNEL_ID)
    for uid in (11, 12, 13, 14):
        await bot.repo.join(game["id"], uid)
    await flow.begin_game(bot, game, 1)
    rnd = await bot.repo.get_current_round(game["id"])
    assert await flow.submit_question(bot, rnd["id"], 12, "tea", "coffee")
    await bot.repo.close_writing(rnd["id"], flow.RNG)
    game = await bot.repo.get_game(game["id"])
    await flow.advance(bot, game)
    q = await bot.repo.current_question(rnd["id"])
    players = {uid: await bot.repo.get_player(game["id"], uid) for uid in (11, 13, 14)}
    for uid, choice, guess in ((11, "a", 2), (13, "b", 1), (14, "a", 0)):
        await bot.repo.cast_vote(q["id"], players[uid]["id"], choice)
        await bot.repo.set_guess(q["id"], players[uid]["id"], guess)
    assert await flow.close_question(bot, game, q)
    assert (await bot.repo.get_game(game["id"]))["status"] == "finished"
    rows = flow.standings(await bot.repo.finished_questions(1), await bot.repo.games_played(1))
    assert rows == [
        Standing(11, games_won=1, points=150, questions=1, games_played=2, guess_points=50),
        Standing(12, games_won=1, points=100, questions=2, games_played=2),
        Standing(13, games_won=0, points=0, questions=0, games_played=2),
        Standing(14, games_won=0, points=0, questions=0, games_played=1),
    ]
    board = bot.channel.texts[-1]
    assert "`1.` **U11**: 1 game won, 150 points, 2 played" in board
    assert "`2.` **U12**: 1 game won, 100 points, 2 played" in board


async def test_old_round_caught_mid_writing_plays_on(old_db):
    await prepare_database(old_db)
    bot = make_bot(old_db)
    repo = bot.repo
    game = await repo.get_game(2)
    assert await repo.write_progress(2) == (1, 3)
    status = await flow.status_reply(bot, game, 21)
    assert "Your question: Would you rather be famous for **juggling** or be rich and unknown?" in (
        status
    )
    waiting = (await flow.status_reply(bot, game, 22)).split("\n")
    assert waiting[-1] == (
        "You haven't written your question yet. Press **Write** on the round message."
    )
    click = click_for(bot, 21)
    await handle_write_click(click, 2)
    (modal,) = click.response.send_modal.await_args.args
    assert "Topic idea" not in modal.children[0].content
    assert [modal.children[n].component.default for n in (1, 2)] == [None, None]

    saved = await flow.submit_question(bot, 2, 22, "walk", "bike")
    assert saved.split("\n")[1] == "Would you rather walk or bike?"
    assert await repo.write_progress(2) == (2, 3)
    again = await flow.submit_question(bot, 2, 21, "sing", "dance")
    assert again.split("\n")[1] == "Would you rather sing or dance?"
    rewritten = await repo.get_question(4)
    assert (rewritten["option_a"], rewritten["option_b"], rewritten["answer"]) == (
        "sing",
        "dance",
        None,
    )
    assert await repo.write_progress(2) == (2, 3)
    async with repo.db.transaction() as conn:
        await conn.execute(
            "UPDATE fiftyfifty_questions SET option_a = 'be famous for ____', "
            "option_b = 'be rich and unknown', answer = 'juggling' WHERE id = 4"
        )
    assert await flow.close_writing(bot, game, await repo.get_round(2), cause="deadline")
    asked = [t for t in bot.channel.texts if "**Question 1 of 2**" in t]
    assert len(asked) == 1
    old_asked = "> 🅰️ be famous for **juggling**\n> 🅱️ be rich and unknown\n"
    first = await repo.current_question(2)
    assert (old_asked in asked[0]) == (first["id"] == 4)
    assert await repo.unwritten_user_ids(2) == [23]

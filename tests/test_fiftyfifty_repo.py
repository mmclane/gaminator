"""Fifty-Fifty database access."""

import random

import aiosqlite
import pytest
from fiftyfifty_fakes import DECK

from gaminator.core.repo import AlreadyJoined
from gaminator.games.fiftyfifty.repo import FiftyFiftyRepo, GameFull

LATER = "2999-01-01T00:00:00+00:00"
EARLIER = "2000-01-01T00:00:00+00:00"
USERS = (11, 12, 13)


@pytest.fixture
async def frepo(db):
    return FiftyFiftyRepo(db)


@pytest.fixture
async def fgame(frepo):
    return await frepo.create_game(guild_id=1, name="Test", created_by=11, channel_id=555)


@pytest.fixture
async def frunning(frepo, fgame):
    """Three players (user ids 11, 12, 13), game started, round 1 writing. Players by user id."""
    for uid in USERS:
        await frepo.join(fgame["id"], uid)
    await frepo.start_game(fgame["id"], 3)
    await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    return {uid: await frepo.get_player(fgame["id"], uid) for uid in USERS}


async def qid(frepo, rnd, uid):
    return (await frepo.write_context(rnd["id"], uid))["question_id"]


async def to_voting(frepo, game, writers=USERS):
    """Have ``writers`` write their questions and close writing. Returns the round."""
    rnd = await frepo.get_current_round(game["id"])
    for uid in writers:
        assert await frepo.write_options(await qid(frepo, rnd, uid), f"blank {uid}", "or not")
    assert await frepo.close_writing(rnd["id"], random.Random(1))
    return await frepo.get_round(rnd["id"])


async def open_next(frepo, rnd, deadline=LATER):
    q = await frepo.next_question(rnd["id"])
    assert await frepo.open_question(q["id"], deadline)
    return await frepo.get_question(q["id"])


async def run_sql(frepo, sql, params=()):
    async with frepo.db.transaction() as conn:
        await conn.execute(sql, params)


async def test_create_sets_host_and_channel(frepo, fgame):
    assert fgame["status"] == "open"
    assert fgame["host_id"] == 11 and fgame["created_by"] == 11
    assert fgame["channel_id"] == 555
    assert fgame["max_rounds"] == 1
    assert fgame["lobby_message_id"] is None
    await frepo.set_host(fgame["id"], 12)
    await frepo.set_lobby_message(fgame["id"], 777)
    game = await frepo.get_game(fgame["id"])
    assert game["host_id"] == 12 and game["lobby_message_id"] == 777


async def test_one_game_per_guild(frepo, fgame):
    with pytest.raises(aiosqlite.IntegrityError):
        await frepo.create_game(1, "Second", 12, 555)
    assert await frepo.finish_game(fgame["id"])
    assert await frepo.get_current_game(1) is None
    await frepo.create_game(1, "Second", 12, 555)
    await frepo.create_game(2, "Other server", 12, 556)


async def test_start_game_twice(frepo, fgame):
    assert await frepo.start_game(fgame["id"], 4)
    game = await frepo.get_game(fgame["id"])
    assert game["status"] == "active" and game["max_rounds"] == 4 and game["started_at"]
    assert not await frepo.start_game(fgame["id"], 2)
    assert (await frepo.get_game(fgame["id"]))["max_rounds"] == 4


async def test_join_and_duplicate(frepo, fgame):
    player, rejoined = await frepo.join(fgame["id"], 11)
    assert player["user_id"] == 11 and player["left_at"] is None and not rejoined
    assert (await frepo.get_player_by_id(player["id"]))["user_id"] == 11
    with pytest.raises(AlreadyJoined):
        await frepo.join(fgame["id"], 11)


async def test_join_cap(frepo, fgame):
    for uid in range(100, 112):
        await frepo.join(fgame["id"], uid)
    with pytest.raises(GameFull, match=r"The game is full \(12 players\)."):
        await frepo.join(fgame["id"], 999)
    assert await frepo.get_player(fgame["id"], 999) is None
    first = await frepo.get_player(fgame["id"], 100)
    await frepo.leave(first["id"])
    await frepo.join(fgame["id"], 999)
    with pytest.raises(GameFull):
        await frepo.join(fgame["id"], 100)


async def test_rejoin_reuses_row(frepo, fgame):
    first, _ = await frepo.join(fgame["id"], 11)
    await frepo.leave(first["id"])
    assert await frepo.list_players(fgame["id"], active_only=True) == []
    again, rejoined = await frepo.join(fgame["id"], 11)
    assert rejoined and again["id"] == first["id"] and again["left_at"] is None
    assert len(await frepo.list_players(fgame["id"])) == 1


async def test_leave_twice(frepo, fgame):
    player, _ = await frepo.join(fgame["id"], 11)
    assert await frepo.leave(player["id"])
    assert not await frepo.leave(player["id"])
    assert (await frepo.get_player(fgame["id"], 11))["left_at"] is not None


async def test_begin_round_assigns_distinct_topics(frepo, fgame):
    players = {uid: (await frepo.join(fgame["id"], uid))[0] for uid in USERS}
    await frepo.start_game(fgame["id"], 3)
    await frepo.leave(players[13]["id"])
    rnd = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    assert rnd["number"] == 1 and rnd["write_deadline_at"] == LATER and rnd["abandoned"] == 0
    assert rnd["write_closed_at"] is None and rnd["closed_at"] is None
    assert (await frepo.get_current_round(fgame["id"]))["id"] == rnd["id"]
    late, _ = await frepo.join(fgame["id"], 14)
    assert [p["user_id"] for p in await frepo.round_roster(rnd["id"])] == [11, 12]
    assert await frepo.on_roster(rnd["id"], players[11]["id"])
    assert not await frepo.on_roster(rnd["id"], players[13]["id"])
    assert not await frepo.on_roster(rnd["id"], late["id"])
    first = await frepo.write_context(rnd["id"], 11)
    second = await frepo.write_context(rnd["id"], 12)
    assert (first["topic"], second["topic"]) == (DECK[0], DECK[1])
    assert (first["option_a"], first["option_b"], first["answered_at"]) == ("", "", None)
    assert (await frepo.write_context(rnd["id"], 14))["question_id"] is None
    assert await frepo.used_topics(fgame["id"]) == {DECK[0], DECK[1]}
    assert await frepo.rounds_played(fgame["id"]) == 0
    await frepo.close_round(rnd["id"])
    assert await frepo.rounds_played(fgame["id"]) == 1
    again = await frepo.begin_round(fgame["id"], DECK[12:24], LATER)
    assert again["number"] == 2
    assert [p["user_id"] for p in await frepo.round_roster(again["id"])] == [11, 12, 14]
    assert await frepo.used_topics(fgame["id"]) == {DECK[0], DECK[1], *DECK[12:15]}
    assert (await frepo.get_current_round(fgame["id"]))["id"] == again["id"]


async def test_begin_round_refuses(frepo, fgame):
    for uid in USERS:
        await frepo.join(fgame["id"], uid)
    assert await frepo.begin_round(fgame["id"], DECK[:12], LATER) is None
    await frepo.start_game(fgame["id"], 3)
    rnd = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    assert await frepo.begin_round(fgame["id"], DECK[:12], LATER) is None
    await frepo.close_writing(rnd["id"], random.Random(1))
    assert await frepo.begin_round(fgame["id"], DECK[:12], LATER) is None
    await frepo.close_round(rnd["id"])
    assert await frepo.begin_round(fgame["id"], DECK[:12], LATER) is not None
    await frepo.finish_game(fgame["id"])
    assert await frepo.begin_round(fgame["id"], DECK[:12], LATER) is None
    assert await frepo.begin_round(9999, DECK[:12], LATER) is None


async def test_begin_round_number_after_abandon(frepo, fgame, frunning):
    first = await frepo.get_current_round(fgame["id"])
    assert await frepo.abandon_round(first["id"])
    assert not await frepo.abandon_round(first["id"])
    gone = await frepo.get_round(first["id"])
    assert gone["abandoned"] == 1 and gone["closed_at"] and gone["write_closed_at"]
    assert await frepo.rounds_played(fgame["id"]) == 0
    second = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    assert second["number"] == 1
    await frepo.close_round(second["id"])
    third = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    assert third["number"] == 2
    assert await frepo.rounds_played(fgame["id"]) == 1


async def test_write_context(frepo, fgame, frunning):
    rnd = await frepo.get_current_round(fgame["id"])
    ctx = await frepo.write_context(rnd["id"], 12)
    assert ctx["game_id"] == fgame["id"] and ctx["status"] == "active"
    assert ctx["guild_id"] == 1 and ctx["game_name"] == "Test"
    assert ctx["round_id"] == rnd["id"]
    assert ctx["write_closed_at"] is None and ctx["round_closed_at"] is None
    assert ctx["player_id"] == frunning[12]["id"] and ctx["left_at"] is None
    assert ctx["on_roster"] == 1
    assert ctx["question_id"] and ctx["answered_at"] is None and ctx["dm_prompted_at"] is None
    assert ctx["topic"] == DECK[1] and ctx["answer"] is None
    assert (ctx["option_a"], ctx["option_b"]) == ("", "")
    stranger = await frepo.write_context(rnd["id"], 99)
    assert stranger["game_id"] == fgame["id"] and stranger["player_id"] is None
    assert stranger["on_roster"] == 0 and stranger["question_id"] is None
    await frepo.join(fgame["id"], 14)
    late = await frepo.write_context(rnd["id"], 14)
    assert late["player_id"] and late["on_roster"] == 0 and late["question_id"] is None
    await frepo.leave(frunning[12]["id"])
    left = await frepo.write_context(rnd["id"], 12)
    assert left["left_at"] is not None and left["on_roster"] == 1
    assert await frepo.write_context(9999, 12) is None
    await frepo.close_writing(rnd["id"], random.Random(1))
    assert (await frepo.write_context(rnd["id"], 11))["write_closed_at"]
    await frepo.finish_game(fgame["id"])
    over = await frepo.write_context(rnd["id"], 11)
    assert over["status"] == "finished" and over["round_closed_at"]


async def test_write_options_and_edit(frepo, fgame, frunning):
    rnd = await frepo.get_current_round(fgame["id"])
    question_id = await qid(frepo, rnd, 11)
    assert await frepo.write_options(question_id, "cold soup", "or not")
    saved = await frepo.get_question(question_id)
    assert (saved["option_a"], saved["option_b"]) == ("cold soup", "or not")
    assert saved["answered_at"] and saved["answer"] is None and saved["topic"] == DECK[0]
    assert saved["round_number"] == 1 and saved["game_id"] == fgame["id"]
    assert saved["writer_id"] == frunning[11]["id"]
    assert await frepo.write_options(question_id, "warm soup", "or not")
    assert (await frepo.get_question(question_id))["option_a"] == "warm soup"
    mine = await frepo.write_context(rnd["id"], 11)
    assert mine["option_a"] == "warm soup" and mine["answered_at"]
    assert not await frepo.write_options(9999, "nothing", "or not")
    assert await frepo.get_question(9999) is None


async def test_popup_answer_clears_dm_flag(frepo, fgame, frunning):
    rnd = await frepo.get_current_round(fgame["id"])
    question_id = await qid(frepo, rnd, 11)
    stamp = "2026-01-01T00:00:00+00:00"
    await run_sql(
        frepo,
        "UPDATE fiftyfifty_questions SET dm_prompted_at = ? WHERE id = ?",
        (stamp, question_id),
    )
    assert await frepo.write_options(question_id, "by dm", "or not", via_dm=True)
    assert (await frepo.get_question(question_id))["dm_prompted_at"] == stamp
    assert await frepo.write_options(question_id, "by pop-up", "or not")
    after = await frepo.get_question(question_id)
    assert after["dm_prompted_at"] is None and after["option_a"] == "by pop-up"


async def test_write_after_close(frepo, fgame, frunning):
    rnd = await frepo.get_current_round(fgame["id"])
    question_id = await qid(frepo, rnd, 11)
    assert await frepo.write_options(question_id, "in time", "or not")
    assert await frepo.close_writing(rnd["id"], random.Random(1))
    assert not await frepo.write_options(question_id, "too late", "or not")
    assert not await frepo.write_options(await qid(frepo, rnd, 12), "too late", "or not")
    assert (await frepo.get_question(question_id))["option_a"] == "in time"
    assert (await frepo.get_question(await qid(frepo, rnd, 12)))["answered_at"] is None


async def test_write_after_game_end(frepo, fgame, frunning):
    rnd = await frepo.get_current_round(fgame["id"])
    await frepo.finish_game(fgame["id"])
    assert not await frepo.write_options(await qid(frepo, rnd, 11), "too late", "or not")


async def test_write_progress_ignores_leavers(frepo, fgame, frunning):
    rnd = await frepo.get_current_round(fgame["id"])
    assert await frepo.write_progress(rnd["id"]) == (0, 3)
    await frepo.write_options(await qid(frepo, rnd, 11), "one", "or not")
    await frepo.write_options(await qid(frepo, rnd, 13), "three", "or not")
    assert await frepo.write_progress(rnd["id"]) == (2, 3)
    await frepo.leave(frunning[13]["id"])
    assert await frepo.write_progress(rnd["id"]) == (1, 2)
    await frepo.join(fgame["id"], 14)
    assert await frepo.write_progress(rnd["id"]) == (1, 2)
    await frepo.leave(frunning[12]["id"])
    assert await frepo.write_progress(rnd["id"]) == (1, 1)
    await frepo.join(fgame["id"], 13)
    assert await frepo.write_progress(rnd["id"]) == (2, 2)


async def test_close_writing_orders_answered_only(frepo, fgame):
    users = tuple(range(11, 21))
    for uid in users:
        await frepo.join(fgame["id"], uid)
    await frepo.start_game(fgame["id"], 1)
    rnd = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    assert await frepo.next_question(rnd["id"]) is None
    writers = users[:8]
    for uid in writers:
        await frepo.write_options(await qid(frepo, rnd, uid), f"blank {uid}", "or not")
    assert await frepo.close_writing(rnd["id"], random.Random(3))
    assert not await frepo.close_writing(rnd["id"], random.Random(4))
    assert (await frepo.get_round(rnd["id"]))["write_closed_at"]
    positions = {
        uid: (await frepo.get_question(await qid(frepo, rnd, uid)))["position"] for uid in users
    }
    assert sorted(positions[uid] for uid in writers) == list(range(1, 9))
    assert [positions[uid] for uid in writers] != list(range(1, 9))
    assert positions[19] is None and positions[20] is None
    assert await frepo.question_count(rnd["id"]) == 8
    assert (await frepo.next_question(rnd["id"]))["position"] == 1
    assert sorted(await frepo.unwritten_user_ids(rnd["id"])) == [19, 20]


async def test_open_question_refuses(frepo, fgame, frunning):
    rnd = await frepo.get_current_round(fgame["id"])
    await frepo.write_options(await qid(frepo, rnd, 11), "one", "or not")
    await frepo.write_options(await qid(frepo, rnd, 12), "two", "or not")
    assert not await frepo.open_question(await qid(frepo, rnd, 11), LATER)
    await frepo.close_writing(rnd["id"], random.Random(1))
    assert not await frepo.open_question(await qid(frepo, rnd, 13), LATER)
    assert await frepo.current_question(rnd["id"]) is None
    first = await frepo.next_question(rnd["id"])
    assert await frepo.open_question(first["id"], LATER)
    opened = await frepo.current_question(rnd["id"])
    assert opened["id"] == first["id"] and opened["deadline_at"] == LATER and opened["started_at"]
    assert not await frepo.open_question(first["id"], LATER)
    second = await frepo.next_question(rnd["id"])
    assert second["id"] != first["id"] and second["position"] == 2
    assert not await frepo.open_question(second["id"], LATER)
    await frepo.set_vote_message(first["id"], 555, 1001)
    assert (await frepo.get_question(first["id"]))["vote_message_id"] == 1001
    await frepo.close_question(first["id"])
    assert await frepo.current_question(rnd["id"]) is None
    await frepo.finish_game(fgame["id"])
    assert not await frepo.open_question(second["id"], LATER)


async def test_cast_vote_and_change(frepo, fgame, frunning):
    rnd = await to_voting(frepo, fgame)
    waiting = await frepo.next_question(rnd["id"])
    voter = frunning[11]["id"]
    assert not await frepo.cast_vote(waiting["id"], voter, "a")
    q = await open_next(frepo, rnd)
    assert await frepo.vote_counts(q["id"]) == (0, 0)
    voters = [p["id"] for p in frunning.values() if p["id"] != q["writer_id"]]
    assert await frepo.cast_vote(q["id"], voters[0], "a")
    assert await frepo.cast_vote(q["id"], voters[1], "a")
    assert await frepo.vote_counts(q["id"]) == (2, 0)
    assert await frepo.cast_vote(q["id"], voters[0], "b")
    assert (await frepo.get_vote(q["id"], voters[0]))["choice"] == "b"
    assert await frepo.vote_counts(q["id"]) == (1, 1)
    assert await frepo.get_vote(q["id"], q["writer_id"]) is None


async def test_cast_vote_after_close(frepo, fgame, frunning):
    rnd = await to_voting(frepo, fgame)
    q = await open_next(frepo, rnd)
    voters = [p["id"] for p in frunning.values() if p["id"] != q["writer_id"]]
    assert await frepo.cast_vote(q["id"], voters[0], "a")
    await frepo.close_question(q["id"])
    assert not await frepo.cast_vote(q["id"], voters[0], "b")
    assert not await frepo.cast_vote(q["id"], voters[1], "b")
    assert await frepo.vote_counts(q["id"]) == (1, 0)


async def test_vote_progress_excludes_writer_and_leavers(frepo, fgame, frunning):
    rnd = await to_voting(frepo, fgame)
    q = await open_next(frepo, rnd)
    voters = [p["id"] for p in frunning.values() if p["id"] != q["writer_id"]]
    assert await frepo.vote_progress(q["id"]) == (0, 2)
    await frepo.cast_vote(q["id"], voters[0], "a")
    assert await frepo.vote_progress(q["id"]) == (1, 2)
    await frepo.leave(voters[0])
    assert await frepo.vote_progress(q["id"]) == (0, 1)
    await frepo.join(fgame["id"], 14)
    assert await frepo.vote_progress(q["id"]) == (0, 1)
    await frepo.leave(q["writer_id"])
    assert await frepo.vote_progress(q["id"]) == (0, 1)
    await frepo.leave(voters[1])
    assert await frepo.vote_progress(q["id"]) == (0, 0)
    assert await frepo.vote_counts(q["id"]) == (1, 0)


async def test_close_question_race(frepo, fgame, frunning):
    rnd = await to_voting(frepo, fgame)
    waiting = await frepo.next_question(rnd["id"])
    assert not await frepo.close_question(waiting["id"])
    q = await open_next(frepo, rnd)
    assert await frepo.close_question(q["id"])
    assert not await frepo.close_question(q["id"])
    assert not await frepo.abandon_question(q["id"])
    closed = await frepo.get_question(q["id"])
    assert closed["closed_at"] and closed["abandoned"] == 0
    second = await open_next(frepo, rnd)
    assert await frepo.abandon_question(second["id"])
    assert not await frepo.close_question(second["id"])
    assert (await frepo.get_question(second["id"]))["abandoned"] == 1
    assert await frepo.close_round(rnd["id"])
    assert not await frepo.close_round(rnd["id"])


async def test_finish_game_twice(frepo, fgame, frunning):
    rnd = await to_voting(frepo, fgame)
    first = await open_next(frepo, rnd)
    await frepo.close_question(first["id"])
    second = await open_next(frepo, rnd)
    assert await frepo.finish_game(fgame["id"])
    assert not await frepo.finish_game(fgame["id"])
    assert (await frepo.get_game(fgame["id"]))["finished_at"]
    kept = await frepo.get_question(first["id"])
    assert kept["closed_at"] and kept["abandoned"] == 0
    cut = await frepo.get_question(second["id"])
    assert cut["closed_at"] and cut["abandoned"] == 1
    closed = await frepo.get_round(rnd["id"])
    assert closed["closed_at"] and closed["abandoned"] == 0
    assert closed["write_closed_at"] == rnd["write_closed_at"]


async def test_finish_game_during_writing(frepo, fgame, frunning):
    rnd = await frepo.get_current_round(fgame["id"])
    assert await frepo.finish_game(fgame["id"])
    closed = await frepo.get_round(rnd["id"])
    assert closed["closed_at"] and closed["write_closed_at"] == closed["closed_at"]


async def test_list_overdue_writing(frepo, fgame, frunning):
    now = "2026-06-01T00:00:00+00:00"
    rnd = await frepo.get_current_round(fgame["id"])
    assert await frepo.list_overdue_writing(now) == []
    sql = "UPDATE fiftyfifty_rounds SET write_deadline_at = ? WHERE id = ?"
    await run_sql(frepo, sql, ("2026-05-31T23:59:59+00:00", rnd["id"]))
    assert [r["id"] for r in await frepo.list_overdue_writing(now)] == [rnd["id"]]
    await run_sql(frepo, sql, (now, rnd["id"]))
    assert [r["id"] for r in await frepo.list_overdue_writing(now)] == [rnd["id"]]
    await frepo.close_writing(rnd["id"], random.Random(1))
    assert await frepo.list_overdue_writing(now) == []
    await frepo.close_round(rnd["id"])
    second = await frepo.begin_round(fgame["id"], DECK[:12], EARLIER)
    assert [r["id"] for r in await frepo.list_overdue_writing(now)] == [second["id"]]
    await frepo.finish_game(fgame["id"])
    assert await frepo.list_overdue_writing(now) == []


async def test_list_overdue_questions(frepo, fgame, frunning):
    now = "2026-06-01T00:00:00+00:00"
    rnd = await to_voting(frepo, fgame)
    assert await frepo.list_overdue_questions(now) == []
    first = await open_next(frepo, rnd)
    assert await frepo.list_overdue_questions(now) == []
    await frepo.close_question(first["id"])
    second = await open_next(frepo, rnd, deadline=now)
    overdue = await frepo.list_overdue_questions(now)
    assert [q["id"] for q in overdue] == [second["id"]]
    assert overdue[0]["game_id"] == fgame["id"] and overdue[0]["round_number"] == 1
    await frepo.close_question(second["id"])
    assert await frepo.list_overdue_questions(now) == []
    third = await open_next(frepo, rnd, deadline=EARLIER)
    assert [q["id"] for q in await frepo.list_overdue_questions(now)] == [third["id"]]
    await frepo.finish_game(fgame["id"])
    assert await frepo.list_overdue_questions(now) == []


async def stalled(frepo, before=LATER):
    return [g["id"] for g in await frepo.list_stalled_games(before)]


async def test_list_stalled_games(frepo, fgame):
    for uid in USERS:
        await frepo.join(fgame["id"], uid)
    assert await stalled(frepo) == []
    await frepo.start_game(fgame["id"], 3)
    assert await stalled(frepo) == [fgame["id"]]
    assert await stalled(frepo, EARLIER) == []
    rnd = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    assert await stalled(frepo) == []
    await to_voting(frepo, fgame)
    assert await stalled(frepo) == [fgame["id"]]
    assert await stalled(frepo, EARLIER) == []
    first = await open_next(frepo, rnd)
    assert await stalled(frepo) == []
    await frepo.close_question(first["id"])
    assert await stalled(frepo) == [fgame["id"]]
    assert await stalled(frepo, EARLIER) == []
    await frepo.close_round(rnd["id"])
    assert await stalled(frepo) == [fgame["id"]]
    second = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    assert await stalled(frepo) == []
    await frepo.abandon_round(second["id"])
    assert await stalled(frepo) == [fgame["id"]]
    assert await stalled(frepo, EARLIER) == []
    await frepo.finish_game(fgame["id"])
    assert await stalled(frepo) == []


async def test_closed_questions(frepo, fgame, frunning):
    rnd = await to_voting(frepo, fgame, writers=(11, 12))
    assert await frepo.closed_questions(fgame["id"]) == []
    first = await open_next(frepo, rnd)
    writer = await frepo.get_player_by_id(first["writer_id"])
    other = frunning[12 if writer["user_id"] == 11 else 11]
    await frepo.cast_vote(first["id"], other["id"], "a")
    await frepo.cast_vote(first["id"], frunning[13]["id"], "b")
    assert await frepo.closed_questions(fgame["id"]) == []
    await frepo.close_question(first["id"])
    second = await open_next(frepo, rnd)
    await frepo.cast_vote(second["id"], frunning[13]["id"], "b")
    await frepo.abandon_question(second["id"])
    (scored,) = await frepo.closed_questions(fgame["id"])
    assert scored.writer_id == writer["user_id"]
    assert (scored.a, scored.b, scored.points) == (1, 1, 100)
    assert scored.option_a == f"blank {writer['user_id']}" and scored.option_b == "or not"
    assert scored.answer is None and scored.closed_at and scored.guesses == ()
    assert await frepo.unwritten_user_ids(rnd["id"]) == [13]
    await frepo.close_round(rnd["id"])
    await frepo.join(fgame["id"], 14)
    again = await frepo.begin_round(fgame["id"], DECK[12:24], LATER)
    await to_voting(frepo, fgame, writers=(14,))
    third = await open_next(frepo, again)
    await frepo.close_question(third["id"])
    both = await frepo.closed_questions(fgame["id"])
    assert [q.writer_id for q in both] == [writer["user_id"], 14]
    assert [q.writer_id for q in await frepo.closed_questions(fgame["id"], again["id"])] == [14]
    assert await frepo.round_user_ids(fgame["id"]) == [11, 12, 13, 14]
    await frepo.close_round(again["id"])
    await frepo.join(fgame["id"], 15)
    lost = await frepo.begin_round(fgame["id"], DECK[24:36], LATER)
    await frepo.abandon_round(lost["id"])
    assert await frepo.round_user_ids(fgame["id"]) == [11, 12, 13, 14]


async def test_asked_vote_totals_skips_voterless(frepo, fgame, frunning):
    rnd = await to_voting(frepo, fgame)
    first = await open_next(frepo, rnd)
    voters = [p["id"] for p in frunning.values() if p["id"] != first["writer_id"]]
    await frepo.cast_vote(first["id"], voters[0], "a")
    await frepo.cast_vote(first["id"], voters[1], "a")
    assert await frepo.asked_vote_totals(fgame["id"]) == []
    await frepo.close_question(first["id"])
    assert await frepo.asked_vote_totals(fgame["id"]) == [2]
    second = await open_next(frepo, rnd)
    await frepo.close_question(second["id"])
    assert await frepo.asked_vote_totals(fgame["id"]) == [2, 0]
    third = await open_next(frepo, rnd)
    await frepo.abandon_question(third["id"])
    assert await frepo.asked_vote_totals(fgame["id"]) == [2, 0]
    for uid in (14, 15, 16):
        await frepo.join(fgame["id"], uid)
    for player in frunning.values():
        if player["id"] != second["writer_id"]:
            await frepo.leave(player["id"])
    assert await frepo.asked_vote_totals(fgame["id"]) == [2]
    await frepo.leave(second["writer_id"])
    assert await frepo.asked_vote_totals(fgame["id"]) == [2]


async def test_abandoned_streak(frepo, fgame, frunning):
    assert await frepo.abandoned_streak(fgame["id"]) == 0
    first = await frepo.get_current_round(fgame["id"])
    await frepo.abandon_round(first["id"])
    assert await frepo.abandoned_streak(fgame["id"]) == 1
    second = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    await frepo.abandon_round(second["id"])
    assert await frepo.abandoned_streak(fgame["id"]) == 2
    third = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    assert await frepo.abandoned_streak(fgame["id"]) == 0
    await frepo.close_round(third["id"])
    fourth = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    await frepo.abandon_round(fourth["id"])
    assert await frepo.abandoned_streak(fgame["id"]) == 1
    assert await frepo.abandoned_streak(9999) == 0


async def test_last_round_answers(frepo, fgame, frunning):
    assert await frepo.last_round_answers(fgame["id"]) is None
    first = await to_voting(frepo, fgame, writers=(11, 12))
    assert await frepo.last_round_answers(fgame["id"]) is None
    await frepo.close_round(first["id"])
    assert await frepo.last_round_answers(fgame["id"]) == 2
    lost = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    await frepo.abandon_round(lost["id"])
    assert await frepo.last_round_answers(fgame["id"]) == 2
    empty = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    assert await frepo.last_round_answers(fgame["id"]) == 2
    await frepo.close_writing(empty["id"], random.Random(1))
    await frepo.close_round(empty["id"])
    assert await frepo.last_round_answers(fgame["id"]) == 0


async def test_dm_candidates(frepo, fgame, frunning):
    rnd = await frepo.get_current_round(fgame["id"])
    (row,) = await frepo.dm_candidates(11)
    assert row["question_id"] == await qid(frepo, rnd, 11) and row["round_id"] == rnd["id"]
    assert row["topic"] == DECK[0] and (row["option_a"], row["option_b"]) == ("", "")
    assert row["answered_at"] is None and row["dm_prompted_at"] is None
    assert row["write_closed_at"] is None
    assert (row["game_id"], row["game_name"], row["guild_id"]) == (fgame["id"], "Test", 1)
    assert await frepo.dm_candidates(99) == []
    await frepo.join(fgame["id"], 14)
    assert await frepo.dm_candidates(14) == []
    await frepo.leave(frunning[13]["id"])
    assert await frepo.dm_candidates(13) == []
    await frepo.write_options(row["question_id"], "soup", "or not", via_dm=True)
    await frepo.close_writing(rnd["id"], random.Random(1))
    (closed,) = await frepo.dm_candidates(11)
    assert closed["write_closed_at"] and closed["option_a"] == "soup" and closed["answered_at"]
    await frepo.close_round(rnd["id"])
    assert await frepo.dm_candidates(11) == []
    await frepo.begin_round(fgame["id"], DECK[12:24], LATER)
    assert len(await frepo.dm_candidates(11)) == 1
    assert len(await frepo.dm_candidates(14)) == 1
    await frepo.finish_game(fgame["id"])
    assert await frepo.dm_candidates(11) == []


async def test_dm_candidates_two_guilds_order(frepo, fgame, frunning):
    other = await frepo.create_game(guild_id=2, name="Other", created_by=11, channel_id=556)
    for uid in (11, 21, 22):
        await frepo.join(other["id"], uid)
    await frepo.start_game(other["id"], 1)
    second = await frepo.begin_round(other["id"], DECK[:12], LATER)
    first = await frepo.get_current_round(fgame["id"])

    async def order():
        return [row["game_id"] for row in await frepo.dm_candidates(11)]

    async def stamp(rnd, value):
        await run_sql(
            frepo,
            "UPDATE fiftyfifty_questions SET dm_prompted_at = ? WHERE id = ?",
            (value, await qid(frepo, rnd, 11)),
        )

    assert await order() == [other["id"], fgame["id"]]
    await stamp(first, "2026-01-01T00:00:00+00:00")
    assert await order() == [fgame["id"], other["id"]]
    await stamp(second, "2026-01-01T00:00:05+00:00")
    assert await order() == [other["id"], fgame["id"]]
    await frepo.close_writing(second["id"], random.Random(1))
    assert await order() == [fgame["id"], other["id"]]
    assert [row["guild_id"] for row in await frepo.dm_candidates(11)] == [1, 2]
    assert [row["game_id"] for row in await frepo.dm_candidates(21)] == [other["id"]]


async def test_mark_and_clear_dm_prompted(frepo, fgame, frunning):
    rnd = await frepo.get_current_round(fgame["id"])
    question_id = await qid(frepo, rnd, 11)
    assert await frepo.mark_dm_prompted(question_id)
    stamp = (await frepo.get_question(question_id))["dm_prompted_at"]
    assert stamp
    assert not await frepo.mark_dm_prompted(question_id)
    assert (await frepo.get_question(question_id))["dm_prompted_at"] == stamp
    await frepo.clear_dm_prompted(question_id)
    assert (await frepo.get_question(question_id))["dm_prompted_at"] is None
    assert await frepo.mark_dm_prompted(question_id)
    assert (await frepo.get_question(await qid(frepo, rnd, 12)))["dm_prompted_at"] is None
    await frepo.clear_dm_prompted(question_id)
    await frepo.close_writing(rnd["id"], random.Random(1))
    assert not await frepo.mark_dm_prompted(question_id)
    assert (await frepo.get_question(question_id))["dm_prompted_at"] is None
    assert not await frepo.mark_dm_prompted(9999)


async def finished_game(frepo, guild_id, users, name="Done"):
    """A finished one-round game where every question closed with a 1-1 vote. Returns the game."""
    game = await frepo.create_game(guild_id, name, users[0], 555)
    players = {uid: (await frepo.join(game["id"], uid))[0] for uid in users}
    await frepo.start_game(game["id"], 1)
    await frepo.begin_round(game["id"], DECK[:12], LATER)
    rnd = await to_voting(frepo, game, writers=users)
    for _ in users:
        q = await open_next(frepo, rnd)
        voters = [p["id"] for p in players.values() if p["id"] != q["writer_id"]]
        await frepo.cast_vote(q["id"], voters[0], "a")
        await frepo.cast_vote(q["id"], voters[1], "b")
        await frepo.close_question(q["id"])
    await frepo.close_round(rnd["id"])
    await frepo.finish_game(game["id"])
    return game


async def test_finished_questions_only_finished(frepo, fgame, frunning):
    rnd = await to_voting(frepo, fgame, writers=(11, 12))
    first = await open_next(frepo, rnd)
    writer = (await frepo.get_player_by_id(first["writer_id"]))["user_id"]
    other = frunning[12 if writer == 11 else 11]
    await frepo.cast_vote(first["id"], other["id"], "a")
    await frepo.cast_vote(first["id"], frunning[13]["id"], "a")
    await frepo.close_question(first["id"])
    second = await open_next(frepo, rnd)
    await frepo.cast_vote(second["id"], frunning[13]["id"], "b")
    assert await frepo.finished_questions(1) == {}
    await frepo.finish_game(fgame["id"])
    unwritten = await frepo.create_game(1, "Ended while writing", 30, 555)
    for uid in (30, 31, 32):
        await frepo.join(unwritten["id"], uid)
    await frepo.start_game(unwritten["id"], 1)
    await frepo.begin_round(unwritten["id"], DECK[:12], LATER)
    await frepo.finish_game(unwritten["id"])
    assert 30 not in await frepo.games_played(1)
    cancelled = await frepo.create_game(1, "Lobby only", 20, 555)
    await frepo.join(cancelled["id"], 20)
    await frepo.finish_game(cancelled["id"])
    done = await finished_game(frepo, 1, (31, 32, 33))
    live = await frepo.create_game(1, "Running", 30, 555)
    for uid in (30, 31, 32):
        await frepo.join(live["id"], uid)
    await frepo.start_game(live["id"], 1)
    await frepo.begin_round(live["id"], DECK[:12], LATER)
    live_round = await to_voting(frepo, live, writers=(30, 31, 32))
    await frepo.close_question((await open_next(frepo, live_round))["id"])

    games = await frepo.finished_questions(1)
    assert list(games) == [fgame["id"], done["id"]]
    (kept,) = games[fgame["id"]]
    assert kept.writer_id == writer and (kept.a, kept.b, kept.points) == (2, 0, 0)
    assert kept.option_a == f"blank {writer}" and kept.answer is None and kept.closed_at
    assert sorted(q.writer_id for q in games[done["id"]]) == [31, 32, 33]
    assert [(q.a, q.b, q.points) for q in games[done["id"]]] == [(1, 1, 100)] * 3
    assert await frepo.finished_questions(2) == {}
    elsewhere = await finished_game(frepo, 2, (41, 42, 43))
    assert list(await frepo.finished_questions(2)) == [elsewhere["id"]]


async def test_games_played(frepo, fgame, frunning):
    await frepo.join(fgame["id"], 14)
    await frepo.leave(frunning[13]["id"])
    first = await to_voting(frepo, fgame, writers=(11,))
    asked = await open_next(frepo, first)
    await frepo.close_question(asked["id"])
    await frepo.close_round(first["id"])
    lost = await frepo.begin_round(fgame["id"], DECK[:12], LATER)
    await frepo.abandon_round(lost["id"])
    assert await frepo.games_played(1) == {}
    await frepo.finish_game(fgame["id"])
    assert await frepo.games_played(1) == {11: 1, 12: 1, 13: 1}
    await finished_game(frepo, 1, (11, 14, 15))
    assert await frepo.games_played(1) == {11: 2, 12: 1, 13: 1, 14: 1, 15: 1}
    unwritten = await frepo.create_game(1, "Ended while writing", 30, 555)
    for uid in (30, 31, 32):
        await frepo.join(unwritten["id"], uid)
    await frepo.start_game(unwritten["id"], 1)
    await frepo.begin_round(unwritten["id"], DECK[:12], LATER)
    await frepo.finish_game(unwritten["id"])
    assert 30 not in await frepo.games_played(1)
    cancelled = await frepo.create_game(1, "Lobby only", 20, 555)
    await frepo.join(cancelled["id"], 20)
    await frepo.finish_game(cancelled["id"])
    assert 20 not in await frepo.games_played(1)
    assert await frepo.games_played(2) == {}


async def test_set_guess_and_change(frepo, fgame, frunning):
    rnd = await to_voting(frepo, fgame)
    waiting = await frepo.next_question(rnd["id"])
    assert not await frepo.set_guess(waiting["id"], frunning[11]["id"], 1)
    q = await open_next(frepo, rnd)
    first, second = [p["id"] for p in frunning.values() if p["id"] != q["writer_id"]]
    assert not await frepo.set_guess(q["id"], first, 1)
    assert await frepo.get_guess(q["id"], first) is None
    await frepo.cast_vote(q["id"], first, "a")
    assert await frepo.set_guess(q["id"], first, 1)
    assert (await frepo.get_guess(q["id"], first))["a_votes"] == 1
    assert await frepo.set_guess(q["id"], first, 2)
    guess = await frepo.get_guess(q["id"], first)
    assert guess["a_votes"] == 2 and guess["made_at"]
    assert not await frepo.set_guess(q["id"], second, 0)
    assert not await frepo.set_guess(q["id"], q["writer_id"], 0)
    assert not await frepo.set_guess(9999, first, 0)
    with pytest.raises(aiosqlite.IntegrityError):
        await frepo.set_guess(q["id"], first, -1)
    await frepo.cast_vote(q["id"], first, "b")
    assert (await frepo.get_guess(q["id"], first))["a_votes"] == 2
    user = next(uid for uid, p in frunning.items() if p["id"] == first)
    assert await frepo.guesses(q["id"]) == ((user, 2),)
    await frepo.close_question(q["id"])
    assert not await frepo.set_guess(q["id"], first, 0)
    assert (await frepo.get_guess(q["id"], first))["a_votes"] == 2


async def test_guess_progress_and_most_votes(frepo, fgame, frunning):
    rnd = await to_voting(frepo, fgame)
    q = await open_next(frepo, rnd)
    first, second = [p["id"] for p in frunning.values() if p["id"] != q["writer_id"]]
    assert await frepo.guess_progress(q["id"]) == (0, 2)
    assert await frepo.most_votes(q["id"]) == 2
    await frepo.cast_vote(q["id"], first, "a")
    assert await frepo.guess_progress(q["id"]) == (0, 2)
    await frepo.set_guess(q["id"], first, 1)
    assert await frepo.guess_progress(q["id"]) == (1, 2)
    assert await frepo.vote_progress(q["id"]) == (1, 2)
    await frepo.join(fgame["id"], 14)
    assert await frepo.guess_progress(q["id"]) == (1, 2)
    assert await frepo.most_votes(q["id"]) == 2
    await frepo.leave(first)
    assert await frepo.guess_progress(q["id"]) == (0, 1)
    assert await frepo.most_votes(q["id"]) == 2
    await frepo.leave(second)
    assert await frepo.guess_progress(q["id"]) == (0, 0)
    assert await frepo.most_votes(q["id"]) == 1
    assert await frepo.guess_progress(9999) == (0, 0)
    assert await frepo.most_votes(9999) == 0
    assert [tuple(v) for v in await frepo.voters(q["id"])] == [
        (next(uid for uid, p in frunning.items() if p["id"] == first), "a")
    ]


async def test_guesses_come_back_with_scored_questions(frepo, fgame, frunning):
    rnd = await to_voting(frepo, fgame, writers=(11, 12))
    first = await open_next(frepo, rnd)
    writer = (await frepo.get_player_by_id(first["writer_id"]))["user_id"]
    other = 12 if writer == 11 else 11
    await frepo.cast_vote(first["id"], frunning[other]["id"], "a")
    await frepo.cast_vote(first["id"], frunning[13]["id"], "b")
    await frepo.set_guess(first["id"], frunning[other]["id"], 1)
    await frepo.set_guess(first["id"], frunning[13]["id"], 2)
    await frepo.close_question(first["id"])
    second = await open_next(frepo, rnd)
    await frepo.cast_vote(second["id"], frunning[13]["id"], "a")
    await frepo.set_guess(second["id"], frunning[13]["id"], 1)
    await frepo.close_question(second["id"])
    one, two = await frepo.closed_questions(fgame["id"])
    assert one.guesses == tuple(sorted(((other, 1), (13, 2))))
    assert one.guess_points == [(other, 50)]
    assert two.guesses == ((13, 1),) and two.guess_points == []
    assert [q.guesses for q in await frepo.closed_questions(fgame["id"], rnd["id"])] == [
        one.guesses,
        two.guesses,
    ]
    assert await frepo.finished_questions(1) == {}
    await frepo.close_round(rnd["id"])
    await frepo.finish_game(fgame["id"])
    assert await frepo.finished_questions(1) == {fgame["id"]: [one, two]}
    assert await frepo.finished_questions(2) == {}
    elsewhere = await finished_game(frepo, 2, (41, 42, 43))
    assert all(q.guesses == () for q in (await frepo.finished_questions(2))[elsewhere["id"]])

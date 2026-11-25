"""Headlines database access."""

import random
from datetime import timedelta

import aiosqlite
import pytest

from gaminator.core.repo import AlreadyJoined
from gaminator.core.util import now
from gaminator.games.headlines.repo import GameFull, HeadlinesRepo
from gaminator.games.headlines.services.rules import ScoredPairing

LATER = "2999-01-01T00:00:00+00:00"
EARLIER = "2000-01-01T00:00:00+00:00"
USERS = (11, 12, 13)
DECK = [f"question {n}?" for n in range(100)]


@pytest.fixture
async def hrepo(db):
    await db.apply_schema("gaminator.games.headlines")
    return HeadlinesRepo(db)


@pytest.fixture
async def hgame(hrepo):
    return await hrepo.create_game(guild_id=1, name="Test", created_by=11, channel_id=555)


@pytest.fixture
async def hrunning(hrepo, hgame):
    """Three players (user ids 11, 12, 13), game started, round 1 dealt. Players by user id."""
    for uid in USERS:
        await hrepo.join(hgame["id"], uid)
    await hrepo.start_game(hgame["id"], 3)
    await hrepo.begin_round(hgame["id"], DECK[:12], LATER)
    return {uid: await hrepo.get_player(hgame["id"], uid) for uid in USERS}


async def run_sql(hrepo, sql, params=()):
    async with hrepo.db.transaction() as conn:
        await conn.execute(sql, params)


async def current(hrepo, game):
    return await hrepo.get_current_round(game["id"])


async def pairings_of(hrepo, rnd):
    """The round's pairings with the writer's and the quoted player's user ids."""
    return await hrepo._all(
        "SELECT w.*, wp.user_id AS writer_user_id, qp.user_id AS quoted_user_id, a.answer "
        "FROM headlines_pairings w "
        "JOIN headlines_answers a ON a.id = w.answer_id "
        "JOIN headlines_players wp ON wp.id = w.writer_id "
        "JOIN headlines_players qp ON qp.id = a.player_id "
        "WHERE w.round_id = ? ORDER BY w.id",
        (rnd["id"],),
    )


async def answer_all(hrepo, game, players, users=USERS):
    rnd = await current(hrepo, game)
    for uid in users:
        assert await hrepo.save_answer(rnd["id"], players[uid]["id"], f"answer {uid}")
    return rnd


async def to_writing(hrepo, game, players, users=USERS):
    """Every listed user answers and answering closes. Returns the round."""
    rnd = await answer_all(hrepo, game, players, users)
    assert await hrepo.close_answers(rnd["id"], random.Random(1), LATER) is not None
    return await hrepo.get_round(rnd["id"])


async def to_reveal(hrepo, game, players, users=USERS):
    """Every pairing gets a headline and writing closes. Returns the round."""
    rnd = await to_writing(hrepo, game, players, users)
    for row in await pairings_of(hrepo, rnd):
        assert await hrepo.save_headline(
            row["id"], row["writer_id"], f"headline by {row['writer_user_id']}"
        )
    assert await hrepo.close_writing(rnd["id"], random.Random(1)) == len(users)
    return await hrepo.get_round(rnd["id"])


async def to_voting(hrepo, game, players, users=USERS):
    rnd = await to_reveal(hrepo, game, players, users)
    assert await hrepo.open_vote(rnd["id"], LATER)
    return await hrepo.get_round(rnd["id"])


async def pairing_by_writer(hrepo, rnd, user_id):
    return next(p for p in await pairings_of(hrepo, rnd) if p["writer_user_id"] == user_id)


async def play_voted_round(hrepo, game, players, votes):
    """Play the open round to a closed vote. ``votes`` maps voter user id to writer user id."""
    rnd = await to_voting(hrepo, game, players)
    for voter, writer in votes.items():
        target = await pairing_by_writer(hrepo, rnd, writer)
        assert await hrepo.cast_vote(rnd["id"], players[voter]["id"], target["id"])
    assert await hrepo.close_vote(rnd["id"])
    return await hrepo.get_round(rnd["id"])


async def test_create_sets_host_and_channel(hrepo, hgame):
    assert hgame["status"] == "open" and hgame["host_id"] == 11 and hgame["channel_id"] == 555
    assert hgame["max_rounds"] == 5 and hgame["lobby_message_id"] is None
    await hrepo.set_lobby_message(hgame["id"], 777)
    await hrepo.set_host(hgame["id"], 12)
    game = await hrepo.get_game(hgame["id"])
    assert game["lobby_message_id"] == 777 and game["host_id"] == 12
    assert (await hrepo.get_current_game(1))["id"] == hgame["id"]
    assert await hrepo.get_current_game(2) is None


async def test_one_game_per_guild(hrepo, hgame):
    with pytest.raises(aiosqlite.IntegrityError):
        await hrepo.create_game(1, "Second", 12, 555)
    other = await hrepo.create_game(2, "Elsewhere", 12, 556)
    assert other["guild_id"] == 2
    assert await hrepo.finish_game(hgame["id"])
    assert await hrepo.get_current_game(1) is None
    await hrepo.create_game(1, "Second", 12, 555)


async def test_start_game_twice(hrepo, hgame):
    assert await hrepo.start_game(hgame["id"], 4)
    assert not await hrepo.start_game(hgame["id"], 5)
    game = await hrepo.get_game(hgame["id"])
    assert game["status"] == "active" and game["max_rounds"] == 4 and game["started_at"]


async def test_join_and_duplicate(hrepo, hgame):
    player, rejoined = await hrepo.join(hgame["id"], 11)
    assert player["user_id"] == 11 and not rejoined
    with pytest.raises(AlreadyJoined):
        await hrepo.join(hgame["id"], 11)
    assert await hrepo.get_player_by_id(player["id"]) == player
    assert await hrepo.get_player(hgame["id"], 99) is None


async def test_join_cap(hrepo, hgame):
    for uid in range(100, 112):
        await hrepo.join(hgame["id"], uid)
    with pytest.raises(GameFull) as full:
        await hrepo.join(hgame["id"], 200)
    assert "12 players" in str(full.value)
    first = await hrepo.get_player(hgame["id"], 100)
    assert await hrepo.leave(first["id"])
    await hrepo.join(hgame["id"], 200)
    with pytest.raises(GameFull):
        await hrepo.join(hgame["id"], 100)
    assert len(await hrepo.list_players(hgame["id"], active_only=True)) == 12
    assert len(await hrepo.list_players(hgame["id"])) == 13


async def test_rejoin_reuses_row(hrepo, hgame):
    player, _ = await hrepo.join(hgame["id"], 11)
    assert await hrepo.leave(player["id"])
    back, rejoined = await hrepo.join(hgame["id"], 11)
    assert rejoined and back["id"] == player["id"] and back["left_at"] is None


async def test_leave_twice(hrepo, hgame):
    player, _ = await hrepo.join(hgame["id"], 11)
    assert await hrepo.leave(player["id"])
    assert not await hrepo.leave(player["id"])


async def test_begin_round_distinct_questions(hrepo, hgame, hrunning):
    rnd = await current(hrepo, hgame)
    assert rnd["number"] == 1 and rnd["answer_deadline_at"] == LATER
    assert rnd["answer_closed_at"] is None and rnd["outcome"] is None
    rows = await hrepo._all(
        "SELECT * FROM headlines_answers WHERE round_id = ? ORDER BY id", (rnd["id"],)
    )
    assert [r["player_id"] for r in rows] == [hrunning[uid]["id"] for uid in USERS]
    assert [r["question"] for r in rows] == DECK[:3]
    assert all(r["answer"] is None for r in rows)
    assert await hrepo.used_questions(hgame["id"]) == set(DECK[:3])
    assert await hrepo.rounds_played(hgame["id"]) == 0


async def test_begin_round_refuses(hrepo, hgame, hrunning):
    assert await hrepo.begin_round(hgame["id"], DECK, LATER) is None
    rnd = await current(hrepo, hgame)
    assert await hrepo.abandon_round(rnd["id"])
    assert await hrepo.finish_game(hgame["id"])
    assert await hrepo.begin_round(hgame["id"], DECK, LATER) is None
    lobby = await hrepo.create_game(1, "Open", 11, 555)
    assert await hrepo.begin_round(lobby["id"], DECK, LATER) is None


async def test_round_number_skips_abandoned(hrepo, hgame, hrunning):
    first = await current(hrepo, hgame)
    assert await hrepo.abandon_round(first["id"])
    assert not await hrepo.abandon_round(first["id"])
    second = await hrepo.begin_round(hgame["id"], DECK, LATER)
    assert second["number"] == 1 and second["id"] != first["id"]
    assert await hrepo.rounds_played(hgame["id"]) == 0
    await play_voted_round(hrepo, hgame, hrunning, {})
    assert await hrepo.rounds_played(hgame["id"]) == 1
    third = await hrepo.begin_round(hgame["id"], DECK, LATER)
    assert third["number"] == 2


async def test_round_context(hrepo, hgame, hrunning):
    rnd = await current(hrepo, hgame)
    assert await hrepo.round_context(9999, 11) is None
    ctx = await hrepo.round_context(rnd["id"], 11)
    assert (ctx["game_id"], ctx["status"], ctx["guild_id"]) == (hgame["id"], "active", 1)
    assert (ctx["channel_id"], ctx["max_rounds"]) == (555, 3)
    assert (ctx["round_id"], ctx["round_number"]) == (rnd["id"], 1)
    assert ctx["answer_deadline_at"] == LATER and ctx["answer_closed_at"] is None
    assert ctx["write_deadline_at"] is None and ctx["write_closed_at"] is None
    assert ctx["vote_deadline_at"] is None and ctx["vote_closed_at"] is None
    assert ctx["closed_at"] is None
    assert ctx["player_id"] == hrunning[11]["id"] and ctx["left_at"] is None
    assert ctx["answer_id"] is not None and ctx["question"] == DECK[0] and ctx["answer"] is None
    assert ctx["pairing_id"] is None and ctx["quote"] is None and ctx["headline"] is None
    assert ctx["my_vote"] is None and not ctx["can_vote"]
    outsider = await hrepo.round_context(rnd["id"], 99)
    assert outsider["player_id"] is None and outsider["answer_id"] is None
    late, _ = await hrepo.join(hgame["id"], 14)
    joiner = await hrepo.round_context(rnd["id"], 14)
    assert joiner["player_id"] == late["id"] and joiner["answer_id"] is None

    rnd = await to_voting(hrepo, hgame, hrunning)
    mine = await pairing_by_writer(hrepo, rnd, 11)
    other = await pairing_by_writer(hrepo, rnd, 12)
    assert await hrepo.cast_vote(rnd["id"], hrunning[11]["id"], other["id"])
    ctx = await hrepo.round_context(rnd["id"], 11)
    assert ctx["answer"] == "answer 11"
    assert ctx["pairing_id"] == mine["id"] and ctx["quote"] == mine["answer"]
    assert ctx["quote"] != "answer 11" and ctx["headline"] == "headline by 11"
    assert ctx["my_vote"] == other["id"] and ctx["can_vote"]
    assert ctx["write_closed_at"] and ctx["vote_deadline_at"] == LATER
    joiner = await hrepo.round_context(rnd["id"], 14)
    assert joiner["pairing_id"] is None and joiner["my_vote"] is None


async def test_save_answer_guards(hrepo, hgame, hrunning):
    rnd = await current(hrepo, hgame)
    late, _ = await hrepo.join(hgame["id"], 14)
    assert not await hrepo.save_answer(rnd["id"], late["id"], "not on the roster")
    assert await hrepo.leave(hrunning[13]["id"])
    assert not await hrepo.save_answer(rnd["id"], hrunning[13]["id"], "left")
    assert await hrepo.save_answer(rnd["id"], hrunning[11]["id"], "in time")
    assert await hrepo.close_answers(rnd["id"], random.Random(1), LATER) is not None
    assert not await hrepo.save_answer(rnd["id"], hrunning[12]["id"], "too late")
    assert (await hrepo.round_context(rnd["id"], 12))["answer"] is None


async def test_save_answer_refused_after_game_ends(hrepo, hgame, hrunning):
    rnd = await current(hrepo, hgame)
    await run_sql(hrepo, "UPDATE headlines_games SET status = 'finished'")
    assert not await hrepo.save_answer(rnd["id"], hrunning[11]["id"], "over")


async def test_save_answer_changes(hrepo, hgame, hrunning):
    rnd = await current(hrepo, hgame)
    assert await hrepo.save_answer(rnd["id"], hrunning[11]["id"], "first")
    assert await hrepo.save_answer(rnd["id"], hrunning[11]["id"], "second")
    assert (await hrepo.round_context(rnd["id"], 11))["answer"] == "second"
    assert await hrepo.answer_progress(rnd["id"]) == (1, 3)


async def test_answer_progress_counts_roster_still_in(hrepo, hgame, hrunning):
    rnd = await current(hrepo, hgame)
    assert await hrepo.answer_progress(rnd["id"]) == (0, 3)
    await hrepo.join(hgame["id"], 14)
    assert await hrepo.answer_progress(rnd["id"]) == (0, 3)
    assert await hrepo.save_answer(rnd["id"], hrunning[11]["id"], "mine")
    assert await hrepo.save_answer(rnd["id"], hrunning[12]["id"], "mine too")
    assert await hrepo.answer_progress(rnd["id"]) == (2, 3)
    assert await hrepo.leave(hrunning[12]["id"])
    assert await hrepo.answer_progress(rnd["id"]) == (1, 2)
    await hrepo.join(hgame["id"], 12)
    assert await hrepo.answer_progress(rnd["id"]) == (2, 3)


async def test_close_answers_assigns(hrepo, hgame, hrunning):
    rnd = await answer_all(hrepo, hgame, hrunning)
    assert await hrepo.close_answers(rnd["id"], random.Random(1), LATER) == (3, 3)
    rnd = await hrepo.get_round(rnd["id"])
    assert rnd["answer_closed_at"] and rnd["write_deadline_at"] == LATER
    assert rnd["closed_at"] is None and rnd["outcome"] is None
    rows = await pairings_of(hrepo, rnd)
    assert sorted(r["writer_user_id"] for r in rows) == list(USERS)
    assert sorted(r["quoted_user_id"] for r in rows) == list(USERS)
    assert all(r["writer_user_id"] != r["quoted_user_id"] for r in rows)
    assert all(r["headline"] is None and r["position"] is None for r in rows)
    assert await hrepo.write_progress(rnd["id"]) == (0, 3)


async def test_close_answers_hands_out_a_leavers_answer(hrepo, hgame, hrunning):
    rnd = await answer_all(hrepo, hgame, hrunning, users=(11, 13))
    assert await hrepo.leave(hrunning[13]["id"])
    assert await hrepo.close_answers(rnd["id"], random.Random(1), LATER) == (2, 2)
    rows = {r["quoted_user_id"]: r["writer_user_id"] for r in await pairings_of(hrepo, rnd)}
    assert rows == {11: 12, 13: 11}


async def test_close_answers_no_answers_closes_round(hrepo, hgame, hrunning):
    rnd = await current(hrepo, hgame)
    assert await hrepo.close_answers(rnd["id"], random.Random(1), LATER) == (0, 0)
    rnd = await hrepo.get_round(rnd["id"])
    assert rnd["closed_at"] and rnd["outcome"] == "no_answers"
    assert rnd["write_deadline_at"] is None
    assert await hrepo.rounds_played(hgame["id"]) == 1

    second = await hrepo.begin_round(hgame["id"], DECK, LATER)
    assert await hrepo.save_answer(second["id"], hrunning[11]["id"], "alone")
    assert await hrepo.save_answer(second["id"], hrunning[12]["id"], "leaving")
    assert await hrepo.leave(hrunning[12]["id"])
    assert await hrepo.leave(hrunning[13]["id"])
    assert await hrepo.close_answers(second["id"], random.Random(1), LATER) == (2, 0)
    second = await hrepo.get_round(second["id"])
    assert second["outcome"] == "no_answers" and await pairings_of(hrepo, second) == []


async def test_close_answers_twice(hrepo, hgame, hrunning):
    rnd = await answer_all(hrepo, hgame, hrunning)
    assert await hrepo.close_answers(rnd["id"], random.Random(1), LATER) is not None
    assert await hrepo.close_answers(rnd["id"], random.Random(2), EARLIER) is None
    assert (await hrepo.get_round(rnd["id"]))["write_deadline_at"] == LATER
    assert len(await pairings_of(hrepo, rnd)) == 3


async def test_close_answers_refused_on_closed_round(hrepo, hgame, hrunning):
    rnd = await current(hrepo, hgame)
    assert await hrepo.abandon_round(rnd["id"])
    assert await hrepo.close_answers(rnd["id"], random.Random(1), LATER) is None


async def test_save_headline_guards(hrepo, hgame, hrunning):
    rnd = await to_writing(hrepo, hgame, hrunning)
    mine = await pairing_by_writer(hrepo, rnd, 11)
    other = await pairing_by_writer(hrepo, rnd, 12)
    assert not await hrepo.save_headline(other["id"], hrunning[11]["id"], "not my quote")
    assert await hrepo.leave(hrunning[12]["id"])
    assert not await hrepo.save_headline(other["id"], hrunning[12]["id"], "left")
    assert await hrepo.save_headline(mine["id"], hrunning[11]["id"], "first")
    assert await hrepo.save_headline(mine["id"], hrunning[11]["id"], "second")
    assert (await hrepo.round_context(rnd["id"], 11))["headline"] == "second"
    assert await hrepo.close_writing(rnd["id"], random.Random(1)) == 1
    assert not await hrepo.save_headline(mine["id"], hrunning[11]["id"], "too late")
    assert (await hrepo.round_context(rnd["id"], 11))["headline"] == "second"


async def test_save_headline_after_rejoin(hrepo, hgame, hrunning):
    rnd = await to_writing(hrepo, hgame, hrunning)
    mine = await pairing_by_writer(hrepo, rnd, 12)
    assert await hrepo.leave(hrunning[12]["id"])
    assert not await hrepo.save_headline(mine["id"], hrunning[12]["id"], "gone")
    await hrepo.join(hgame["id"], 12)
    assert await hrepo.save_headline(mine["id"], hrunning[12]["id"], "back")


async def test_write_progress_counts_writers_still_in(hrepo, hgame, hrunning):
    rnd = await to_writing(hrepo, hgame, hrunning)
    await hrepo.join(hgame["id"], 14)
    assert await hrepo.write_progress(rnd["id"]) == (0, 3)
    mine = await pairing_by_writer(hrepo, rnd, 11)
    assert await hrepo.save_headline(mine["id"], hrunning[11]["id"], "in")
    assert await hrepo.write_progress(rnd["id"]) == (1, 3)
    assert await hrepo.leave(hrunning[11]["id"])
    assert await hrepo.write_progress(rnd["id"]) == (0, 2)
    assert await hrepo.leave(hrunning[12]["id"])
    assert await hrepo.write_progress(rnd["id"]) == (0, 1)


async def test_close_writing_positions(hrepo, hgame, hrunning):
    rnd = await to_writing(hrepo, hgame, hrunning)
    for uid in (11, 13):
        row = await pairing_by_writer(hrepo, rnd, uid)
        assert await hrepo.save_headline(row["id"], row["writer_id"], f"headline {uid}")
    assert await hrepo.close_writing(rnd["id"], random.Random(1)) == 2
    assert await hrepo.close_writing(rnd["id"], random.Random(1)) is None
    rows = {r["writer_user_id"]: r for r in await pairings_of(hrepo, rnd)}
    assert sorted(rows[uid]["position"] for uid in (11, 13)) == [1, 2]
    assert rows[12]["position"] is None and rows[12]["headline"] is None
    rnd = await hrepo.get_round(rnd["id"])
    assert rnd["write_closed_at"] and rnd["closed_at"] is None
    options = await hrepo.vote_options(rnd["id"])
    assert [o["position"] for o in options] == [1, 2]
    assert {o["headline"] for o in options} == {"headline 11", "headline 13"}
    assert all(o["answer"].startswith("answer ") for o in options)
    assert {o["writer_id"] for o in options} == {rows[uid]["writer_id"] for uid in (11, 13)}
    assert all(
        o["headline"] == rows[uid]["headline"]
        for uid in (11, 13)
        for o in options
        if o["writer_id"] == rows[uid]["writer_id"]
    )


async def test_close_writing_needs_the_writing_step(hrepo, hgame, hrunning):
    rnd = await current(hrepo, hgame)
    assert await hrepo.close_writing(rnd["id"], random.Random(1)) is None


async def test_close_writing_no_headlines_closes_round(hrepo, hgame, hrunning):
    rnd = await to_writing(hrepo, hgame, hrunning)
    assert await hrepo.close_writing(rnd["id"], random.Random(1)) == 0
    rnd = await hrepo.get_round(rnd["id"])
    assert rnd["closed_at"] and rnd["outcome"] == "no_headlines"
    assert await hrepo.vote_options(rnd["id"]) == []


async def test_touch_reveal_guards(hrepo, hgame, hrunning):
    rnd = await current(hrepo, hgame)
    assert not await hrepo.touch_reveal(rnd["id"])
    rnd = await to_writing(hrepo, hgame, hrunning)
    assert not await hrepo.touch_reveal(rnd["id"])
    for row in await pairings_of(hrepo, rnd):
        await hrepo.save_headline(row["id"], row["writer_id"], "a headline")
    assert await hrepo.close_writing(rnd["id"], random.Random(1)) == 3
    assert (await hrepo.get_round(rnd["id"]))["reveal_active_at"] is None
    assert await hrepo.touch_reveal(rnd["id"])
    assert (await hrepo.get_round(rnd["id"]))["reveal_active_at"]
    assert await hrepo.open_vote(rnd["id"], LATER)
    assert not await hrepo.touch_reveal(rnd["id"])


async def test_touch_reveal_stops_when_game_or_round_ends(hrepo, hgame, hrunning):
    rnd = await to_reveal(hrepo, hgame, hrunning)
    assert await hrepo.touch_reveal(rnd["id"])
    assert await hrepo.finish_game(hgame["id"])
    assert not await hrepo.touch_reveal(rnd["id"])
    assert (await hrepo.get_round(rnd["id"]))["outcome"] == "abandoned"


async def test_reveal_queue_and_mark_shown(hrepo, hgame, hrunning):
    rnd = await to_reveal(hrepo, hgame, hrunning)
    queue = await hrepo.reveal_queue(rnd["id"])
    assert [q["position"] for q in queue] == [1, 2, 3]
    by_id = {p["id"]: p for p in await pairings_of(hrepo, rnd)}
    for row in queue:
        assert row["headline"] == by_id[row["id"]]["headline"]
        assert row["answer"] == by_id[row["id"]]["answer"]
        assert row["quoted_user_id"] == by_id[row["id"]]["quoted_user_id"]
    assert await hrepo.mark_shown(queue[0]["id"])
    assert not await hrepo.mark_shown(queue[0]["id"])
    assert [q["position"] for q in await hrepo.reveal_queue(rnd["id"])] == [2, 3]
    assert len(await hrepo.vote_options(rnd["id"])) == 3


async def test_close_unvoted(hrepo, hgame, hrunning):
    rnd = await current(hrepo, hgame)
    assert not await hrepo.close_unvoted(rnd["id"])
    rnd = await to_reveal(hrepo, hgame, hrunning)
    assert await hrepo.close_unvoted(rnd["id"])
    assert not await hrepo.close_unvoted(rnd["id"])
    rnd = await hrepo.get_round(rnd["id"])
    assert rnd["closed_at"] and rnd["outcome"] == "one_headline"
    assert await hrepo.rounds_played(hgame["id"]) == 1
    assert await hrepo.scored_pairings(hgame["id"]) == []


async def test_open_vote_stores_eligible(hrepo, hgame, hrunning):
    rnd = await to_writing(hrepo, hgame, hrunning)
    assert not await hrepo.open_vote(rnd["id"], LATER)
    for row in await pairings_of(hrepo, rnd):
        await hrepo.save_headline(row["id"], row["writer_id"], "a headline")
    await hrepo.close_writing(rnd["id"], random.Random(1))
    await hrepo.join(hgame["id"], 14)
    assert await hrepo.leave(hrunning[13]["id"])
    assert await hrepo.open_vote(rnd["id"], LATER)
    assert not await hrepo.open_vote(rnd["id"], EARLIER)
    rnd = await hrepo.get_round(rnd["id"])
    assert rnd["vote_deadline_at"] == LATER and rnd["eligible_voters"] == 2
    await hrepo.join(hgame["id"], 13)
    assert (await hrepo.get_round(rnd["id"]))["eligible_voters"] == 2
    assert await hrepo.vote_progress(rnd["id"]) == (0, 3)


async def test_get_pairing(hrepo, hgame, hrunning):
    rnd = await to_reveal(hrepo, hgame, hrunning)
    mine = await pairing_by_writer(hrepo, rnd, 11)
    row = await hrepo.get_pairing(mine["id"])
    assert row["round_id"] == rnd["id"] and row["writer_id"] == hrunning[11]["id"]
    assert row["position"] in (1, 2, 3)
    assert await hrepo.get_pairing(9999) is None


async def test_cast_vote_guards(hrepo, hgame, hrunning):
    rnd = await to_reveal(hrepo, hgame, hrunning)
    mine = await pairing_by_writer(hrepo, rnd, 11)
    other = await pairing_by_writer(hrepo, rnd, 12)
    voter = hrunning[11]["id"]
    assert not await hrepo.cast_vote(rnd["id"], voter, other["id"])
    late, _ = await hrepo.join(hgame["id"], 14)
    assert await hrepo.open_vote(rnd["id"], LATER)
    assert not await hrepo.cast_vote(rnd["id"], voter, mine["id"])
    assert not await hrepo.cast_vote(rnd["id"], voter, 9999)
    assert not await hrepo.cast_vote(rnd["id"], late["id"], other["id"])
    await run_sql(
        hrepo, "UPDATE headlines_pairings SET position = NULL WHERE id = ?", (other["id"],)
    )
    assert not await hrepo.cast_vote(rnd["id"], voter, other["id"])
    await run_sql(hrepo, "UPDATE headlines_pairings SET position = 2 WHERE id = ?", (other["id"],))
    assert await hrepo.leave(hrunning[13]["id"])
    assert not await hrepo.cast_vote(rnd["id"], hrunning[13]["id"], other["id"])
    assert await hrepo.cast_vote(rnd["id"], voter, other["id"])
    assert await hrepo.close_vote(rnd["id"])
    assert not await hrepo.cast_vote(rnd["id"], hrunning[12]["id"], mine["id"])
    assert await hrepo._all("SELECT * FROM headlines_votes WHERE voter_id != ?", (voter,)) == []


async def test_cast_vote_refuses_a_pairing_of_another_round(hrepo, hgame, hrunning):
    first = await play_voted_round(hrepo, hgame, hrunning, {11: 12})
    old = await pairing_by_writer(hrepo, first, 12)
    await hrepo.begin_round(hgame["id"], DECK[12:], LATER)
    second = await to_voting(hrepo, hgame, hrunning)
    assert not await hrepo.cast_vote(second["id"], hrunning[11]["id"], old["id"])


async def test_cast_vote_changes(hrepo, hgame, hrunning):
    rnd = await to_voting(hrepo, hgame, hrunning)
    second = await pairing_by_writer(hrepo, rnd, 12)
    third = await pairing_by_writer(hrepo, rnd, 13)
    voter = hrunning[11]["id"]
    assert await hrepo.cast_vote(rnd["id"], voter, second["id"])
    assert await hrepo.cast_vote(rnd["id"], voter, third["id"])
    votes = await hrepo._all("SELECT * FROM headlines_votes")
    assert [(v["voter_id"], v["pairing_id"]) for v in votes] == [(voter, third["id"])]
    assert await hrepo.vote_progress(rnd["id"]) == (1, 3)


async def test_vote_progress(hrepo, hgame, hrunning):
    rnd = await to_voting(hrepo, hgame, hrunning)
    await hrepo.join(hgame["id"], 14)
    assert await hrepo.vote_progress(rnd["id"]) == (0, 3)
    target = await pairing_by_writer(hrepo, rnd, 11)
    for uid in (12, 13):
        assert await hrepo.cast_vote(rnd["id"], hrunning[uid]["id"], target["id"])
    assert await hrepo.vote_progress(rnd["id"]) == (2, 3)
    assert await hrepo.leave(hrunning[13]["id"])
    assert await hrepo.vote_progress(rnd["id"]) == (1, 2)
    assert await hrepo.leave(hrunning[11]["id"])
    assert await hrepo.vote_progress(rnd["id"]) == (1, 1)


async def test_vote_progress_leaves_out_a_lone_writer(hrepo, hgame, hrunning):
    """With one headline, its writer has nothing to vote for."""
    rnd = await to_writing(hrepo, hgame, hrunning)
    row = await pairing_by_writer(hrepo, rnd, 11)
    assert await hrepo.save_headline(row["id"], row["writer_id"], "the only one")
    assert await hrepo.close_writing(rnd["id"], random.Random(1)) == 1
    assert await hrepo.open_vote(rnd["id"], LATER)
    assert await hrepo.vote_progress(rnd["id"]) == (0, 2)
    assert not (await hrepo.round_context(rnd["id"], 11))["can_vote"]
    assert (await hrepo.round_context(rnd["id"], 12))["can_vote"]


async def test_close_vote_twice(hrepo, hgame, hrunning):
    rnd = await to_reveal(hrepo, hgame, hrunning)
    assert not await hrepo.close_vote(rnd["id"])
    assert await hrepo.open_vote(rnd["id"], LATER)
    assert await hrepo.close_vote(rnd["id"])
    assert not await hrepo.close_vote(rnd["id"])
    rnd = await hrepo.get_round(rnd["id"])
    assert rnd["vote_closed_at"] and rnd["closed_at"] and rnd["outcome"] == "voted"
    assert not await hrepo.abandon_round(rnd["id"])


async def test_message_ids(hrepo, hgame, hrunning):
    rnd = await current(hrepo, hgame)
    await hrepo.set_answer_message(rnd["id"], 1)
    await hrepo.set_write_message(rnd["id"], 2)
    await hrepo.set_vote_message(rnd["id"], 3)
    rnd = await hrepo.get_round(rnd["id"])
    assert (rnd["answer_message_id"], rnd["write_message_id"], rnd["vote_message_id"]) == (1, 2, 3)


async def test_finish_game_abandons_open_round(hrepo, hgame, hrunning):
    first = await play_voted_round(hrepo, hgame, hrunning, {11: 12})
    second = await hrepo.begin_round(hgame["id"], DECK[12:], LATER)
    assert await hrepo.finish_game(hgame["id"])
    assert not await hrepo.finish_game(hgame["id"])
    game = await hrepo.get_game(hgame["id"])
    assert game["status"] == "finished" and game["finished_at"]
    second = await hrepo.get_round(second["id"])
    assert second["closed_at"] and second["outcome"] == "abandoned"
    assert (await hrepo.get_round(first["id"]))["outcome"] == "voted"


async def test_list_overdue_each_step(hrepo, hgame, hrunning):
    stamp = "2500-01-01T00:00:00+00:00"
    rnd = await current(hrepo, hgame)
    assert await hrepo.list_overdue_answering(stamp) == []
    assert [r["id"] for r in await hrepo.list_overdue_answering(LATER)] == [rnd["id"]]
    assert await hrepo.list_overdue_writing(LATER) == []
    assert await hrepo.list_overdue_voting(LATER) == []
    await hrepo.set_answer_message(rnd["id"], 71)
    assert (await hrepo.list_overdue_answering(LATER))[0]["answer_message_id"] == 71
    assert (await hrepo.list_overdue_answering(LATER))[0]["game_id"] == hgame["id"]

    rnd = await to_writing(hrepo, hgame, hrunning)
    assert await hrepo.list_overdue_answering(LATER) == []
    assert await hrepo.list_overdue_writing(stamp) == []
    overdue = await hrepo.list_overdue_writing(LATER)
    assert [r["id"] for r in overdue] == [rnd["id"]] and overdue[0]["write_message_id"] is None

    for row in await pairings_of(hrepo, rnd):
        await hrepo.save_headline(row["id"], row["writer_id"], "a headline")
    await hrepo.close_writing(rnd["id"], random.Random(1))
    assert await hrepo.list_overdue_writing(LATER) == []
    assert await hrepo.list_overdue_voting(LATER) == []
    await hrepo.open_vote(rnd["id"], LATER)
    assert await hrepo.list_overdue_voting(stamp) == []
    overdue = await hrepo.list_overdue_voting(LATER)
    assert [r["id"] for r in overdue] == [rnd["id"]] and overdue[0]["vote_message_id"] is None
    await run_sql(hrepo, "UPDATE headlines_games SET status = 'finished'")
    assert await hrepo.list_overdue_voting(LATER) == []


def ago(seconds):
    return (now() - timedelta(seconds=seconds)).isoformat(timespec="seconds")


async def stalled(hrepo, grace=30):
    return [g["id"] for g in await hrepo.list_stalled_games(ago(grace))]


async def test_list_stalled_games(hrepo, hgame):
    for uid in USERS:
        await hrepo.join(hgame["id"], uid)
    players = {uid: await hrepo.get_player(hgame["id"], uid) for uid in USERS}
    assert await stalled(hrepo, 0) == []
    await hrepo.start_game(hgame["id"], 3)
    assert await stalled(hrepo) == []
    assert await stalled(hrepo, 0) == [hgame["id"]]
    await run_sql(hrepo, "UPDATE headlines_games SET started_at = ?", (ago(60),))
    assert await stalled(hrepo) == [hgame["id"]]

    rnd = await hrepo.begin_round(hgame["id"], DECK, LATER)
    await run_sql(hrepo, "UPDATE headlines_rounds SET started_at = ?", (ago(60),))
    assert await stalled(hrepo) == []
    rnd = await to_writing(hrepo, hgame, players)
    await run_sql(hrepo, "UPDATE headlines_rounds SET answer_closed_at = ?", (ago(60),))
    assert await stalled(hrepo) == []

    for row in await pairings_of(hrepo, rnd):
        await hrepo.save_headline(row["id"], row["writer_id"], "a headline")
    await hrepo.close_writing(rnd["id"], random.Random(1))
    assert await stalled(hrepo) == []
    assert await stalled(hrepo, 0) == [hgame["id"]]
    await run_sql(hrepo, "UPDATE headlines_rounds SET write_closed_at = ?", (ago(60),))
    found = await hrepo.list_stalled_games(ago(30))
    assert [g["id"] for g in found] == [hgame["id"]] and found[0]["round_id"] == rnd["id"]

    await hrepo.open_vote(rnd["id"], LATER)
    assert await stalled(hrepo) == []
    await hrepo.close_vote(rnd["id"])
    assert await stalled(hrepo) == []
    await run_sql(hrepo, "UPDATE headlines_rounds SET closed_at = ?", (ago(60),))
    assert await stalled(hrepo) == [hgame["id"]]
    await hrepo.finish_game(hgame["id"])
    assert await stalled(hrepo, 0) == []


async def test_stalled_skips_live_reveal(hrepo, hgame, hrunning):
    rnd = await to_reveal(hrepo, hgame, hrunning)
    await run_sql(
        hrepo,
        "UPDATE headlines_rounds SET write_closed_at = ?, reveal_active_at = ?",
        (ago(120), ago(15)),
    )
    assert await stalled(hrepo) == []
    await run_sql(hrepo, "UPDATE headlines_rounds SET reveal_active_at = ?", (ago(31),))
    assert await stalled(hrepo) == [hgame["id"]]
    assert await hrepo.touch_reveal(rnd["id"])
    assert await stalled(hrepo) == []


async def test_abandoned_streak(hrepo, hgame, hrunning):
    assert await hrepo.abandoned_streak(hgame["id"]) == 0
    for expected in (1, 2):
        rnd = await current(hrepo, hgame)
        assert await hrepo.abandon_round(rnd["id"])
        assert await hrepo.abandoned_streak(hgame["id"]) == expected
        await hrepo.begin_round(hgame["id"], DECK, LATER)
    await play_voted_round(hrepo, hgame, hrunning, {})
    assert await hrepo.abandoned_streak(hgame["id"]) == 0
    rnd = await hrepo.begin_round(hgame["id"], DECK, LATER)
    assert await hrepo.abandon_round(rnd["id"])
    assert await hrepo.abandoned_streak(hgame["id"]) == 1


async def test_last_round_activity_from_stored_columns(hrepo, hgame, hrunning):
    assert await hrepo.last_round_activity(hgame["id"]) is None
    await play_voted_round(hrepo, hgame, hrunning, {11: 12})
    row = await hrepo.last_round_activity(hgame["id"])
    assert (row["outcome"], row["eligible_voters"], row["votes"]) == ("voted", 3, 1)
    await hrepo.join(hgame["id"], 14)
    assert await hrepo.leave(hrunning[11]["id"])
    assert await hrepo.leave(hrunning[12]["id"])
    row = await hrepo.last_round_activity(hgame["id"])
    assert (row["outcome"], row["eligible_voters"], row["votes"]) == ("voted", 3, 1)

    rnd = await hrepo.begin_round(hgame["id"], DECK, LATER)
    assert (await hrepo.last_round_activity(hgame["id"]))["outcome"] == "voted"
    assert await hrepo.abandon_round(rnd["id"])
    assert (await hrepo.last_round_activity(hgame["id"]))["outcome"] == "voted"
    rnd = await hrepo.begin_round(hgame["id"], DECK, LATER)
    assert await hrepo.close_answers(rnd["id"], random.Random(1), LATER) == (0, 0)
    row = await hrepo.last_round_activity(hgame["id"])
    assert (row["outcome"], row["eligible_voters"], row["votes"]) == ("no_answers", None, 0)


async def test_scored_pairings(hrepo, hgame, hrunning):
    assert await hrepo.scored_pairings(hgame["id"]) == []
    rnd = await to_voting(hrepo, hgame, hrunning)
    target = await pairing_by_writer(hrepo, rnd, 12)
    for uid in (11, 13):
        assert await hrepo.cast_vote(rnd["id"], hrunning[uid]["id"], target["id"])
    assert await hrepo.scored_pairings(hgame["id"]) == []
    assert await hrepo.leave(hrunning[13]["id"])
    assert await hrepo.close_vote(rnd["id"])
    scored = await hrepo.scored_pairings(hgame["id"])
    assert [s.position for s in scored] == [1, 2, 3]
    assert all(isinstance(s, ScoredPairing) and s.round_number == 1 for s in scored)
    rows = {r["position"]: r for r in await pairings_of(hrepo, rnd)}
    for s in scored:
        row = rows[s.position]
        assert (s.writer_id, s.quoted_id) == (row["writer_user_id"], row["quoted_user_id"])
        assert (s.headline, s.answer) == (row["headline"], row["answer"])
        assert s.votes == (2 if s.writer_id == 12 else 0)
    assert await hrepo.scored_pairings(hgame["id"], rnd["id"]) == scored
    assert await hrepo.scored_pairings(hgame["id"], 9999) == []


async def test_game_user_ids_voted_rounds_only(hrepo, hgame, hrunning):
    assert await hrepo.game_user_ids(hgame["id"]) == []
    first = await play_voted_round(hrepo, hgame, hrunning, {})
    assert await hrepo.game_user_ids(hgame["id"]) == [11, 12, 13]
    await hrepo.join(hgame["id"], 14)
    assert await hrepo.leave(hrunning[11]["id"])
    second = await hrepo.begin_round(hgame["id"], DECK[12:], LATER)
    assert await hrepo.game_user_ids(hgame["id"]) == [11, 12, 13]
    assert await hrepo.game_user_ids(hgame["id"], second["id"]) == []
    assert await hrepo.close_answers(second["id"], random.Random(1), LATER) == (0, 0)
    assert await hrepo.game_user_ids(hgame["id"]) == [11, 12, 13]
    assert await hrepo.game_user_ids(hgame["id"], first["id"]) == [11, 12, 13]


async def test_finished_pairings_only_finished(hrepo, hgame, hrunning):
    await play_voted_round(hrepo, hgame, hrunning, {11: 12, 13: 12})
    assert await hrepo.finished_pairings(1) == {}
    assert await hrepo.finish_game(hgame["id"])
    found = await hrepo.finished_pairings(1)
    assert list(found) == [hgame["id"]]
    assert found[hgame["id"]] == await hrepo.scored_pairings(hgame["id"])
    assert sum(p.votes for p in found[hgame["id"]]) == 2
    assert await hrepo.finished_pairings(2) == {}


async def test_games_played(hrepo, hgame, hrunning):
    await play_voted_round(hrepo, hgame, hrunning, {11: 12})
    await hrepo.join(hgame["id"], 14)
    second = await hrepo.begin_round(hgame["id"], DECK[12:], LATER)
    assert await hrepo.games_played(1) == {}
    assert await hrepo.finish_game(hgame["id"])
    assert await hrepo.games_played(1) == {11: 1, 12: 1, 13: 1}
    assert (await hrepo.get_round(second["id"]))["outcome"] == "abandoned"

    early = await hrepo.create_game(1, "Early", 21, 555)
    for uid in (21, 22, 23):
        await hrepo.join(early["id"], uid)
    await hrepo.start_game(early["id"], 2)
    await hrepo.begin_round(early["id"], DECK, LATER)
    assert await hrepo.finish_game(early["id"])
    assert await hrepo.games_played(1) == {11: 1, 12: 1, 13: 1}

    single = await hrepo.create_game(1, "Single", 11, 555)
    for uid in USERS:
        await hrepo.join(single["id"], uid)
    players = {uid: await hrepo.get_player(single["id"], uid) for uid in USERS}
    await hrepo.start_game(single["id"], 2)
    await hrepo.begin_round(single["id"], DECK, LATER)
    rnd = await to_reveal(hrepo, single, players)
    assert await hrepo.close_unvoted(rnd["id"])
    await hrepo.begin_round(single["id"], DECK, LATER)
    await play_voted_round(hrepo, single, players, {})
    assert await hrepo.finish_game(single["id"])
    assert await hrepo.games_played(1) == {11: 2, 12: 2, 13: 2}
    assert await hrepo.games_played(2) == {}

"""Most Likely To database access."""

import aiosqlite
import pytest

from gaminator.core.repo import AlreadyJoined
from gaminator.games.mostlikely.repo import GameFull, MostLikelyRepo

LATER = "2999-01-01T00:00:00+00:00"


@pytest.fixture
async def mrepo(db):
    return MostLikelyRepo(db)


@pytest.fixture
async def mgame(mrepo):
    return await mrepo.create_game(guild_id=1, name="Test", created_by=11, channel_id=555)


@pytest.fixture
async def mrunning(mrepo, mgame):
    """Three players (user ids 11, 12, 13), game started, prompt 1 open. Players by user id."""
    for uid in (11, 12, 13):
        await mrepo.join(mgame["id"], uid)
    await mrepo.start_game(mgame["id"], 4)
    await mrepo.begin_round(mgame["id"], "name their car", LATER)
    return {uid: await mrepo.get_player(mgame["id"], uid) for uid in (11, 12, 13)}


async def test_create_sets_host_and_channel(mrepo, mgame):
    assert mgame["status"] == "open"
    assert mgame["host_id"] == 11 and mgame["created_by"] == 11
    assert mgame["channel_id"] == 555
    assert mgame["max_rounds"] == 10
    assert mgame["lobby_message_id"] is None
    await mrepo.set_host(mgame["id"], 12)
    assert (await mrepo.get_game(mgame["id"]))["host_id"] == 12


async def test_one_game_per_guild(mrepo, mgame):
    with pytest.raises(aiosqlite.IntegrityError):
        await mrepo.create_game(1, "Second", 12, 555)
    assert await mrepo.finish_game(mgame["id"])
    assert await mrepo.get_current_game(1) is None
    await mrepo.create_game(1, "Second", 12, 555)
    await mrepo.create_game(2, "Other server", 12, 556)


async def test_start_game_twice(mrepo, mgame):
    assert await mrepo.start_game(mgame["id"], 4)
    game = await mrepo.get_game(mgame["id"])
    assert game["status"] == "active" and game["max_rounds"] == 4 and game["started_at"]
    assert not await mrepo.start_game(mgame["id"], 9)
    assert (await mrepo.get_game(mgame["id"]))["max_rounds"] == 4


async def test_finish_game_twice(mrepo, mgame, mrunning):
    rnd = await mrepo.get_current_round(mgame["id"])
    assert await mrepo.finish_game(mgame["id"])
    closed = await mrepo.get_round(rnd["id"])
    assert closed["closed_at"] and closed["abandoned"] == 1
    assert not await mrepo.finish_game(mgame["id"])


async def test_set_lobby_message(mrepo, mgame):
    await mrepo.set_lobby_message(mgame["id"], 777)
    assert (await mrepo.get_game(mgame["id"]))["lobby_message_id"] == 777


async def test_join_and_duplicate(mrepo, mgame):
    player, rejoined = await mrepo.join(mgame["id"], 11)
    assert player["user_id"] == 11 and player["left_at"] is None and not rejoined
    with pytest.raises(AlreadyJoined):
        await mrepo.join(mgame["id"], 11)


async def test_join_mid_game(mrepo, mgame, mrunning):
    player, rejoined = await mrepo.join(mgame["id"], 14)
    assert not rejoined
    rnd = await mrepo.get_current_round(mgame["id"])
    assert not await mrepo.on_roster(rnd["id"], player["id"])
    assert len(await mrepo.list_players(mgame["id"], active_only=True)) == 4


async def test_rejoin_reuses_row(mrepo, mgame):
    first, _ = await mrepo.join(mgame["id"], 11)
    assert await mrepo.leave(first["id"])
    assert not await mrepo.leave(first["id"])
    assert (await mrepo.get_player(mgame["id"], 11))["left_at"] is not None
    assert await mrepo.list_players(mgame["id"], active_only=True) == []
    again, rejoined = await mrepo.join(mgame["id"], 11)
    assert rejoined and again["id"] == first["id"] and again["left_at"] is None
    assert len(await mrepo.list_players(mgame["id"])) == 1


async def test_join_cap(mrepo, mgame):
    for uid in range(100, 125):
        await mrepo.join(mgame["id"], uid)
    with pytest.raises(GameFull):
        await mrepo.join(mgame["id"], 999)
    assert await mrepo.get_player(mgame["id"], 999) is None
    first = await mrepo.get_player(mgame["id"], 100)
    await mrepo.leave(first["id"])
    await mrepo.join(mgame["id"], 999)
    with pytest.raises(GameFull):
        await mrepo.join(mgame["id"], 100)


async def test_begin_round_snapshots_roster(mrepo, mgame):
    players = {uid: (await mrepo.join(mgame["id"], uid))[0] for uid in (11, 12, 13)}
    assert await mrepo.begin_round(mgame["id"], "p0", LATER) is None
    await mrepo.start_game(mgame["id"], 4)
    await mrepo.leave(players[13]["id"])
    rnd = await mrepo.begin_round(mgame["id"], "p1", LATER)
    assert rnd["number"] == 1 and rnd["deadline_at"] == LATER and rnd["abandoned"] == 0
    assert (await mrepo.get_game(mgame["id"]))["round"] == 1
    late, _ = await mrepo.join(mgame["id"], 14)
    roster = await mrepo.round_roster(rnd["id"])
    assert [p["user_id"] for p in roster] == [11, 12]
    assert await mrepo.on_roster(rnd["id"], players[11]["id"])
    assert not await mrepo.on_roster(rnd["id"], players[13]["id"])
    assert not await mrepo.on_roster(rnd["id"], late["id"])
    assert await mrepo.begin_round(mgame["id"], "p2", LATER) is None
    await mrepo.leave(players[12]["id"])
    assert [p["user_id"] for p in await mrepo.round_roster(rnd["id"])] == [11, 12]
    assert await mrepo.close_round(rnd["id"])
    second = await mrepo.begin_round(mgame["id"], "p2", LATER)
    assert second["number"] == 2
    assert [p["user_id"] for p in await mrepo.round_roster(second["id"])] == [11, 14]
    assert await mrepo.used_prompts(mgame["id"]) == {"p1", "p2"}
    await mrepo.finish_game(mgame["id"])
    assert await mrepo.begin_round(mgame["id"], "p3", LATER) is None


async def test_abandon_round(mrepo, mgame, mrunning):
    rnd = await mrepo.get_current_round(mgame["id"])
    await mrepo.cast_vote(rnd["id"], mrunning[11]["id"], mrunning[12]["id"])
    assert await mrepo.abandon_round(rnd["id"])
    assert not await mrepo.abandon_round(rnd["id"])
    assert (await mrepo.get_round(rnd["id"]))["abandoned"] == 1
    assert await mrepo.closed_round_votes(mgame["id"]) == []
    assert len(await mrepo.list_votes(rnd["id"])) == 1


async def test_votes_and_progress(mrepo, mgame, mrunning):
    rnd = await mrepo.get_current_round(mgame["id"])
    a, b = mrunning[11]["id"], mrunning[12]["id"]
    assert await mrepo.vote_progress(rnd["id"]) == (0, 3)
    assert await mrepo.cast_vote(rnd["id"], a, b)
    assert await mrepo.cast_vote(rnd["id"], a, a)
    assert (await mrepo.get_vote(rnd["id"], a))["target_id"] == a
    assert len(await mrepo.list_votes(rnd["id"])) == 1
    assert await mrepo.vote_progress(rnd["id"]) == (1, 3)
    assert await mrepo.cast_vote(rnd["id"], b, a)
    assert await mrepo.vote_progress(rnd["id"]) == (2, 3)


async def test_vote_progress_ignores_leavers(mrepo, mgame, mrunning):
    rnd = await mrepo.get_current_round(mgame["id"])
    a, b, c = (mrunning[u]["id"] for u in (11, 12, 13))
    await mrepo.cast_vote(rnd["id"], a, b)
    await mrepo.cast_vote(rnd["id"], c, b)
    await mrepo.leave(c)
    assert await mrepo.vote_progress(rnd["id"]) == (1, 2)
    await mrepo.leave(b)
    assert await mrepo.vote_progress(rnd["id"]) == (1, 1)
    await mrepo.join(mgame["id"], 14)
    assert await mrepo.vote_progress(rnd["id"]) == (1, 1)
    await mrepo.join(mgame["id"], 13)
    assert await mrepo.vote_progress(rnd["id"]) == (2, 2)


async def test_close_round_race(mrepo, mgame, mrunning):
    rnd = await mrepo.get_current_round(mgame["id"])
    assert await mrepo.close_round(rnd["id"])
    assert not await mrepo.close_round(rnd["id"])
    closed = await mrepo.get_round(rnd["id"])
    assert closed["closed_at"] and closed["abandoned"] == 0


async def test_cast_vote_after_close(mrepo, mgame, mrunning):
    rnd = await mrepo.get_current_round(mgame["id"])
    a, b = mrunning[11]["id"], mrunning[12]["id"]
    assert await mrepo.cast_vote(rnd["id"], a, b)
    await mrepo.close_round(rnd["id"])
    assert not await mrepo.cast_vote(rnd["id"], a, a)
    assert not await mrepo.cast_vote(rnd["id"], b, a)
    votes = await mrepo.list_votes(rnd["id"])
    assert [(v["voter_id"], v["target_id"]) for v in votes] == [(a, b)]


async def test_closed_round_votes(mrepo, mgame, mrunning):
    a, b, c = (mrunning[u]["id"] for u in (11, 12, 13))
    first = await mrepo.get_current_round(mgame["id"])
    await mrepo.cast_vote(first["id"], a, b)
    await mrepo.cast_vote(first["id"], c, b)
    assert await mrepo.closed_round_votes(mgame["id"]) == []
    await mrepo.close_round(first["id"])
    second = await mrepo.begin_round(mgame["id"], "p2", LATER)
    await mrepo.close_round(second["id"])
    third = await mrepo.begin_round(mgame["id"], "p3", LATER)
    await mrepo.cast_vote(third["id"], a, a)
    assert await mrepo.closed_round_votes(mgame["id"]) == [[b, b], []]


async def set_deadline(mrepo, round_id, deadline):
    async with mrepo.db.transaction() as conn:
        await conn.execute(
            "UPDATE mostlikely_rounds SET deadline_at = ? WHERE id = ?", (deadline, round_id)
        )


async def test_list_overdue_rounds(mrepo, mgame, mrunning):
    now = "2026-06-01T00:00:00+00:00"
    rnd = await mrepo.get_current_round(mgame["id"])
    assert await mrepo.list_overdue_rounds(now) == []
    await set_deadline(mrepo, rnd["id"], "2026-05-31T23:59:59+00:00")
    assert [r["id"] for r in await mrepo.list_overdue_rounds(now)] == [rnd["id"]]
    await set_deadline(mrepo, rnd["id"], now)
    assert [r["id"] for r in await mrepo.list_overdue_rounds(now)] == [rnd["id"]]
    await mrepo.close_round(rnd["id"])
    assert await mrepo.list_overdue_rounds(now) == []
    second = await mrepo.begin_round(mgame["id"], "p2", "2026-05-01T00:00:00+00:00")
    assert [r["id"] for r in await mrepo.list_overdue_rounds(now)] == [second["id"]]
    await mrepo.finish_game(mgame["id"])
    assert await mrepo.list_overdue_rounds(now) == []


async def test_list_stalled_games(mrepo, mgame):
    for uid in (11, 12, 13):
        await mrepo.join(mgame["id"], uid)
    assert await mrepo.list_stalled_games(LATER) == []
    await mrepo.start_game(mgame["id"], 4)
    assert [g["id"] for g in await mrepo.list_stalled_games(LATER)] == [mgame["id"]]
    assert await mrepo.list_stalled_games("2000-01-01T00:00:00+00:00") == []
    rnd = await mrepo.begin_round(mgame["id"], "p1", LATER)
    assert await mrepo.list_stalled_games(LATER) == []
    await mrepo.close_round(rnd["id"])
    assert [g["id"] for g in await mrepo.list_stalled_games(LATER)] == [mgame["id"]]
    assert await mrepo.list_stalled_games("2000-01-01T00:00:00+00:00") == []
    second = await mrepo.begin_round(mgame["id"], "p2", LATER)
    await mrepo.abandon_round(second["id"])
    assert [g["id"] for g in await mrepo.list_stalled_games(LATER)] == [mgame["id"]]
    await mrepo.finish_game(mgame["id"])
    assert await mrepo.list_stalled_games(LATER) == []


async def test_finished_prompts_only_finished(mrepo, mgame, mrunning):
    a, b, c = (mrunning[u]["id"] for u in (11, 12, 13))
    first = await mrepo.get_current_round(mgame["id"])
    await mrepo.cast_vote(first["id"], a, c)
    await mrepo.cast_vote(first["id"], b, c)
    await mrepo.leave(c)
    await mrepo.close_round(first["id"])
    second = await mrepo.begin_round(mgame["id"], "p2", LATER)
    await mrepo.close_round(second["id"])
    third = await mrepo.begin_round(mgame["id"], "p3", LATER)
    await mrepo.cast_vote(third["id"], a, a)
    assert await mrepo.finished_prompts(1) == {}
    await mrepo.finish_game(mgame["id"])
    cancelled = await mrepo.create_game(1, "Lobby only", 20, 555)
    await mrepo.join(cancelled["id"], 20)
    await mrepo.finish_game(cancelled["id"])
    live = await mrepo.create_game(1, "Running", 30, 555)
    await mrepo.join(live["id"], 30)
    await mrepo.start_game(live["id"], 3)
    await mrepo.begin_round(live["id"], "p", LATER)

    games = await mrepo.finished_prompts(1)
    assert list(games) == [mgame["id"]]
    name_car, p2 = games[mgame["id"]]
    assert name_car.prompt == "name their car" and name_car.targets == (13, 13)
    assert name_car.closed_at
    assert p2.prompt == "p2" and p2.targets == ()
    assert await mrepo.finished_prompts(2) == {}


async def test_games_played(mrepo, mgame, mrunning):
    first = await mrepo.get_current_round(mgame["id"])
    await mrepo.join(mgame["id"], 14)
    await mrepo.leave(mrunning[13]["id"])
    await mrepo.close_round(first["id"])
    assert await mrepo.games_played(1) == {}
    await mrepo.finish_game(mgame["id"])
    assert await mrepo.games_played(1) == {11: 1, 12: 1, 13: 1}
    again = await mrepo.create_game(1, "Second", 11, 555)
    for uid in (11, 14):
        await mrepo.join(again["id"], uid)
    await mrepo.start_game(again["id"], 1)
    rnd = await mrepo.begin_round(again["id"], "p", LATER)
    await mrepo.close_round(rnd["id"])
    await mrepo.finish_game(again["id"])
    assert await mrepo.games_played(1) == {11: 2, 12: 1, 13: 1, 14: 1}

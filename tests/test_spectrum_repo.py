"""Spectrum database access."""

import random

import aiosqlite
import pytest

from gaminator.core.repo import AlreadyJoined
from gaminator.games.spectrum.repo import GameFull, SpectrumRepo
from gaminator.games.spectrum.services.rules import ScoredTurn
from gaminator.games.spectrum.services.scales import Scale

LATER = "2999-01-01T00:00:00+00:00"
EARLIER = "2000-01-01T00:00:00+00:00"
USERS = (11, 12, 13)
DECK = [Scale(f"left {n}", f"right {n}") for n in range(80)]


@pytest.fixture
async def srepo(db):
    return SpectrumRepo(db)


@pytest.fixture
async def sgame(srepo):
    return await srepo.create_game(guild_id=1, name="Test", created_by=11, channel_id=555)


@pytest.fixture
async def srunning(srepo, sgame):
    """Three players (user ids 11, 12, 13), game started, round 1 dealt. Players by user id."""
    for uid in USERS:
        await srepo.join(sgame["id"], uid)
    await srepo.start_game(sgame["id"], 3)
    await srepo.begin_round(sgame["id"], DECK[:12], random.Random(1))
    return {uid: await srepo.get_player(sgame["id"], uid) for uid in USERS}


async def run_sql(srepo, sql, params=()):
    async with srepo.db.transaction() as conn:
        await conn.execute(sql, params)


async def turns_of(srepo, rnd):
    return await srepo._all(
        "SELECT * FROM spectrum_turns WHERE round_id = ? ORDER BY position", (rnd["id"],)
    )


async def open_next(srepo, game, deadline=LATER):
    rnd = await srepo.get_current_round(game["id"])
    turn = await srepo.next_turn(rnd["id"])
    assert await srepo.open_turn(turn["id"], deadline)
    return await srepo.get_turn(turn["id"])


async def clue(srepo, turn, deadline=LATER, text="a clue"):
    assert await srepo.save_clue(turn["id"], turn["giver_id"], text, deadline)
    return await srepo.get_turn(turn["id"])


async def guessing(srepo, game, deadline=LATER):
    """Open the next turn and save its clue. Returns the turn."""
    return await clue(srepo, await open_next(srepo, game), deadline)


async def others(srepo, game, turn):
    """Player ids of everyone but the turn's giver, in join order."""
    players = await srepo.list_players(game["id"])
    return [p["id"] for p in players if p["id"] != turn["giver_id"]]


async def test_create_sets_host_and_channel(srepo, sgame):
    assert sgame["status"] == "open" and sgame["host_id"] == 11 and sgame["channel_id"] == 555
    assert sgame["max_rounds"] == 1 and sgame["lobby_message_id"] is None
    await srepo.set_lobby_message(sgame["id"], 777)
    await srepo.set_host(sgame["id"], 12)
    game = await srepo.get_game(sgame["id"])
    assert game["lobby_message_id"] == 777 and game["host_id"] == 12
    assert (await srepo.get_current_game(1))["id"] == sgame["id"]
    assert await srepo.get_current_game(2) is None


async def test_one_game_per_guild(srepo, sgame):
    with pytest.raises(aiosqlite.IntegrityError):
        await srepo.create_game(1, "Second", 12, 555)
    await srepo.create_game(2, "Elsewhere", 12, 556)
    assert await srepo.finish_game(sgame["id"])
    assert await srepo.get_current_game(1) is None
    await srepo.create_game(1, "Second", 12, 555)


async def test_start_game_twice(srepo, sgame):
    assert await srepo.start_game(sgame["id"], 2)
    assert not await srepo.start_game(sgame["id"], 5)
    game = await srepo.get_game(sgame["id"])
    assert game["status"] == "active" and game["max_rounds"] == 2 and game["started_at"]


async def test_join_and_duplicate(srepo, sgame):
    player, rejoined = await srepo.join(sgame["id"], 11)
    assert player["user_id"] == 11 and not rejoined
    with pytest.raises(AlreadyJoined):
        await srepo.join(sgame["id"], 11)
    await srepo.join(sgame["id"], 12)
    assert [p["user_id"] for p in await srepo.list_players(sgame["id"])] == [11, 12]
    assert (await srepo.get_player_by_id(player["id"]))["user_id"] == 11
    assert await srepo.get_player(sgame["id"], 99) is None


async def test_join_cap(srepo, sgame):
    for uid in range(100, 112):
        await srepo.join(sgame["id"], uid)
    with pytest.raises(GameFull) as full:
        await srepo.join(sgame["id"], 200)
    assert str(full.value) == "The game is full (12 players)."
    first = await srepo.get_player(sgame["id"], 100)
    assert await srepo.leave(first["id"])
    await srepo.join(sgame["id"], 200)
    with pytest.raises(GameFull):
        await srepo.join(sgame["id"], 100)


async def test_rejoin_reuses_row(srepo, sgame):
    player, _ = await srepo.join(sgame["id"], 11)
    assert await srepo.leave(player["id"])
    back, rejoined = await srepo.join(sgame["id"], 11)
    assert rejoined and back["id"] == player["id"] and back["left_at"] is None
    assert len(await srepo.list_players(sgame["id"])) == 1


async def test_leave_twice(srepo, sgame):
    player, _ = await srepo.join(sgame["id"], 11)
    assert await srepo.leave(player["id"])
    assert not await srepo.leave(player["id"])
    assert await srepo.list_players(sgame["id"], active_only=True) == []
    assert len(await srepo.list_players(sgame["id"])) == 1


async def test_begin_round_orders_turns(srepo, sgame):
    for uid in (11, 12, 13, 14, 15):
        await srepo.join(sgame["id"], uid)
    gone = await srepo.get_player(sgame["id"], 15)
    await srepo.leave(gone["id"])
    await srepo.start_game(sgame["id"], 2)
    rnd = await srepo.begin_round(sgame["id"], DECK[:12], random.Random(7))
    assert rnd["number"] == 1 and rnd["closed_at"] is None
    turns = await turns_of(srepo, rnd)
    assert [t["position"] for t in turns] == [1, 2, 3, 4]
    assert [t["id"] for t in turns] == sorted(t["id"] for t in turns)
    active = {p["id"] for p in await srepo.list_players(sgame["id"], active_only=True)}
    assert {t["giver_id"] for t in turns} == active and gone["id"] not in active
    assert [(t["left_end"], t["right_end"]) for t in turns] == [(s.left, s.right) for s in DECK[:4]]
    assert all(1 <= t["target"] <= 100 for t in turns)
    assert all(t["started_at"] is None and t["closed_at"] is None for t in turns)
    orders = set()
    for seed in range(8):
        game = await srepo.create_game(100 + seed, "Other", 11, 555)
        for uid in (11, 12, 13, 14):
            await srepo.join(game["id"], uid)
        await srepo.start_game(game["id"], 1)
        other = await srepo.begin_round(game["id"], DECK[:12], random.Random(seed))
        players = {p["id"]: p["user_id"] for p in await srepo.list_players(game["id"])}
        orders.add(tuple(players[t["giver_id"]] for t in await turns_of(srepo, other)))
    assert len(orders) > 1
    assert await srepo.used_scales(sgame["id"]) == set(DECK[:4])


async def test_target_stored_at_round_start(srepo, sgame, srunning):
    rnd = await srepo.get_current_round(sgame["id"])
    before = {t["id"]: t["target"] for t in await turns_of(srepo, rnd)}
    turn = await guessing(srepo, sgame)
    assert turn["target"] == before[turn["id"]]
    assert {t["id"]: t["target"] for t in await turns_of(srepo, rnd)} == before
    again = SpectrumRepo(srepo.db)
    assert (await again.turn_context(turn["id"], 11))["target"] == before[turn["id"]]


async def test_begin_round_refuses(srepo, sgame):
    for uid in USERS:
        await srepo.join(sgame["id"], uid)
    assert await srepo.begin_round(sgame["id"], DECK[:12], random.Random(1)) is None
    await srepo.start_game(sgame["id"], 2)
    first = await srepo.begin_round(sgame["id"], DECK[:12], random.Random(1))
    assert await srepo.begin_round(sgame["id"], DECK[:12], random.Random(1)) is None
    assert await srepo.rounds_played(sgame["id"]) == 0
    for turn in await turns_of(srepo, first):
        await srepo.close_turn(turn["id"], "skipped")
    assert await srepo.close_round(first["id"])
    assert await srepo.rounds_played(sgame["id"]) == 1
    second = await srepo.begin_round(sgame["id"], DECK[12:24], random.Random(1))
    assert second["number"] == 2
    assert (await srepo.get_current_round(sgame["id"]))["id"] == second["id"]
    assert (await srepo.get_round(first["id"]))["number"] == 1
    await srepo.finish_game(sgame["id"])
    assert await srepo.begin_round(sgame["id"], DECK[:12], random.Random(1)) is None


async def test_add_turn_appends_once(srepo, sgame, srunning):
    rnd = await srepo.get_current_round(sgame["id"])
    late, _ = await srepo.join(sgame["id"], 14)
    assert not await srepo.has_turn(rnd["id"], late["id"])
    assert await srepo.add_turn(rnd["id"], late["id"], Scale("Up", "Down"), 42)
    assert await srepo.has_turn(rnd["id"], late["id"])
    assert not await srepo.add_turn(rnd["id"], late["id"], Scale("In", "Out"), 43)
    turns = await turns_of(srepo, rnd)
    assert [t["position"] for t in turns] == [1, 2, 3, 4]
    last = turns[-1]
    assert (last["giver_id"], last["left_end"], last["right_end"], last["target"]) == (
        late["id"],
        "Up",
        "Down",
        42,
    )
    assert last["id"] == max(t["id"] for t in turns)
    for turn in turns:
        await srepo.close_turn(turn["id"], "skipped")
    assert await srepo.close_round(rnd["id"])
    later, _ = await srepo.join(sgame["id"], 15)
    assert not await srepo.add_turn(rnd["id"], later["id"], Scale("In", "Out"), 43)
    assert not await srepo.has_turn(rnd["id"], later["id"])


async def test_turn_context(srepo, sgame, srunning):
    turn = await open_next(srepo, sgame, deadline="2030-01-01T00:00:00+00:00")
    giver = await srepo.get_player_by_id(turn["giver_id"])
    assert turn["giver_user_id"] == giver["user_id"]
    assert (turn["round_number"], turn["game_id"], turn["turn_count"]) == (1, sgame["id"], 3)
    assert turn["channel_id"] == 555
    mine = await srepo.turn_context(turn["id"], giver["user_id"])
    assert mine["player_id"] == mine["giver_id"] == giver["id"]
    assert mine["status"] == "active" and mine["game_id"] == sgame["id"]
    assert mine["target"] == turn["target"] and mine["clue"] is None
    assert mine["clue_deadline_at"] == "2030-01-01T00:00:00+00:00"
    assert (mine["left_end"], mine["right_end"]) == (turn["left_end"], turn["right_end"])
    assert not mine["may_guess"] and mine["my_guess"] is None
    outsider = await srepo.turn_context(turn["id"], 99)
    assert outsider["player_id"] is None and outsider["giver_user_id"] == giver["user_id"]
    assert await srepo.turn_context(9999, 11) is None
    turn = await clue(srepo, turn)
    other = next(uid for uid in USERS if uid != giver["user_id"])
    theirs = await srepo.turn_context(turn["id"], other)
    assert theirs["may_guess"] and theirs["my_guess"] is None and theirs["clue"] == "a clue"
    assert await srepo.save_guess(turn["id"], theirs["player_id"], 64)
    assert (await srepo.turn_context(turn["id"], other))["my_guess"] == 64
    assert not (await srepo.turn_context(turn["id"], giver["user_id"]))["may_guess"]
    await srepo.leave(theirs["player_id"])
    assert (await srepo.turn_context(turn["id"], other))["left_at"] is not None


async def test_open_turn_refuses(srepo, sgame, srunning):
    rnd = await srepo.get_current_round(sgame["id"])
    first, second, third = await turns_of(srepo, rnd)
    assert (await srepo.next_turn(rnd["id"]))["id"] == first["id"]
    assert await srepo.current_turn(rnd["id"]) is None
    assert await srepo.open_turn(first["id"], LATER)
    assert not await srepo.open_turn(first["id"], LATER)
    assert not await srepo.open_turn(second["id"], LATER)
    assert (await srepo.current_turn(rnd["id"]))["id"] == first["id"]
    assert (await srepo.next_turn(rnd["id"]))["id"] == second["id"]
    assert await srepo.close_turn(first["id"], "no_clue")
    assert not await srepo.open_turn(first["id"], LATER)
    await run_sql(
        srepo, "UPDATE spectrum_rounds SET closed_at = ? WHERE id = ?", (LATER, rnd["id"])
    )
    assert not await srepo.open_turn(second["id"], LATER)
    await run_sql(srepo, "UPDATE spectrum_rounds SET closed_at = NULL WHERE id = ?", (rnd["id"],))
    await srepo.finish_game(sgame["id"])
    assert not await srepo.open_turn(third["id"], LATER)


async def test_turn_messages(srepo, sgame, srunning):
    turn = await open_next(srepo, sgame)
    assert turn["turn_message_id"] is None and turn["guess_message_id"] is None
    await srepo.set_turn_message(turn["id"], 1001)
    await srepo.set_guess_message(turn["id"], 1002)
    turn = await srepo.get_turn(turn["id"])
    assert (turn["turn_message_id"], turn["guess_message_id"]) == (1001, 1002)


async def test_save_clue_guards(srepo, sgame, srunning):
    rnd = await srepo.get_current_round(sgame["id"])
    waiting = (await turns_of(srepo, rnd))[1]
    assert not await srepo.save_clue(waiting["id"], waiting["giver_id"], "early", LATER)
    turn = await open_next(srepo, sgame)
    wrong = (await others(srepo, sgame, turn))[0]
    assert not await srepo.save_clue(turn["id"], wrong, "not mine", LATER)
    await srepo.leave(turn["giver_id"])
    assert not await srepo.save_clue(turn["id"], turn["giver_id"], "gone", LATER)
    await srepo.join(sgame["id"], turn["giver_user_id"])
    assert await srepo.save_clue(turn["id"], turn["giver_id"], "rain", LATER)
    assert not await srepo.save_clue(turn["id"], turn["giver_id"], "again", LATER)
    saved = await srepo.get_turn(turn["id"])
    assert saved["clue"] == "rain" and saved["clue_at"] and saved["guess_deadline_at"] == LATER
    assert await srepo.close_turn(turn["id"], "revealed")
    second = await open_next(srepo, sgame)
    assert await srepo.close_turn(second["id"], "no_clue")
    assert not await srepo.save_clue(second["id"], second["giver_id"], "late", LATER)
    third = await open_next(srepo, sgame)
    await srepo.finish_game(sgame["id"])
    assert not await srepo.save_clue(third["id"], third["giver_id"], "over", LATER)


async def test_save_clue_snapshots_guessers(srepo, sgame, srunning):
    extra, _ = await srepo.join(sgame["id"], 14)
    await srepo.leave(extra["id"])
    turn = await open_next(srepo, sgame)
    assert turn["eligible_guessers"] is None
    turn = await clue(srepo, turn)
    assert turn["eligible_guessers"] == 2
    rows = await srepo._all(
        "SELECT player_id FROM spectrum_turn_guessers WHERE turn_id = ?", (turn["id"],)
    )
    assert {r["player_id"] for r in rows} == set(await others(srepo, sgame, turn)) - {extra["id"]}
    late, _ = await srepo.join(sgame["id"], 15)
    assert not (await srepo.turn_context(turn["id"], 15))["may_guess"]
    assert not await srepo.save_guess(turn["id"], late["id"], 50)
    assert (await srepo.get_turn(turn["id"]))["eligible_guessers"] == 2
    assert await srepo.guess_progress(turn["id"]) == (0, 2)


async def test_close_turn_outcomes(srepo, sgame, srunning):
    rnd = await srepo.get_current_round(sgame["id"])
    waiting = (await turns_of(srepo, rnd))[2]
    assert not await srepo.close_turn(waiting["id"], "no_clue")
    assert not await srepo.close_turn(waiting["id"], "revealed")
    assert await srepo.close_turn(waiting["id"], "skipped")
    assert not await srepo.close_turn(waiting["id"], "abandoned")
    turn = await open_next(srepo, sgame)
    assert not await srepo.close_turn(turn["id"], "revealed")
    turn = await clue(srepo, turn)
    assert not await srepo.close_turn(turn["id"], "no_clue")
    assert not await srepo.close_turn(turn["id"], "skipped")
    assert await srepo.close_turn(turn["id"], "revealed")
    assert not await srepo.close_turn(turn["id"], "revealed")
    closed = await srepo.get_turn(turn["id"])
    assert closed["outcome"] == "revealed" and closed["closed_at"]
    second = await open_next(srepo, sgame)
    assert await srepo.close_turn(second["id"], "no_clue")
    assert (await srepo.get_turn(second["id"]))["outcome"] == "no_clue"
    assert (await srepo.get_turn(waiting["id"]))["outcome"] == "skipped"
    with pytest.raises(ValueError):
        await srepo.close_turn(second["id"], "lost")


async def test_close_turn_abandons_any_open_step(srepo, sgame, srunning):
    turn = await open_next(srepo, sgame)
    assert await srepo.close_turn(turn["id"], "abandoned")
    clued = await guessing(srepo, sgame)
    assert await srepo.close_turn(clued["id"], "abandoned")
    assert (await srepo.get_turn(clued["id"]))["outcome"] == "abandoned"


async def test_close_round_waits_for_open_turns(srepo, sgame, srunning):
    rnd = await srepo.get_current_round(sgame["id"])
    turns = await turns_of(srepo, rnd)
    for turn in turns[:2]:
        await srepo.close_turn(turn["id"], "skipped")
    assert not await srepo.close_round(rnd["id"])
    await srepo.close_turn(turns[2]["id"], "skipped")
    late, _ = await srepo.join(sgame["id"], 14)
    assert await srepo.add_turn(rnd["id"], late["id"], Scale("Up", "Down"), 42)
    assert not await srepo.close_round(rnd["id"])
    appended = await srepo.next_turn(rnd["id"])
    assert appended["giver_id"] == late["id"]
    await srepo.close_turn(appended["id"], "skipped")
    assert await srepo.close_round(rnd["id"])
    assert not await srepo.close_round(rnd["id"])
    assert (await srepo.get_round(rnd["id"]))["closed_at"]


async def test_save_guess_and_change(srepo, sgame, srunning):
    turn = await guessing(srepo, sgame)
    first, second = await others(srepo, sgame, turn)
    assert await srepo.get_guess(turn["id"], first) is None
    assert await srepo.save_guess(turn["id"], first, 40)
    assert await srepo.save_guess(turn["id"], first, 62)
    assert (await srepo.get_guess(turn["id"], first))["value"] == 62
    assert await srepo.guess_progress(turn["id"]) == (1, 2)
    assert await srepo.save_guess(turn["id"], second, 5)
    assert await srepo.guess_progress(turn["id"]) == (2, 2)
    players = {p["id"]: p["user_id"] for p in await srepo.list_players(sgame["id"])}
    guesses = [(g["user_id"], g["value"]) for g in await srepo.turn_guesses(turn["id"])]
    assert guesses == [(players[second], 5), (players[first], 62)]


async def test_save_guess_refused(srepo, sgame, srunning):
    turn = await open_next(srepo, sgame)
    first, _ = await others(srepo, sgame, turn)
    assert not await srepo.save_guess(turn["id"], first, 40)
    turn = await clue(srepo, turn)
    assert not await srepo.save_guess(turn["id"], turn["giver_id"], 40)
    late, _ = await srepo.join(sgame["id"], 14)
    assert not await srepo.save_guess(turn["id"], late["id"], 40)
    assert await srepo.save_guess(turn["id"], first, 40)
    assert await srepo.close_turn(turn["id"], "revealed")
    assert not await srepo.save_guess(turn["id"], first, 90)
    assert (await srepo.get_guess(turn["id"], first))["value"] == 40
    assert await srepo.turn_guesses(9999) == []


async def test_guess_progress_counts_snapshot_still_in(srepo, sgame, srunning):
    turn = await guessing(srepo, sgame)
    first, second = await others(srepo, sgame, turn)
    await srepo.join(sgame["id"], 14)
    assert await srepo.guess_progress(turn["id"]) == (0, 2)
    assert await srepo.save_guess(turn["id"], first, 40)
    assert await srepo.guess_progress(turn["id"]) == (1, 2)
    await srepo.leave(first)
    assert await srepo.guess_progress(turn["id"]) == (0, 1)
    assert [g["value"] for g in await srepo.turn_guesses(turn["id"])] == [40]
    await srepo.leave(turn["giver_id"])
    assert await srepo.guess_progress(turn["id"]) == (0, 1)
    await srepo.leave(second)
    assert await srepo.guess_progress(turn["id"]) == (0, 0)
    await srepo.join(sgame["id"], (await srepo.get_player_by_id(first))["user_id"])
    assert await srepo.guess_progress(turn["id"]) == (1, 1)


async def test_finish_game_twice(srepo, sgame, srunning):
    rnd = await srepo.get_current_round(sgame["id"])
    turn = await open_next(srepo, sgame)
    assert await srepo.finish_game(sgame["id"])
    assert not await srepo.finish_game(sgame["id"])
    game = await srepo.get_game(sgame["id"])
    assert game["status"] == "finished" and game["finished_at"]
    closed = await srepo.get_turn(turn["id"])
    assert closed["outcome"] == "abandoned" and closed["closed_at"]
    waiting = [t for t in await turns_of(srepo, rnd) if t["id"] != turn["id"]]
    assert all(t["outcome"] is None and t["closed_at"] is None for t in waiting)
    assert (await srepo.get_round(rnd["id"]))["closed_at"]


async def test_list_overdue_clues(srepo, sgame, srunning):
    turn = await open_next(srepo, sgame, deadline=LATER)
    assert await srepo.list_overdue_clues(EARLIER) == []
    await srepo.set_turn_message(turn["id"], 1001)
    due = await srepo.list_overdue_clues(LATER)
    assert [(t["id"], t["turn_message_id"], t["game_id"]) for t in due] == [
        (turn["id"], 1001, sgame["id"])
    ]
    assert await srepo.list_overdue_guessing(LATER) == []
    await clue(srepo, turn)
    assert await srepo.list_overdue_clues(LATER) == []
    await srepo.close_turn(turn["id"], "revealed")
    second = await open_next(srepo, sgame, deadline=EARLIER)
    assert [t["id"] for t in await srepo.list_overdue_clues(EARLIER)] == [second["id"]]
    await srepo.finish_game(sgame["id"])
    assert await srepo.list_overdue_clues(LATER) == []


async def test_list_overdue_guessing(srepo, sgame, srunning):
    turn = await guessing(srepo, sgame, deadline=LATER)
    assert await srepo.list_overdue_guessing(EARLIER) == []
    await srepo.set_guess_message(turn["id"], 1002)
    due = await srepo.list_overdue_guessing(LATER)
    assert [(t["id"], t["guess_message_id"], t["game_id"]) for t in due] == [
        (turn["id"], 1002, sgame["id"])
    ]
    await srepo.close_turn(turn["id"], "revealed")
    assert await srepo.list_overdue_guessing(LATER) == []
    second = await guessing(srepo, sgame, deadline=EARLIER)
    assert [t["id"] for t in await srepo.list_overdue_guessing(EARLIER)] == [second["id"]]
    await srepo.finish_game(sgame["id"])
    assert await srepo.list_overdue_guessing(LATER) == []


async def test_list_stalled_games(srepo, sgame):
    async def stalled(before=LATER):
        return [g["id"] for g in await srepo.list_stalled_games(before)]

    for uid in USERS:
        await srepo.join(sgame["id"], uid)
    assert await stalled() == []
    await srepo.start_game(sgame["id"], 2)
    assert await stalled() == [sgame["id"]]
    assert await stalled(EARLIER) == []
    rnd = await srepo.begin_round(sgame["id"], DECK[:12], random.Random(1))
    assert await stalled() == [sgame["id"]]
    assert await stalled(EARLIER) == []
    turn = await open_next(srepo, sgame)
    assert await stalled() == []
    await clue(srepo, turn)
    assert await stalled() == []
    await srepo.close_turn(turn["id"], "revealed")
    assert await stalled() == [sgame["id"]]
    assert await stalled(EARLIER) == []
    for waiting in await turns_of(srepo, rnd):
        await srepo.close_turn(waiting["id"], "skipped")
    assert await srepo.close_round(rnd["id"])
    assert await stalled() == [sgame["id"]]
    await srepo.finish_game(sgame["id"])
    assert await stalled() == []


async def test_scored_turns(srepo, sgame, srunning):
    assert await srepo.scored_turns(sgame["id"]) == []
    turn = await guessing(srepo, sgame)
    first, second = await others(srepo, sgame, turn)
    await srepo.save_guess(turn["id"], first, 40)
    await srepo.save_guess(turn["id"], second, 70)
    assert await srepo.scored_turns(sgame["id"]) == []
    await srepo.leave(second)
    await srepo.close_turn(turn["id"], "revealed")
    unclued = await open_next(srepo, sgame)
    await srepo.close_turn(unclued["id"], "no_clue")
    lost = await guessing(srepo, sgame)
    await srepo.close_turn(lost["id"], "abandoned")
    players = {p["id"]: p["user_id"] for p in await srepo.list_players(sgame["id"])}
    closed = await srepo.get_turn(turn["id"])
    expected = ScoredTurn(
        turn["giver_user_id"],
        turn["left_end"],
        turn["right_end"],
        turn["target"],
        "a clue",
        closed["closed_at"],
        tuple(sorted([(players[first], 40), (players[second], 70)])),
    )
    assert await srepo.scored_turns(sgame["id"]) == [expected]
    rnd = await srepo.get_current_round(sgame["id"])
    assert await srepo.scored_turns(sgame["id"], rnd["id"]) == [expected]
    assert await srepo.scored_turns(sgame["id"], rnd["id"] + 1) == []
    assert await srepo.game_user_ids(sgame["id"]) == [11, 12, 13]
    assert await srepo.game_user_ids(sgame["id"], rnd["id"]) == [11, 12, 13]
    assert await srepo.game_user_ids(sgame["id"], rnd["id"] + 1) == []


async def test_game_user_ids_need_a_clue(srepo, sgame, srunning):
    turn = await open_next(srepo, sgame)
    assert await srepo.game_user_ids(sgame["id"]) == []
    await clue(srepo, turn)
    await srepo.join(sgame["id"], 14)
    assert await srepo.game_user_ids(sgame["id"]) == [11, 12, 13]


async def test_revealed_guess_counts_from_stored_columns(srepo, sgame, srunning):
    first = await guessing(srepo, sgame)
    one, two = await others(srepo, sgame, first)
    await srepo.save_guess(first["id"], one, 40)
    await srepo.close_turn(first["id"], "revealed")
    unclued = await open_next(srepo, sgame)
    await srepo.close_turn(unclued["id"], "no_clue")
    third = await guessing(srepo, sgame)
    assert await srepo.revealed_guess_counts(sgame["id"]) == [(2, 1)]
    await srepo.close_turn(third["id"], "revealed")
    assert await srepo.revealed_guess_counts(sgame["id"]) == [(2, 1), (2, 0)]
    await srepo.leave(one)
    await srepo.leave(two)
    await srepo.join(sgame["id"], 14)
    await srepo.join(sgame["id"], 15)
    assert await srepo.revealed_guess_counts(sgame["id"]) == [(2, 1), (2, 0)]


async def test_round_clue_counts(srepo, sgame, srunning):
    rnd = await srepo.get_current_round(sgame["id"])
    assert await srepo.round_clue_counts(rnd["id"]) == (0, 0)
    assert await srepo.last_closed_round(sgame["id"]) is None
    first = await open_next(srepo, sgame)
    await srepo.close_turn(first["id"], "no_clue")
    assert await srepo.round_clue_counts(rnd["id"]) == (1, 0)
    second = await guessing(srepo, sgame)
    assert await srepo.round_clue_counts(rnd["id"]) == (1, 1)
    await srepo.close_turn(second["id"], "abandoned")
    third = await open_next(srepo, sgame)
    await srepo.close_turn(third["id"], "skipped")
    assert await srepo.round_clue_counts(rnd["id"]) == (1, 1)
    assert await srepo.close_round(rnd["id"])
    assert (await srepo.last_closed_round(sgame["id"]))["id"] == rnd["id"]


async def test_abandoned_streak(srepo, sgame):
    for uid in (11, 12, 13, 14, 15, 16):
        await srepo.join(sgame["id"], uid)
    await srepo.start_game(sgame["id"], 2)
    await srepo.begin_round(sgame["id"], DECK[:12], random.Random(1))
    assert await srepo.abandoned_streak(sgame["id"]) == 0
    for expected in (1, 2):
        turn = await open_next(srepo, sgame)
        await srepo.close_turn(turn["id"], "abandoned")
        assert await srepo.abandoned_streak(sgame["id"]) == expected
    turn = await open_next(srepo, sgame)
    await srepo.close_turn(turn["id"], "no_clue")
    assert await srepo.abandoned_streak(sgame["id"]) == 0
    for expected in (1, 2, 3):
        turn = await open_next(srepo, sgame)
        await srepo.close_turn(turn["id"], "abandoned")
        assert await srepo.abandoned_streak(sgame["id"]) == expected
    other = await srepo.create_game(2, "Other", 11, 556)
    assert await srepo.abandoned_streak(other["id"]) == 0


async def play_revealed(srepo, game, guesses=()):
    """Open the next turn, save a clue and the given (player id, value) guesses, and reveal it."""
    turn = await guessing(srepo, game)
    for player_id, value in guesses:
        assert await srepo.save_guess(turn["id"], player_id, value)
    assert await srepo.close_turn(turn["id"], "revealed")
    return turn


async def test_finished_turns_only_finished(srepo, sgame, srunning):
    turn = await play_revealed(srepo, sgame)
    first, _ = await others(srepo, sgame, turn)
    again = await guessing(srepo, sgame)
    await srepo.close_turn(again["id"], "abandoned")
    assert await srepo.finished_turns(1) == {}
    other = await srepo.create_game(2, "Elsewhere", 21, 556)
    for uid in (21, 22):
        await srepo.join(other["id"], uid)
    await srepo.start_game(other["id"], 1)
    await srepo.begin_round(other["id"], DECK[:12], random.Random(1))
    elsewhere = await guessing(srepo, other)
    (guesser,) = await others(srepo, other, elsewhere)
    await srepo.save_guess(elsewhere["id"], guesser, 30)
    await srepo.close_turn(elsewhere["id"], "revealed")
    await srepo.finish_game(other["id"])
    await srepo.finish_game(sgame["id"])
    mine = await srepo.finished_turns(1)
    assert list(mine) == [sgame["id"]]
    assert mine[sgame["id"]] == await srepo.scored_turns(sgame["id"])
    assert len(mine[sgame["id"]]) == 1 and mine[sgame["id"]][0].guesses == ()
    theirs = await srepo.finished_turns(2)
    assert list(theirs) == [other["id"]]
    players = {p["id"]: p["user_id"] for p in await srepo.list_players(other["id"])}
    assert theirs[other["id"]][0].guesses == ((players[guesser], 30),)
    assert theirs[other["id"]][0].giver_id == elsewhere["giver_user_id"]
    assert await srepo.finished_turns(3) == {}
    assert first in {p["id"] for p in await srepo.list_players(sgame["id"])}


async def test_games_played(srepo, sgame, srunning):
    await open_next(srepo, sgame)
    await srepo.finish_game(sgame["id"])
    assert await srepo.games_played(1) == {}
    second = await srepo.create_game(1, "Second", 11, 555)
    for uid in (11, 12, 13):
        await srepo.join(second["id"], uid)
    await srepo.start_game(second["id"], 1)
    await srepo.begin_round(second["id"], DECK[:12], random.Random(1))
    turn = await play_revealed(srepo, second)
    late, _ = await srepo.join(second["id"], 14)
    assert await srepo.games_played(1) == {}
    lost = await guessing(srepo, second)
    await srepo.close_turn(lost["id"], "abandoned")
    await srepo.leave((await others(srepo, second, turn))[0])
    await srepo.finish_game(second["id"])
    assert await srepo.games_played(1) == {11: 1, 12: 1, 13: 1}
    third = await srepo.create_game(1, "Third", 11, 555)
    for uid in (11, 12, 14):
        await srepo.join(third["id"], uid)
    await srepo.start_game(third["id"], 1)
    await srepo.begin_round(third["id"], DECK[:12], random.Random(1))
    await play_revealed(srepo, third)
    await play_revealed(srepo, third)
    await srepo.finish_game(third["id"])
    assert await srepo.games_played(1) == {11: 2, 12: 2, 13: 1, 14: 1}
    assert await srepo.games_played(2) == {}
    assert late["user_id"] == 14

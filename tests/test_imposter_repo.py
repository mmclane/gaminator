import pytest

from gaminator.core.repo import AlreadyJoined
from gaminator.games.imposter.repo import AlreadyOut, ImposterRepo


@pytest.fixture
async def irepo(db):
    return ImposterRepo(db)


@pytest.fixture
async def igame(irepo):
    return await irepo.create_game(guild_id=1, name="Test", created_by=99)


@pytest.fixture
async def running(irepo, igame):
    """Five players (user ids 11..15), 12 and 14 are imposters, round 1 begun."""
    players = {uid: await irepo.add_player(igame["id"], uid) for uid in (11, 12, 13, 14, 15)}
    await irepo.start_game(igame["id"], 555, [players[12]["id"], players[14]["id"]])
    await irepo.begin_round(igame["id"], "Name a food.", "It's about food.")
    return {uid: await irepo.get_player(igame["id"], uid) for uid in players}


async def test_join_and_settings(irepo, igame):
    await irepo.add_player(igame["id"], 5)
    with pytest.raises(AlreadyJoined):
        await irepo.add_player(igame["id"], 5)
    assert igame["max_rounds"] == 5
    assert igame["imposter_hint"] == 1
    await irepo.update_settings(igame["id"], imposters=2, max_rounds=4, imposter_hint=0)
    g = await irepo.get_game(igame["id"])
    assert (g["imposters"], g["max_rounds"], g["imposter_hint"]) == (2, 4, 0)
    with pytest.raises(ValueError):
        await irepo.update_settings(igame["id"], status="finished")


async def test_start_marks_imposters_and_channel(irepo, igame, running):
    g = await irepo.get_game(igame["id"])
    assert g["status"] == "active" and g["phase"] == "discussion" and g["channel_id"] == 555
    assert g["round"] == 1
    assert {u for u, p in running.items() if p["imposter"]} == {12, 14}
    assert await irepo.alive_counts(igame["id"]) == (3, 2)


async def test_rounds_and_prompts(irepo, igame, running):
    rnd = await irepo.get_current_round(igame["id"])
    assert rnd["number"] == 1 and rnd["prompt"] == "Name a food."
    await irepo.set_next_prompt(igame["id"], "Custom", None)
    assert (await irepo.get_game(igame["id"]))["next_prompt"] == "Custom"
    r2 = await irepo.begin_round(igame["id"], "Custom", None)
    g = await irepo.get_game(igame["id"])
    assert r2["number"] == 2 and g["round"] == 2 and g["next_prompt"] is None
    assert await irepo.used_prompts(igame["id"]) == {"Name a food.", "Custom"}
    await irepo.reroll_round(r2["id"], "Other", "clue")
    r2 = await irepo.get_round(r2["id"])
    assert (r2["prompt"], r2["hint"]) == ("Other", "clue")


async def test_voting_flow(irepo, igame, running):
    rnd = await irepo.get_current_round(igame["id"])
    await irepo.open_voting(rnd["id"], 555, 9001)
    g = await irepo.get_game(igame["id"])
    rnd = await irepo.get_round(rnd["id"])
    assert g["phase"] == "voting" and rnd["vote_message_id"] == 9001
    a, b, c = running[11], running[13], running[15]
    await irepo.cast_vote(rnd["id"], a["id"], b["id"])
    await irepo.cast_vote(rnd["id"], a["id"], c["id"])  # changed their mind
    await irepo.cast_vote(rnd["id"], b["id"], None)
    votes = await irepo.list_votes(rnd["id"])
    assert {(v["voter_id"], v["target_id"]) for v in votes} == {(a["id"], c["id"]), (b["id"], None)}
    assert await irepo.living_vote_count(rnd["id"]) == 2
    assert (await irepo.get_vote(rnd["id"], a["id"]))["target_id"] == c["id"]
    # a voter who leaves no longer counts
    await irepo.eliminate(b["id"], 1, "left")
    assert await irepo.living_vote_count(rnd["id"]) == 1
    assert await irepo.close_round(rnd["id"], c["id"], "innocent")
    assert not await irepo.close_round(rnd["id"], None, "tie")  # already closed
    rnd = await irepo.get_round(rnd["id"])
    assert rnd["closed_at"] and rnd["ejected_id"] == c["id"] and rnd["result"] == "innocent"
    assert (await irepo.get_game(igame["id"]))["phase"] == "discussion"
    with pytest.raises(ValueError):
        await irepo.close_round(rnd["id"], None, "bogus")


async def test_eliminate_and_finish(irepo, igame, running):
    imp = running[12]
    await irepo.eliminate(imp["id"], 1, "caught")
    with pytest.raises(AlreadyOut):
        await irepo.eliminate(imp["id"], 1, "caught")
    with pytest.raises(ValueError):
        await irepo.eliminate(running[11]["id"], 1, "bogus")
    row = await irepo.get_player_by_id(imp["id"])
    assert row["alive"] == 0 and row["eliminated_round"] == 1 and row["cause"] == "caught"
    assert await irepo.alive_counts(igame["id"]) == (3, 1)
    assert len(await irepo.list_players(igame["id"], alive_only=True)) == 4
    await irepo.finish_game(igame["id"], "crew")
    g = await irepo.get_game(igame["id"])
    assert g["status"] == "finished" and g["phase"] == "over" and g["winner"] == "crew"
    assert await irepo.get_current_game(1) is None


async def test_migrations_upgrade_old_tables(tmp_path):
    import sqlite3

    from gaminator.core.db import Database
    from gaminator.games import prepare_database

    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE imposter_games (id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, "
        "name TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'signup', "
        "imposters INTEGER NOT NULL DEFAULT 1, eliminate_innocent INTEGER NOT NULL DEFAULT 1, "
        "max_rounds INTEGER NOT NULL DEFAULT 0, channel_id INTEGER, round INTEGER NOT NULL "
        "DEFAULT 0, next_prompt TEXT, next_hint TEXT, winner TEXT, created_by INTEGER NOT NULL, "
        "created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);"
        "INSERT INTO imposter_games (guild_id, name, status, created_by, created_at) "
        "VALUES (1, 'Old', 'open', 9, 'x');"
    )
    con.commit()
    con.close()
    database = Database(path)
    await database.connect()
    await prepare_database(database)
    repo = ImposterRepo(database)
    game = await repo.get_current_game(1)
    assert game["name"] == "Old" and game["max_rounds"] == 5 and game["imposter_hint"] == 1
    assert "eliminate_innocent" not in game.keys()  # noqa: SIM118  (Row, not a dict)
    await database.close()

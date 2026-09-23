import pytest

from gaminator.games.assassin.repo import AlreadyDead, AlreadyJoined


async def test_one_current_game_per_guild(repo, game):
    assert (await repo.get_current_game(1))["id"] == game["id"]
    await repo.finish_game(game["id"], None)
    assert await repo.get_current_game(1) is None
    g2 = await repo.create_game(1, "Second", 99)
    assert (await repo.get_current_game(1))["id"] == g2["id"]


async def test_join_twice(repo, game):
    await repo.add_player(game["id"], 5)
    with pytest.raises(AlreadyJoined):
        await repo.add_player(game["id"], 5)


async def test_start_sets_targets_and_activity(repo, game, started):
    g = await repo.get_game(game["id"])
    assert g["status"] == "active" and g["started_at"]
    a, b = started[11], started[12]
    assert a["target_id"] == b["id"]
    assert a["poison_word"] == "w1" and a["bait_word"] == "b1"
    assert a["last_message_at"] is not None


async def test_eliminate_closes_chain(repo, game, started):
    a, b, c = started[11], started[12], started[13]
    hunter = await repo.eliminate(b["id"], a["id"], "poison", "fresh", "lure", detail="w1")
    assert hunter["id"] == a["id"]
    assert hunter["target_id"] == c["id"]
    assert hunter["poison_word"] == "fresh" and hunter["bait_word"] == "lure"
    assert hunter["last_kill_at"] is not None
    assert hunter["kills"] == 1
    dead = await repo.get_player_by_id(b["id"])
    assert dead["alive"] == 0 and dead["cause"] == "poison" and dead["eliminated_by"] == a["id"]
    assert await repo.alive_count(game["id"]) == 3
    with pytest.raises(AlreadyDead):
        await repo.eliminate(b["id"], a["id"], "poison", "x")


async def test_eliminate_by_non_hunter_still_retargets_hunter(repo, game, started):
    # C reports A (A hunts... no: A hunts B). D hunts A. If A is arrested, D inherits B.
    a, b, d = started[11], started[12], started[14]
    hunter = await repo.eliminate(a["id"], b["id"], "arrested", "fresh")
    assert hunter["id"] == d["id"]
    assert hunter["target_id"] == b["id"]
    assert (await repo.get_player_by_id(b["id"]))["kills"] == 1


async def test_last_two_clear_target(repo, game, started):
    a, b, c, d = (started[u] for u in (11, 12, 13, 14))
    await repo.eliminate(b["id"], a["id"], "quickdraw", "x")  # A -> C
    await repo.eliminate(c["id"], a["id"], "quickdraw", "y")  # A -> D, D -> A
    hunter = await repo.eliminate(d["id"], a["id"], "quickdraw", "z")
    assert hunter["id"] == a["id"]
    assert hunter["target_id"] is None
    assert await repo.alive_count(game["id"]) == 1


async def test_wrong_reports_and_activity(repo, game, started):
    a = started[11]
    assert await repo.add_wrong_report(a["id"]) == 1
    assert await repo.add_wrong_report(a["id"]) == 2
    await repo.mark_warned(a["id"])
    assert (await repo.get_player_by_id(a["id"]))["inactivity_warned"] == 1
    await repo.touch_activity(game["id"], 11, "2030-01-01T00:00:00+00:00")
    row = await repo.get_player_by_id(a["id"])
    assert row["last_message_at"].startswith("2030") and row["inactivity_warned"] == 0


async def test_settings_and_channels(repo, game):
    await repo.update_settings(game["id"], inactivity_hours=5, reveal_killer=0)
    g = await repo.get_game(game["id"])
    assert g["inactivity_hours"] == 5 and g["reveal_killer"] == 0
    await repo.update_settings(game["id"], announce_channel_id=6, mod_channel_id=5)
    g = await repo.get_game(game["id"])
    assert g["announce_channel_id"] == 6 and g["mod_channel_id"] == 5
    await repo.update_settings(game["id"], mod_channel_id=None)
    g = await repo.get_game(game["id"])
    assert g["mod_channel_id"] is None and g["announce_channel_id"] == 6
    with pytest.raises(ValueError):
        await repo.update_settings(game["id"], status="finished")
    await repo.add_channel(game["id"], 100)
    await repo.add_channel(game["id"], 100)
    await repo.add_channel(game["id"], 200)
    assert await repo.list_channels(game["id"]) == [100, 200]
    assert await repo.remove_channel(game["id"], 100)
    assert not await repo.remove_channel(game["id"], 100)
    await repo.clear_channels(game["id"])
    assert await repo.list_channels(game["id"]) == []


async def test_events_logged(repo, game, started):
    await repo.log_event(game["id"], "false_report", started[11]["id"], started[12]["id"])
    kinds = [e["kind"] for e in await repo.list_events(game["id"])]
    assert kinds == ["false_report", "start"]


async def test_bounty(repo, game, started):
    a, b, c = (started[u] for u in (11, 12, 13))
    assert await repo.last_action_at(game["id"]) is not None
    # nobody has killed yet (last_kill_at is NULL for all): earliest joiner is the candidate
    cand = await repo.bounty_candidate(game["id"])
    assert cand["id"] == a["id"]
    await repo.set_bounty(game["id"], cand["id"])
    g = await repo.get_game(game["id"])
    assert g["bounty_player_id"] == a["id"]
    assert (await repo.list_events(game["id"]))[0]["kind"] == "bounty"
    # A kills B; the bounty clears and A is no longer the candidate
    await repo.eliminate(b["id"], a["id"], "quickdraw", "x", "y")
    g = await repo.get_game(game["id"])
    assert g["bounty_player_id"] is None
    assert (await repo.bounty_candidate(game["id"]))["id"] == c["id"]


async def test_migration_merges_trigger_emoji(tmp_path):
    import sqlite3

    from gaminator.core.db import Database
    from gaminator.games import prepare_database
    from gaminator.games.assassin.repo import AssassinRepo

    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE assassin_games (id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, "
        "name TEXT NOT NULL, status TEXT NOT NULL, kill_emoji TEXT NOT NULL DEFAULT '🔪', "
        "poison_emoji TEXT NOT NULL DEFAULT '☠️', shield_emoji TEXT NOT NULL DEFAULT '🛡️', "
        "trap_emoji TEXT NOT NULL DEFAULT '🪤', bounty_hours INTEGER NOT NULL DEFAULT 48, "
        "bounty_player_id INTEGER, inactivity_hours INTEGER NOT NULL DEFAULT 24, "
        "max_wrong_reports INTEGER NOT NULL DEFAULT 3, reveal_killer INTEGER NOT NULL DEFAULT 1, "
        "winner_id INTEGER, created_by INTEGER NOT NULL, created_at TEXT NOT NULL, "
        "started_at TEXT, finished_at TEXT);"
        "INSERT INTO assassin_games (guild_id, name, status, poison_emoji, created_by, created_at) "
        "VALUES (1, 'Old', 'active', '💀', 9, 'x');"
    )
    con.commit()
    con.close()
    database = Database(path)
    await database.connect()
    await prepare_database(database)
    game = await AssassinRepo(database).get_current_game(1)
    assert game["kill_emoji"] == "💀"  # the old poison reaction becomes the single trigger
    # Columns added later are created on old databases too.
    assert game["announce_channel_id"] is None and game["mod_channel_id"] is None
    assert "trap_emoji" not in game.keys()  # noqa: SIM118  (Row, not a dict)
    await database.close()

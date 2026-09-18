"""Shared plumbing: schema, per-server settings, and the game registry."""

import pytest

from gaminator.core.repo import GuildSettingsRepo
from gaminator.games import GAMES, get_spec
from gaminator.games.imposter.repo import ImposterRepo


async def test_every_game_has_prefixed_tables(db):
    tables = await db.table_names()
    assert "guild_settings" in tables
    for spec in GAMES:
        assert f"{spec.key}_games" in tables
    assert "games" not in tables  # no game may use unprefixed tables


async def test_schema_is_idempotent(db):
    from gaminator.games import prepare_database

    await prepare_database(db)
    await prepare_database(db)


async def test_guild_settings(db):
    settings = GuildSettingsRepo(db)
    assert await settings.get_admin_role(1) is None
    assert await settings.get_announce_channel(1) is None
    await settings.set_admin_role(1, 555)
    await settings.set_announce_channel(1, 777)
    assert await settings.get_admin_role(1) == 555
    assert await settings.get_announce_channel(1) == 777
    await settings.set_admin_role(1, None)
    assert await settings.get_admin_role(1) is None
    assert await settings.get_announce_channel(1) == 777  # untouched


async def test_games_are_independent_per_guild(repo, db):
    """Assassin and Imposter can each run once per server, at the same time."""
    imposter = ImposterRepo(db)
    a = await repo.create_game(1, "Assassin", 99)
    i = await imposter.create_game(1, "Imposter", 99)
    assert (await repo.get_current_game(1))["id"] == a["id"]
    assert (await imposter.get_current_game(1))["id"] == i["id"]
    # a second server has its own games
    assert await repo.get_current_game(2) is None
    await repo.create_game(2, "Other", 99)
    assert (await repo.get_current_game(1))["id"] == a["id"]


async def test_one_unfinished_game_per_guild_per_game(repo, db):
    import aiosqlite

    await repo.create_game(1, "First", 99)
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.create_game(1, "Second", 99)
    imposter = ImposterRepo(db)
    g = await imposter.create_game(1, "First", 99)
    with pytest.raises(aiosqlite.IntegrityError):
        await imposter.create_game(1, "Second", 99)
    await imposter.finish_game(g["id"])
    assert await imposter.get_current_game(1) is None
    await imposter.create_game(1, "Second", 99)


def test_registry():
    keys = [spec.key for spec in GAMES]
    assert keys == ["assassin", "imposter"]
    assert len(set(keys)) == len(keys)
    assert get_spec("assassin").package == "gaminator.games.assassin"
    with pytest.raises(KeyError):
        get_spec("nope")


def test_signup_text():
    from gaminator.core.util import signup_text

    assert signup_text(1, 3) == "1 of at least 3 players signed up, 2 more needed to start."
    assert signup_text(3, 3) == "3 players signed up. ✅ Enough to start!"
    assert signup_text(5, 3).startswith("5 players signed up. ✅")

import pytest

from gaminator.core.db import Database
from gaminator.games import prepare_database
from gaminator.games.assassin.repo import AssassinRepo


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.connect()
    await prepare_database(database)
    yield database
    await database.close()


@pytest.fixture
async def repo(db):
    return AssassinRepo(db)


@pytest.fixture
async def game(repo):
    return await repo.create_game(guild_id=1, name="Test", created_by=99)


@pytest.fixture
async def started(repo, game):
    """A four-player game with chain A->B->C->D->A and words w1..w4. Returns players by user id."""
    players = {uid: await repo.add_player(game["id"], uid) for uid in (11, 12, 13, 14)}
    ids = [players[u]["id"] for u in (11, 12, 13, 14)]
    assignments = [(ids[i], ids[(i + 1) % 4], f"w{i + 1}", f"b{i + 1}") for i in range(4)]
    await repo.start_game(game["id"], assignments)
    return {uid: await repo.get_player(game["id"], uid) for uid in players}

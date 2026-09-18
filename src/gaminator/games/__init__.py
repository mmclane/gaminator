"""Registry of the games this bot hosts. Add a game by importing its SPEC and listing it here."""

from __future__ import annotations

from ..core.db import Database
from ..core.registry import GameSpec
from .assassin import SPEC as ASSASSIN
from .imposter import SPEC as IMPOSTER

GAMES: tuple[GameSpec, ...] = (ASSASSIN, IMPOSTER)


def get_spec(key: str) -> GameSpec:
    for spec in GAMES:
        if spec.key == key:
            return spec
    raise KeyError(key)


async def prepare_database(db: Database) -> None:
    """Run every game's migrations, then apply its schema. Idempotent, so it runs on every start."""
    for spec in GAMES:
        if spec.migrate is not None:
            await spec.migrate(db)
        await db.apply_schema(spec.package)

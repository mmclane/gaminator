"""Assassin: targets, poison words, quick-draw reactions, traps, reports, bounties, inactivity."""

from __future__ import annotations

import logging

from ...config import Settings
from ...core.db import Database
from ...core.registry import GameSpec
from .context import KEY, AssassinContext
from .repo import AssassinRepo
from .services.words import load_words

log = logging.getLogger(__name__)


def make_context(db: Database, settings: Settings) -> AssassinContext:
    words = load_words(settings.poison_words_file)
    log.info("assassin: loaded %d poison words", len(words))
    return AssassinContext(repo=AssassinRepo(db), words=words)


async def migrate(db: Database) -> None:
    """Fold the old poison/trap reactions into one kill emoji; add per-game channel columns."""
    if "assassin_games" not in await db.table_names():
        return
    async with db.conn.execute("PRAGMA table_info(assassin_games)") as cur:
        columns = {row["name"] for row in await cur.fetchall()}
    if "poison_emoji" in columns:
        log.info("assassin: merging poison/trap emoji into the single kill emoji")
        await db.conn.executescript(
            "UPDATE assassin_games SET kill_emoji = poison_emoji;"
            "ALTER TABLE assassin_games DROP COLUMN poison_emoji;"
            "ALTER TABLE assassin_games DROP COLUMN trap_emoji;"
        )
    for column in ("announce_channel_id", "mod_channel_id"):
        if column not in columns:
            log.info("assassin: adding %s column", column)
            await db.conn.executescript(f"ALTER TABLE assassin_games ADD COLUMN {column} INTEGER;")


SPEC = GameSpec(
    key=KEY,
    title="Assassin",
    extensions=(
        "gaminator.games.assassin.commands",
        "gaminator.games.assassin.events",
    ),
    make_context=make_context,
    migrate=migrate,
)

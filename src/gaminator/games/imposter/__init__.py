"""Imposter: everyone gets the same secret prompt except the imposters, who must bluff along."""

from __future__ import annotations

import logging

from ...config import Settings
from ...core.db import Database
from ...core.registry import GameSpec
from .context import KEY, ImposterContext
from .repo import DEFAULT_MAX_ROUNDS, ImposterRepo
from .services.prompts import load_prompts

log = logging.getLogger(__name__)


def make_context(db: Database, settings: Settings) -> ImposterContext:
    prompts = load_prompts(settings.imposter_prompts_file)
    log.info("imposter: loaded %d prompts", len(prompts))
    return ImposterContext(repo=ImposterRepo(db), prompts=prompts)


async def migrate(db: Database) -> None:
    """Upgrade ``imposter_games`` from earlier builds.

    The first build created an empty placeholder table; the next one had an
    ``eliminate_innocent`` setting and an unlimited-rounds default that the rules no longer have.
    """
    if "imposter_games" not in await db.table_names():
        return
    async with db.conn.execute("PRAGMA table_info(imposter_games)") as cur:
        columns = {row["name"] for row in await cur.fetchall()}
    if "phase" not in columns:
        log.info("imposter: dropping placeholder imposter_games table")
        await db.conn.executescript("DROP TABLE imposter_games")
        return
    if "eliminate_innocent" in columns:
        log.info("imposter: removing the eliminate_innocent setting")
        await db.conn.executescript(
            "ALTER TABLE imposter_games DROP COLUMN eliminate_innocent;"
            f"UPDATE imposter_games SET max_rounds = {DEFAULT_MAX_ROUNDS} WHERE max_rounds = 0;"
        )
    if "imposter_hint" not in columns:
        log.info("imposter: adding the imposter_hint setting")
        await db.conn.executescript(
            "ALTER TABLE imposter_games ADD COLUMN imposter_hint INTEGER NOT NULL DEFAULT 1;"
        )


SPEC = GameSpec(
    key=KEY,
    title="Imposter",
    extensions=("gaminator.games.imposter.commands",),
    make_context=make_context,
    migrate=migrate,
)

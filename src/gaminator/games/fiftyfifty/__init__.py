"""Fifty-Fifty: write a would-you-rather the group splits evenly, then guess how the votes fall."""

from __future__ import annotations

import logging

from ...config import Settings
from ...core.db import Database
from ...core.registry import GameSpec
from .context import KEY, FiftyFiftyContext
from .repo import FiftyFiftyRepo
from .services.prompts import load_topics

log = logging.getLogger(__name__)


def make_context(db: Database, settings: Settings) -> FiftyFiftyContext:
    topics = load_topics(settings.fiftyfifty_prompts_file)
    log.info("fiftyfifty: loaded %d topics", len(topics))
    return FiftyFiftyContext(repo=FiftyFiftyRepo(db), topics=topics)


async def migrate(db: Database) -> None:
    """Add the topic column to a questions table from before players wrote both options."""
    if "fiftyfifty_questions" not in await db.table_names():
        return
    async with db.conn.execute("PRAGMA table_info(fiftyfifty_questions)") as cur:
        columns = {row["name"] for row in await cur.fetchall()}
    if "topic" not in columns:
        log.info("fiftyfifty: adding topic column")
        await db.conn.executescript("ALTER TABLE fiftyfifty_questions ADD COLUMN topic TEXT;")


SPEC = GameSpec(
    key=KEY,
    title="Fifty-Fifty",
    emoji="⚖️",
    summary=(
        "Write a would-you-rather the group splits as close to 50/50 as you can, then guess "
        "how each vote falls."
    ),
    start_hint="Anyone runs `/gaminator fiftyfifty create`.",
    players="3 to 12; about 5 to 15 minutes a round",
    extensions=(
        "gaminator.games.fiftyfifty.commands",
        "gaminator.games.fiftyfifty.events",
    ),
    make_context=make_context,
    migrate=migrate,
    listed=True,
)

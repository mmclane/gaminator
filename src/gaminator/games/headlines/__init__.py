"""Headlines: answer a harmless question, and another player writes the headline that ruins it."""

from __future__ import annotations

import logging

from ...config import Settings
from ...core.db import Database
from ...core.registry import GameSpec
from .context import KEY, HeadlinesContext
from .repo import HeadlinesRepo
from .services.questions import load_questions

log = logging.getLogger(__name__)


def make_context(db: Database, settings: Settings) -> HeadlinesContext:
    questions = load_questions(settings.headlines_questions_file)
    log.info("headlines: loaded %d questions", len(questions))
    return HeadlinesContext(repo=HeadlinesRepo(db), questions=questions)


SPEC = GameSpec(
    key=KEY,
    title="Headlines",
    emoji="📰",
    summary=(
        "Answer a harmless question; another player, who sees only your answer, writes the "
        "news headline that makes it as funny as possible. Vote for the best pairing."
    ),
    start_hint="Anyone runs `/gaminator headlines create`.",
    players="3 to 12; about 5 minutes a round",
    extensions=(
        "gaminator.games.headlines.commands",
        "gaminator.games.headlines.events",
    ),
    make_context=make_context,
    listed=True,
)

"""Most Likely To: vote on who in the group is most likely to do something."""

from __future__ import annotations

import logging

from ...config import Settings
from ...core.db import Database
from ...core.registry import GameSpec
from .context import KEY, MostLikelyContext
from .repo import MostLikelyRepo
from .services.prompts import load_prompts

log = logging.getLogger(__name__)


def make_context(db: Database, settings: Settings) -> MostLikelyContext:
    prompts = load_prompts(settings.mostlikely_prompts_file)
    log.info("mostlikely: loaded %d prompts", len(prompts))
    return MostLikelyContext(repo=MostLikelyRepo(db), prompts=prompts)


SPEC = GameSpec(
    key=KEY,
    title="Most Likely To",
    emoji="🤔",
    summary="Vote on who in the group is most likely to do something, then see the tally.",
    start_hint="Anyone runs `/gaminator mostlikely create`.",
    players="3 to 25; about 10 prompts",
    extensions=(
        "gaminator.games.mostlikely.commands",
        "gaminator.games.mostlikely.events",
    ),
    make_context=make_context,
)

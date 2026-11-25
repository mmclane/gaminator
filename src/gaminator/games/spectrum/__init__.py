"""Spectrum: one player gets a secret spot on a scale and gives a clue; the rest guess the number."""

from __future__ import annotations

import logging

from ...config import Settings
from ...core.db import Database
from ...core.registry import GameSpec
from .context import KEY, SpectrumContext
from .repo import SpectrumRepo
from .services.scales import load_scales

log = logging.getLogger(__name__)


def make_context(db: Database, settings: Settings) -> SpectrumContext:
    scales = load_scales(settings.spectrum_scales_file)
    log.info("spectrum: loaded %d scales", len(scales))
    return SpectrumContext(repo=SpectrumRepo(db), scales=scales)


SPEC = GameSpec(
    key=KEY,
    title="Spectrum",
    emoji="🌈",
    summary=(
        "One player gets a secret spot on a scale like Tiny hiccup ↔ Total disaster and gives "
        "a clue; everyone else guesses the number."
    ),
    start_hint="Anyone runs `/gaminator spectrum create`.",
    players="3 to 12; about 3 minutes a turn",
    extensions=(
        "gaminator.games.spectrum.commands",
        "gaminator.games.spectrum.events",
    ),
    make_context=make_context,
    listed=True,
)

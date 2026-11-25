"""The vote menu. Its custom_id carries the round id, so it survives a restart."""

from __future__ import annotations

import logging
import re

import discord

from ...core.messaging import NETWORK_ERRORS
from ...core.util import GameError, describe_error, respond
from .services import game as flow

log = logging.getLogger(__name__)
VOTE_TEMPLATE = re.compile(r"headlines:vote:(?P<round>[0-9]+)")
VOTE_PLACEHOLDER = "Pick the best headline"


def vote_custom_id(round_id: int) -> str:
    return f"headlines:vote:{round_id}"


class VoteSelect(discord.ui.DynamicItem[discord.ui.Select], template=VOTE_TEMPLATE):
    def __init__(self, round_id: int, options: list[discord.SelectOption]):
        super().__init__(
            discord.ui.Select(
                custom_id=vote_custom_id(round_id),
                placeholder=VOTE_PLACEHOLDER,
                min_values=1,
                max_values=1,
                options=options,
            )
        )
        self.round_id = round_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Select, match: re.Match[str], /
    ):
        return cls(int(match["round"]), list(item.options))

    async def callback(self, interaction: discord.Interaction) -> None:
        values = self.item.values
        await handle_vote(interaction, self.round_id, values[0] if values else "")


def vote_view(round_id: int, pairings) -> discord.ui.View:
    """The vote menu with one entry per headline, in the order they were read out.

    ``pairings`` holds the repo's vote_options rows.
    """
    options = [
        flow.vote_option(p["id"], p["position"], p["headline"], p["answer"]) for p in pairings
    ]
    view = discord.ui.View(timeout=None)
    view.add_item(VoteSelect(round_id, options))
    return view


async def handle_vote(interaction: discord.Interaction, round_id: int, value: str) -> None:
    """Record a pick from the vote menu and answer privately.

    The click is deferred before anything is read, and the voter is answered before the vote is
    checked for closing.
    """
    try:
        await interaction.response.defer(ephemeral=True)
    except NETWORK_ERRORS as e:
        log.warning("headlines: could not defer a vote: %s", e)
    bot = interaction.client
    try:
        text = await flow.submit_vote(bot, round_id, interaction.user.id, value)
    except GameError as e:
        text = describe_error(e) or "That didn't work."
    except Exception:
        log.exception("headlines: vote failed for round %s", round_id)
        text = "Something went wrong; the logs have details."
    try:
        await respond(interaction, text)
    except NETWORK_ERRORS as e:
        log.warning("headlines: could not answer a vote: %s", e)
    try:
        await flow.close_step_if_done(bot, round_id)
    except Exception:
        log.exception("headlines: closing round %s after a vote failed", round_id)

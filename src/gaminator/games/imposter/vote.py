"""The vote dropdown. Its custom_id carries the round id, so it keeps working after a restart."""

from __future__ import annotations

import logging
import re

import discord

from ...core.messaging import display_name
from ...core.util import respond
from .context import context
from .services import game as flow

log = logging.getLogger(__name__)
TEMPLATE = re.compile(r"imposter:vote:(?P<round>[0-9]+)")


class VoteSelect(discord.ui.DynamicItem[discord.ui.Select], template=TEMPLATE):
    def __init__(self, round_id: int, options: list[discord.SelectOption]):
        super().__init__(
            discord.ui.Select(
                custom_id=f"imposter:vote:{round_id}",
                placeholder="Who is the imposter?",
                options=options,
                min_values=1,
                max_values=1,
            )
        )
        self.round_id = round_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Select, match: re.Match[str], /
    ):
        return cls(int(match["round"]), item.options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_vote(interaction, self.round_id, self.item.values[0])


def vote_view(round_id: int, options: list[discord.SelectOption]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(VoteSelect(round_id, options))
    return view


async def handle_vote(interaction: discord.Interaction, round_id: int, value: str) -> None:
    bot = interaction.client
    repo = context(bot).repo
    rnd = await repo.get_round(round_id)
    game = await repo.get_game(rnd["game_id"]) if rnd else None
    if (
        rnd is None
        or game is None
        or game["status"] != "active"
        or game["phase"] != "voting"
        or rnd["closed_at"]
        or rnd["number"] != game["round"]
    ):
        await respond(interaction, "This vote is closed.")
        return
    voter = await repo.get_player(game["id"], interaction.user.id)
    if voter is None or not voter["alive"]:
        await respond(interaction, "Only living players in this game can vote.")
        return
    target_id = int(value) or None
    if target_id is not None:
        target = await repo.get_player_by_id(target_id)
        if target is None or target["game_id"] != game["id"] or not target["alive"]:
            await respond(interaction, "That player isn't in the game any more.")
            return
        if target["id"] == voter["id"]:
            await respond(interaction, "You can't vote for yourself.")
            return
        name = await display_name(bot, game["guild_id"], target["user_id"])
        choice = f"**{name}**"
    else:
        choice = "**no accusation**"
    await repo.cast_vote(rnd["id"], voter["id"], target_id)
    await respond(interaction, f"You voted for {choice}. You can change it until the vote closes.")
    await flow.refresh_vote_message(bot, game, rnd, interaction.message)
    if await flow.everyone_voted(bot, game, rnd):
        await flow.close_vote(bot, game, rnd)

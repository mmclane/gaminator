"""The vote dropdown. Its custom_id carries the round id, so it keeps working after a restart."""

from __future__ import annotations

import re

import discord

from ...core.messaging import display_name
from ...core.util import respond
from .context import context
from .services import game as flow
from .services.rules import CLOSED, vote_problem

TEMPLATE = re.compile(r"mostlikely:vote:(?P<round>[0-9]+)")


class VoteSelect(discord.ui.DynamicItem[discord.ui.Select], template=TEMPLATE):
    def __init__(self, round_id: int, options: list[discord.SelectOption]):
        super().__init__(
            discord.ui.Select(
                custom_id=f"mostlikely:vote:{round_id}",
                placeholder="Who is most likely?",
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
    voter = await repo.get_player(game["id"], interaction.user.id) if game else None
    target_id = int(value)
    voter_on_roster = bool(rnd and voter) and await repo.on_roster(rnd["id"], voter["id"])
    target_on_roster = bool(rnd) and await repo.on_roster(rnd["id"], target_id)
    problem = vote_problem(game, rnd, voter, voter_on_roster, target_on_roster)
    if problem:
        await respond(interaction, problem)
        return
    if not await repo.cast_vote(rnd["id"], voter["id"], target_id):
        await respond(interaction, CLOSED)
        return
    target = await repo.get_player_by_id(target_id)
    name = await display_name(bot, game["guild_id"], target["user_id"])
    await respond(
        interaction, f"You voted for **{name}**. You can change it until the prompt closes."
    )
    await flow.refresh_vote_message(bot, game, rnd, interaction.message)
    if await flow.everyone_voted(bot, rnd):
        await flow.close_round(bot, game, rnd)

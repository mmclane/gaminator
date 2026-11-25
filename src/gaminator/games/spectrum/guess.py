"""The Guess button on a clue. Its custom_id carries the turn id, so it survives a restart."""

from __future__ import annotations

import re

import discord

from ...core.util import GameError, describe_error, respond
from .context import context
from .services.rules import guess_problem
from .turn import guess_modal, open_modal

TEMPLATE = re.compile(r"spectrum:guess:(?P<turn>[0-9]+)")


def guess_custom_id(turn_id: int) -> str:
    return f"spectrum:guess:{turn_id}"


class GuessButton(discord.ui.DynamicItem[discord.ui.Button], template=TEMPLATE):
    def __init__(self, turn_id: int):
        super().__init__(
            discord.ui.Button(
                custom_id=guess_custom_id(turn_id),
                label="Guess",
                emoji="📍",
                style=discord.ButtonStyle.primary,
            )
        )
        self.turn_id = turn_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ):
        return cls(int(match["turn"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_guess_click(interaction, self.turn_id)


def guess_view(turn_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(GuessButton(turn_id))
    return view


async def handle_guess_click(interaction: discord.Interaction, turn_id: int) -> None:
    """Open the guess form as the first response, filled in with the player's guess so far."""
    try:
        ctx = await context(interaction.client).repo.turn_context(turn_id, interaction.user.id)
        problem = guess_problem(ctx)
        if problem:
            await respond(interaction, problem)
            return
        await open_modal(interaction, guess_modal(ctx, ctx["my_guess"]), turn_id)
    except GameError as e:
        await respond(interaction, describe_error(e) or "That didn't work.")

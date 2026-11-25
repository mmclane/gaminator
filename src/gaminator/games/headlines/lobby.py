"""The lobby card buttons. Their custom_ids carry the game id, so they survive a restart."""

from __future__ import annotations

import re

import discord

from ...core.util import GameError, NotGameAdmin, describe_error, respond
from .context import context
from .services import game as flow
from .services.rules import DEFAULT_ROUNDS, GAME_OVER

TEMPLATE = re.compile(r"headlines:(?P<action>join|leave|rules|start):(?P<game>[0-9]+)")
BUTTONS = {
    "join": ("Join", discord.ButtonStyle.success),
    "leave": ("Leave", discord.ButtonStyle.secondary),
    "rules": ("Rules", discord.ButtonStyle.secondary),
    "start": ("Start", discord.ButtonStyle.primary),
}


def lobby_custom_id(action: str, game_id: int) -> str:
    return f"headlines:{action}:{game_id}"


class LobbyButton(discord.ui.DynamicItem[discord.ui.Button], template=TEMPLATE):
    def __init__(self, action: str, game_id: int):
        label, style = BUTTONS[action]
        super().__init__(
            discord.ui.Button(custom_id=lobby_custom_id(action, game_id), label=label, style=style)
        )
        self.action = action
        self.game_id = game_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ):
        return cls(match["action"], int(match["game"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_lobby_click(interaction, self.action, self.game_id)


def lobby_view(game) -> discord.ui.View | None:
    """Buttons for the card: all four while open, no Start once running, none when over."""
    if game["status"] == "finished":
        return None
    actions = ["join", "leave", "rules"]
    if game["status"] == "open":
        actions.append("start")
    view = discord.ui.View(timeout=None)
    for action in actions:
        view.add_item(LobbyButton(action, game["id"]))
    return view


async def handle_lobby_click(interaction: discord.Interaction, action: str, game_id: int) -> None:
    bot = interaction.client
    game = await context(bot).repo.get_game(game_id)
    if game is None or game["status"] == "finished":
        await respond(interaction, GAME_OVER)
        return
    try:
        if action == "rules":
            text = flow.rules_text(
                game,
                flow.answer_seconds(bot),
                flow.write_seconds(bot),
                flow.vote_seconds(bot),
                flow.required_players(bot),
            )
            await respond(interaction, text)
            return
        if action == "start":
            try:
                await flow.require_host_or_admin(interaction, game)
            except NotGameAdmin:
                raise GameError("Only the host or a game admin can start.") from None
        await interaction.response.defer(ephemeral=True)
        if action == "join":
            text = await flow.join_game(bot, game, interaction.user.id)
        elif action == "leave":
            text = await flow.leave_game(bot, game, interaction.user.id)
        else:
            text = await flow.begin_game(bot, game, DEFAULT_ROUNDS)
        await respond(interaction, text)
    except GameError as e:
        await respond(interaction, describe_error(e) or "That didn't work.")

"""Answering by DM: the Answer by DM button, and DM replies read as a player's two options.

The first DM of a round (or a press of the button) sends the player their topic and how to
reply, and marks it in the database. Every DM after that is read as their two options. What a
player types is never logged.
"""

from __future__ import annotations

import logging
import re

import discord

from ...core.messaging import NETWORK_ERRORS, NO_MENTIONS, dm
from ...core.util import GameError, describe_error, now, respond
from .context import context
from .services import game as flow
from .services.rules import dm_action, write_problem
from .write import WriteButton

log = logging.getLogger(__name__)
TEMPLATE = re.compile(r"fiftyfifty:dm:(?P<round>[0-9]+)")


def dm_custom_id(round_id: int) -> str:
    return f"fiftyfifty:dm:{round_id}"


class DMButton(discord.ui.DynamicItem[discord.ui.Button], template=TEMPLATE):
    def __init__(self, round_id: int):
        super().__init__(
            discord.ui.Button(
                custom_id=dm_custom_id(round_id),
                label="Answer by DM",
                emoji="✉️",
                style=discord.ButtonStyle.secondary,
            )
        )
        self.round_id = round_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ):
        return cls(int(match["round"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_dm_click(interaction, self.round_id)


def round_view(round_id: int) -> discord.ui.View:
    """The buttons on a round message: Write, then Answer by DM."""
    view = discord.ui.View(timeout=None)
    view.add_item(WriteButton(round_id))
    view.add_item(DMButton(round_id))
    return view


async def handle_dm_click(interaction: discord.Interaction, round_id: int) -> None:
    """DM the player their topic. Their next DM to the bot is then read as their two options."""
    bot = interaction.client
    repo = context(bot).repo
    user_id = interaction.user.id
    try:
        ctx = await repo.write_context(round_id, user_id)
        problem = write_problem(ctx)
        if problem:
            await respond(interaction, problem)
            return
        await interaction.response.defer(ephemeral=True)
        stamped = await repo.mark_dm_prompted(ctx["question_id"])
        text = flow.dm_question_text(ctx["topic"], flow.written_so_far(ctx))
        if await dm(bot, user_id, text):
            log.info("fiftyfifty: sent question %s to user %s by DM", ctx["question_id"], user_id)
            await respond(interaction, flow.dm_sent_text())
            return
        if stamped:
            await repo.clear_dm_prompted(ctx["question_id"])
        await respond(interaction, flow.dm_failed_text())
    except GameError as e:
        await respond(interaction, describe_error(e) or "That didn't work.")


async def handle_direct_message(bot, message) -> bool:
    """Act on a DM from a player with a question to write. False when the DM isn't for this game.

    One query finds the sender's questions. Senders with none get no reply at all.
    """
    if message.guild is not None or message.author.bot or not message.content.strip():
        return False
    repo = context(bot).repo
    user_id = message.author.id
    rows = await repo.dm_candidates(user_id)
    action, row = dm_action(rows, now())
    if row is None:
        return False
    place = None
    if len(rows) > 1:
        guild = bot.get_guild(row["guild_id"])
        place = flow.place_text(row["game_name"], guild.name if guild is not None else None)
    stamped = False
    try:
        if action == "closed":
            text = flow.dm_closed_text()
        elif await repo.mark_dm_prompted(row["question_id"]):
            stamped = True
            text = flow.dm_question_text(row["topic"], flow.written_so_far(row), place)
        else:
            saved = await flow.submit_dm(bot, row["round_id"], user_id, message.content)
            text = flow.dm_saved_text(saved, place)
    except GameError as e:
        text = describe_error(e) or "That didn't work."
    except Exception:
        log.exception("fiftyfifty: DM question failed for question %s", row["question_id"])
        text = "Something went wrong; the logs have details."
    try:
        await message.channel.send(text, allowed_mentions=NO_MENTIONS)
    except NETWORK_ERRORS as e:
        log.info(
            "fiftyfifty: could not reply to a DM from user %s about question %s: %s",
            user_id,
            row["question_id"],
            e,
        )
        if stamped:
            await repo.clear_dm_prompted(row["question_id"])
    return True

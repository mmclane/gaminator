"""A fake bot, DMs and interactions for driving the Fifty-Fifty flow without Discord."""

from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from mostlikely_fakes import (
    CHANNEL_ID,
    FakeChannel,
    FakeMessage,
    card_names,
    interaction_for,
    reply_of,
)

from gaminator.core.repo import GuildSettingsRepo
from gaminator.games.fiftyfifty.context import FiftyFiftyContext
from gaminator.games.fiftyfifty.repo import FiftyFiftyRepo
from gaminator.games.fiftyfifty.services import game as flow

__all__ = [
    "CHANNEL_ID",
    "DECK",
    "FakeChannel",
    "FakeDMUser",
    "FakeMessage",
    "add_card",
    "card_names",
    "click_for",
    "dm_message",
    "interaction_for",
    "make_bot",
    "reply_of",
    "submit_for",
]

DECK = [f"topic {n}" for n in range(80)]


def forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "closed DMs")


class FakeDMUser:
    """A user whose DMs land in ``bot.dms``, or are refused when listed in ``bot.closed_dms``."""

    def __init__(self, bot, user_id, is_bot=False):
        self._bot = bot
        self.id = user_id
        self.bot = is_bot
        self.display_name = f"U{user_id}"

    async def send(self, content=None, **kwargs):
        if self.id in self._bot.closed_dms:
            raise forbidden()
        self._bot.dms[self.id].append(content)


class FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id
        self.name = f"Server {guild_id}"

    def get_member(self, user_id):
        return None

    async def fetch_member(self, user_id):
        return None


def make_bot(db, min_players=None, write_seconds=120, vote_seconds=60):
    """A fake bot. Building one also turns off the pause before a reveal."""
    flow.REVEAL_PAUSE = 0
    channel = FakeChannel()
    ctx = FiftyFiftyContext(repo=FiftyFiftyRepo(db), topics=list(DECK))
    bot = SimpleNamespace(
        games={"fiftyfifty": ctx},
        settings=SimpleNamespace(
            min_players=min_players,
            fiftyfifty_write_seconds=write_seconds,
            fiftyfifty_vote_seconds=vote_seconds,
        ),
        guild_settings=GuildSettingsRepo(db),
        get_guild=FakeGuild,
        get_channel=lambda channel_id: channel if channel_id == channel.id else None,
        channel=channel,
        repo=ctx.repo,
        dms=defaultdict(list),
        closed_dms=set(),
    )
    bot.get_user = lambda user_id: FakeDMUser(bot, user_id)
    return bot


def dm_message(bot, user_id, content, guild=None, is_bot=False):
    """An incoming message. With no ``guild`` it is a DM, and replies land in ``bot.dms``."""
    author = FakeDMUser(bot, user_id, is_bot)
    return SimpleNamespace(
        author=author, guild=guild, content=content, channel=SimpleNamespace(send=author.send)
    )


async def add_card(bot, game):
    """Post a stand-in lobby card and record it on the game, as ``create`` does."""
    card = await bot.channel.send("card")
    await bot.repo.set_lobby_message(game["id"], card.id)
    return card


def click_for(bot, user_id, **kw):
    """A button click, which can be answered with a pop-up form."""
    interaction = interaction_for(bot, user_id, **kw)
    interaction.response.send_modal = AsyncMock()
    return interaction


def submit_for(bot, user_id, custom_id, option_a, option_b, layout="label"):
    """A form submit as Discord sends it: each text input inside a label or an action row.

    An option given as None is left out of the payload.
    """
    inputs = [
        {"type": 4, "id": 10 + n, "custom_id": input_id, "value": value}
        for n, (input_id, value) in enumerate((("a", option_a), ("b", option_b)))
        if value is not None
    ]
    if layout == "row":
        components = [{"type": 1, "id": n, "components": [item]} for n, item in enumerate(inputs)]
    else:
        components = [{"type": 10, "id": 1}] + [
            {"type": 18, "id": 2 + n, "component": item} for n, item in enumerate(inputs)
        ]
    interaction = interaction_for(bot, user_id)
    interaction.type = discord.InteractionType.modal_submit
    interaction.data = {"custom_id": custom_id, "components": components}
    return interaction

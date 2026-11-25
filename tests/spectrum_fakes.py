"""A fake bot, channel and interactions for driving the Spectrum flow without Discord."""

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
from gaminator.games.spectrum.context import SpectrumContext
from gaminator.games.spectrum.repo import SpectrumRepo
from gaminator.games.spectrum.services import game as flow
from gaminator.games.spectrum.services.scales import Scale

__all__ = [
    "CHANNEL_ID",
    "DECK",
    "FakeChannel",
    "FakeMessage",
    "RecordingChannel",
    "add_card",
    "card_names",
    "click_for",
    "interaction_for",
    "make_bot",
    "play_to_guessing",
    "reply_of",
    "submit_for",
]

DECK = [Scale(f"left {n}", f"right {n}") for n in range(80)]


class RecordingChannel(FakeChannel):
    """A channel whose messages keep the text and keyword arguments they were sent with."""

    async def send(self, content=None, **kwargs):
        message = await super().send(content, **kwargs)
        message.sent = content
        message.kwargs = kwargs
        return message


def make_bot(db, min_players=None, clue_seconds=120, guess_seconds=120):
    """A fake bot. Building one also turns off the pause before a reveal."""
    flow.REVEAL_PAUSE = 0
    channel = RecordingChannel()
    ctx = SpectrumContext(repo=SpectrumRepo(db), scales=list(DECK))
    return SimpleNamespace(
        games={"spectrum": ctx},
        settings=SimpleNamespace(
            min_players=min_players,
            spectrum_clue_seconds=clue_seconds,
            spectrum_guess_seconds=guess_seconds,
        ),
        guild_settings=GuildSettingsRepo(db),
        get_guild=lambda guild_id: None,
        get_user=lambda user_id: SimpleNamespace(display_name=f"U{user_id}"),
        get_channel=lambda channel_id: channel if channel_id == channel.id else None,
        channel=channel,
        repo=ctx.repo,
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


def submit_for(bot, user_id, custom_id, input_id, value, layout="label"):
    """A form submit as Discord sends it: the text input inside a label or an action row.

    A value given as None is left out of the payload.
    """
    inputs = [{"type": 4, "id": 10, "custom_id": input_id, "value": value}]
    if value is None:
        inputs = []
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


async def play_to_guessing(bot, game, clue="rain at a picnic"):
    """Have the open turn's clue giver submit a clue. Returns the turn, now open for guesses."""
    rnd = await bot.repo.get_current_round(game["id"])
    turn = await bot.repo.current_turn(rnd["id"])
    reply = await flow.submit_clue(bot, turn["id"], turn["giver_user_id"], clue)
    assert reply == "Your clue is up.", reply
    return await bot.repo.get_turn(turn["id"])

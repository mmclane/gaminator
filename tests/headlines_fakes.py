"""A fake bot, channel and interactions for driving the Headlines flow without Discord."""

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
from gaminator.games.headlines.context import HeadlinesContext
from gaminator.games.headlines.repo import HeadlinesRepo
from gaminator.games.headlines.services import game as flow
from gaminator.games.headlines.vote import handle_ballot_click

__all__ = [
    "CHANNEL_ID",
    "QUESTIONS",
    "FakeChannel",
    "FakeMessage",
    "RecordingChannel",
    "add_card",
    "card_names",
    "click_for",
    "interaction_for",
    "make_bot",
    "pairings_of",
    "play_to_voting",
    "play_to_writing",
    "press_vote",
    "reply_of",
    "select_for",
    "submit_for",
    "view_of",
]

QUESTIONS = [f"question {n}?" for n in range(100)]


class RecordingChannel(FakeChannel):
    """A channel whose messages keep the text and keyword arguments they were sent with.

    ``on_send``, when set, is awaited with each message after it is recorded and before the
    sender gets it back, which is where a test can act while a post is in flight.
    """

    def __init__(self, channel_id=CHANNEL_ID):
        super().__init__(channel_id)
        self.on_send = None

    async def send(self, content=None, **kwargs):
        message = await super().send(content, **kwargs)
        message.sent = content
        message.kwargs = kwargs
        if self.on_send is not None:
            await self.on_send(message)
        return message


def make_bot(db, min_players=None, answer_seconds=90, write_seconds=120, vote_seconds=60):
    """A fake bot. Building one also turns off the pause between revealed headlines."""
    flow.REVEAL_PAUSE = 0
    channel = RecordingChannel()
    ctx = HeadlinesContext(repo=HeadlinesRepo(db), questions=list(QUESTIONS))
    return SimpleNamespace(
        games={"headlines": ctx},
        settings=SimpleNamespace(
            min_players=min_players,
            headlines_answer_seconds=answer_seconds,
            headlines_write_seconds=write_seconds,
            headlines_vote_seconds=vote_seconds,
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


def view_of(interaction):
    """The view sent with the last reply, or None when it was text only."""
    sent = interaction.followup.send.await_args or interaction.response.send_message.await_args
    return sent.kwargs.get("view")


async def press_vote(bot, user_id, round_id):
    """Press the Vote button of a round. Returns the interaction, answered privately."""
    interaction = interaction_for(bot, user_id)
    await handle_ballot_click(interaction, round_id)
    return interaction


def select_for(bot, user_id, round_id, value):
    """A pick from a player's private vote menu, as ``handle_vote`` gets it."""
    interaction = interaction_for(bot, user_id)
    interaction.type = discord.InteractionType.component
    interaction.data = {"custom_id": f"headlines:vote:{round_id}", "values": [value]}
    return interaction


async def pairings_of(bot, rnd):
    """The round's pairings by writer user id, each with the quoted player's user id."""
    rows = await bot.repo._all(
        "SELECT w.*, wp.user_id AS writer_user_id, wp.left_at AS writer_left_at, "
        "qp.user_id AS quoted_user_id, a.answer "
        "FROM headlines_pairings w "
        "JOIN headlines_answers a ON a.id = w.answer_id "
        "JOIN headlines_players wp ON wp.id = w.writer_id "
        "JOIN headlines_players qp ON qp.id = a.player_id "
        "WHERE w.round_id = ? ORDER BY w.id",
        (rnd["id"],),
    )
    return {row["writer_user_id"]: row for row in rows}


async def play_to_writing(bot, game, answers=None):
    """Answer the open round and close answering. Returns the round, open for headlines.

    ``answers`` maps user id to text; by default every roster player still in the game
    answers. When that leaves somebody out, answering is closed as its deadline would.
    """
    rnd = await bot.repo.get_current_round(game["id"])
    if answers is None:
        roster = await bot.repo._all(
            "SELECT p.user_id FROM headlines_answers a "
            "JOIN headlines_players p ON p.id = a.player_id "
            "WHERE a.round_id = ? AND p.left_at IS NULL ORDER BY p.id",
            (rnd["id"],),
        )
        answers = {p["user_id"]: f"answer of a{p['user_id']}" for p in roster}
    for user_id, text in answers.items():
        reply = await flow.submit_answer(bot, rnd["id"], user_id, text)
        assert reply.startswith("Saved."), reply
        await flow.close_step_if_done(bot, rnd["id"])
    rnd = await bot.repo.get_round(rnd["id"])
    if not rnd["answer_closed_at"]:
        await flow.close_answering(bot, game, rnd, "deadline")
    return await bot.repo.get_round(rnd["id"])


async def play_to_voting(bot, game, headlines=None):
    """Write the open round's headlines and read them out. Returns the round, open for votes.

    A round still being answered is answered by everyone first. ``headlines`` maps writer user
    id to text; by default every writer still in the game writes theirs. When that leaves a
    quote unwritten, writing is closed as its deadline would.
    """
    rnd = await bot.repo.get_current_round(game["id"])
    if not rnd["answer_closed_at"]:
        rnd = await play_to_writing(bot, game)
    if headlines is None:
        pairs = await pairings_of(bot, rnd)
        writers = [uid for uid, row in pairs.items() if row["writer_left_at"] is None]
        headlines = {uid: f"headline by w{uid}" for uid in writers}
    for user_id, text in headlines.items():
        reply = await flow.submit_headline(bot, rnd["id"], user_id, text)
        assert reply.startswith("Saved."), reply
        await flow.close_step_if_done(bot, rnd["id"])
    rnd = await bot.repo.get_round(rnd["id"])
    if not rnd["write_closed_at"]:
        await flow.close_writing_step(bot, game, rnd, "deadline")
    return await bot.repo.get_round(rnd["id"])

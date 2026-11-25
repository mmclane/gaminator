"""A fake bot, channel and messages for driving the Most Likely To flow without Discord."""

from types import SimpleNamespace

from gaminator.core.repo import GuildSettingsRepo
from gaminator.games.mostlikely.context import MostLikelyContext
from gaminator.games.mostlikely.repo import MostLikelyRepo

CHANNEL_ID = 555
PROMPTS = [f"do thing {n}" for n in range(60)]


class FakeMessage:
    def __init__(self, channel, message_id, content=None, **kwargs):
        self.channel = channel
        self.id = message_id
        self.content = content
        self.view = kwargs.get("view")
        self.embed = kwargs.get("embed")
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        for key in ("content", "view", "embed"):
            if key in kwargs:
                setattr(self, key, kwargs[key])


class FakeChannel:
    def __init__(self, channel_id=CHANNEL_ID):
        self.id = channel_id
        self.messages = []
        self.fail_with = []

    async def send(self, content=None, **kwargs):
        if self.fail_with:
            raise self.fail_with.pop(0)
        message = FakeMessage(self, 1000 + len(self.messages), content, **kwargs)
        self.messages.append(message)
        return message

    async def fetch_message(self, message_id):
        return next(m for m in self.messages if m.id == message_id)

    @property
    def texts(self):
        return [m.content for m in self.messages]

    @property
    def recap(self):
        """The end-of-game message, which the all-time standings follow."""
        return next(t for t in self.texts if "is over" in t)


def make_bot(db, min_players=None, vote_seconds=120):
    channel = FakeChannel()
    ctx = MostLikelyContext(repo=MostLikelyRepo(db), prompts=list(PROMPTS))
    bot = SimpleNamespace(
        games={"mostlikely": ctx},
        settings=SimpleNamespace(min_players=min_players, mostlikely_vote_seconds=vote_seconds),
        guild_settings=GuildSettingsRepo(db),
        get_guild=lambda guild_id: None,
        get_user=lambda user_id: SimpleNamespace(display_name=f"U{user_id}"),
        get_channel=lambda channel_id: channel if channel_id == channel.id else None,
        channel=channel,
        repo=ctx.repo,
    )
    return bot


async def add_card(bot, game):
    """Post a stand-in lobby card and record it on the game, as ``create`` does."""
    card = await bot.channel.send("card")
    await bot.repo.set_lobby_message(game["id"], card.id)
    return card


def card_names(card):
    """The player names on the card's last edit, in the order shown."""
    _, _, roster = card.embed.description.partition("\n\n")
    return [line.split(". ", 1)[1] for line in roster.split("\n") if line]


def interaction_for(bot, user_id, manage_guild=False, message=None):
    from unittest.mock import AsyncMock, MagicMock

    interaction = MagicMock()
    interaction.client = bot
    interaction.user.id = user_id
    interaction.guild_id = 1
    interaction.permissions.manage_guild = manage_guild
    interaction.message = message
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.response.is_done = lambda: interaction.response.defer.await_count > 0
    return interaction


def reply_of(interaction):
    """The text of the last reply, whether it was sent directly or as a followup."""
    sent = interaction.followup.send.await_args or interaction.response.send_message.await_args
    return sent.args[0]

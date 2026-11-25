"""The slash-command tree: every game under its own group, organizer commands under admin."""

from dataclasses import replace
from unittest.mock import MagicMock

import discord
from discord import app_commands

from gaminator.core.commands import games_embed, gaminator, gaminator_admin
from gaminator.core.util import GameError, NoCurrentGame, describe_error
from gaminator.games import GAMES
from gaminator.games.assassin.commands import assassin
from gaminator.games.assassin.commands.group import admin
from gaminator.games.fiftyfifty import commands as fiftyfifty_commands
from gaminator.games.fiftyfifty.commands.group import fiftyfifty
from gaminator.games.headlines import commands as headlines_commands
from gaminator.games.headlines.commands.group import headlines
from gaminator.games.imposter.commands import imposter
from gaminator.games.imposter.commands.group import admin as imposter_admin
from gaminator.games.mostlikely import commands as mostlikely_commands
from gaminator.games.mostlikely.commands.group import mostlikely
from gaminator.games.spectrum import commands as spectrum_commands
from gaminator.games.spectrum.commands.group import spectrum


def names(group: app_commands.Group) -> set[str]:
    return {c.name for c in group.commands}


def test_assassin_player_commands():
    assert names(assassin) == {"join", "leave", "status", "report", "rules", "players", "resend"}
    assert assassin.default_permissions is None  # players must see the group
    assert assassin.guild_only


def test_assassin_admin_subgroup():
    assert admin.parent is None and admin.name == "assassin-admin"
    assert admin.default_permissions == discord.Permissions(manage_guild=True)
    assert names(admin) == {
        "create",
        "start",
        "end",
        "remove",
        "execute",
        "settings",
        "channels",
        "status",
        "player",
        "log",
        "nudge",
        "broadcast",
    }


def test_core_groups():
    assert names(gaminator_admin) == {"admin-role", "announce", "status"}
    assert gaminator_admin.default_permissions == discord.Permissions(manage_guild=True)
    assert gaminator_admin.guild_only
    assert names(gaminator) == {"games"}
    assert gaminator.default_permissions is None
    assert gaminator.guild_only


def test_games_embed():
    specs = [replace(spec, listed=True) for spec in GAMES[:2]]
    current = {
        "assassin": {"name": "Autumn", "status": "open"},
        "imposter": {"name": "Friday", "status": "active"},
    }
    embed = games_embed(specs, current)
    assert [f.name for f in embed.fields] == ["🗡️ Assassin", "🎭 Imposter"]
    assert "**Here now:** Autumn (sign-ups open)" in embed.fields[0].value
    assert "**Here now:** Friday (running)" in embed.fields[1].value
    for spec, field in zip(specs, embed.fields, strict=True):
        assert spec.summary in field.value
        assert spec.start_hint in field.value
        assert spec.players in field.value
    idle = games_embed(specs, {"assassin": None})
    assert all("Here now" not in f.value for f in idle.fields)


def test_games_embed_lists_only_listed_games():
    listed = [spec.key for spec in GAMES if spec.listed]
    assert listed == ["mostlikely", "fiftyfifty", "spectrum", "headlines"]
    embed = games_embed(GAMES, {})
    assert [f.name for f in embed.fields] == [
        "🤔 Most Likely To",
        "⚖️ Fifty-Fifty",
        "🌈 Spectrum",
        "📰 Headlines",
    ]


def test_imposter_groups():
    assert names(imposter) == {"join", "leave", "status", "rules", "players", "resend"}
    assert imposter_admin.parent is None and imposter_admin.name == "imposter-admin"
    assert imposter_admin.default_permissions == discord.Permissions(manage_guild=True)
    assert names(imposter_admin) == {
        "create",
        "start",
        "channel",
        "nudge",
        "vote",
        "close",
        "reroll",
        "prompt",
        "end",
        "remove",
        "settings",
        "status",
    }


async def test_mostlikely_subgroup():
    await mostlikely_commands.setup(MagicMock())
    try:
        assert names(gaminator) == {"games", "mostlikely"}
        assert mostlikely.parent is gaminator
        assert names(mostlikely) == {
            "create",
            "join",
            "leave",
            "start",
            "end",
            "status",
            "rules",
            "leaderboard",
        }
    finally:
        await mostlikely_commands.teardown(MagicMock())
    assert names(gaminator) == {"games"}


async def test_fiftyfifty_subgroup():
    bot = MagicMock()
    await fiftyfifty_commands.setup(bot)
    try:
        assert names(gaminator) == {"games", "fiftyfifty"}
        assert fiftyfifty.parent is gaminator
        assert names(fiftyfifty) == {
            "create",
            "join",
            "leave",
            "start",
            "end",
            "status",
            "rules",
            "leaderboard",
        }
        registered = {item.__name__ for item in bot.add_dynamic_items.call_args.args}
        assert registered == {"LobbyButton", "WriteButton", "DMButton", "VoteButton", "GuessSelect"}
        rounds = next(c for c in fiftyfifty.commands if c.name == "start").parameters[0]
        assert (rounds.min_value, rounds.max_value, rounds.default) == (1, 5, 1)
    finally:
        await fiftyfifty_commands.teardown(bot)
    assert names(gaminator) == {"games"}
    removed = {item.__name__ for item in bot.remove_dynamic_items.call_args.args}
    assert removed == {"LobbyButton", "WriteButton", "DMButton", "VoteButton", "GuessSelect"}


async def test_spectrum_subgroup():
    bot = MagicMock()
    await spectrum_commands.setup(bot)
    try:
        assert names(gaminator) == {"games", "spectrum"}
        assert spectrum.parent is gaminator
        assert names(spectrum) == {
            "create",
            "join",
            "leave",
            "start",
            "end",
            "status",
            "rules",
            "leaderboard",
        }
        assert len(spectrum.commands) <= 25
        registered = {item.__name__ for item in bot.add_dynamic_items.call_args.args}
        assert registered == {"LobbyButton", "TargetButton", "WriteClueButton", "GuessButton"}
        rounds = next(c for c in spectrum.commands if c.name == "start").parameters[0]
        assert (rounds.min_value, rounds.max_value, rounds.default) == (1, 5, 1)
    finally:
        await spectrum_commands.teardown(bot)
    assert names(gaminator) == {"games"}
    removed = {item.__name__ for item in bot.remove_dynamic_items.call_args.args}
    assert removed == {"LobbyButton", "TargetButton", "WriteClueButton", "GuessButton"}


async def test_headlines_subgroup():
    bot = MagicMock()
    await headlines_commands.setup(bot)
    try:
        assert names(gaminator) == {"games", "headlines"}
        assert headlines.parent is gaminator
        assert names(headlines) == {
            "create",
            "join",
            "leave",
            "start",
            "end",
            "status",
            "rules",
            "leaderboard",
        }
        assert len(headlines.commands) <= 25
        registered = {item.__name__ for item in bot.add_dynamic_items.call_args.args}
        assert registered == {"LobbyButton", "AnswerButton", "WriteButton", "VoteSelect"}
        rounds = next(c for c in headlines.commands if c.name == "start").parameters[0]
        assert (rounds.min_value, rounds.max_value, rounds.default) == (1, 5, 2)
    finally:
        await headlines_commands.teardown(bot)
    assert names(gaminator) == {"games"}
    removed = {item.__name__ for item in bot.remove_dynamic_items.call_args.args}
    assert removed == {"LobbyButton", "AnswerButton", "WriteButton", "VoteSelect"}


async def test_all_subgroups():
    listed = {
        "mostlikely": (mostlikely_commands, mostlikely),
        "fiftyfifty": (fiftyfifty_commands, fiftyfifty),
        "spectrum": (spectrum_commands, spectrum),
        "headlines": (headlines_commands, headlines),
    }
    assert list(listed) == [spec.key for spec in GAMES if spec.listed]
    attached = []
    try:
        for module, _ in listed.values():
            await module.setup(MagicMock())
            attached.append(module)
        assert names(gaminator) == {"games", *listed}
        for _, group in listed.values():
            assert group.parent is gaminator
            assert len(group.commands) <= 25
        assert len(gaminator.commands) <= 25
    finally:
        for module in reversed(attached):
            await module.teardown(MagicMock())
    assert names(gaminator) == {"games"}


def test_groups_fit_in_one_tree():
    """Top-level command names must be unique and each group within Discord's limit of 25."""
    top = [gaminator, gaminator_admin, assassin, admin, imposter, imposter_admin]
    assert len({g.name for g in top}) == len(top)
    for g in top:
        assert len(g.commands) <= 25


def test_describe_error():
    assert describe_error(NoCurrentGame()) == "There is no game right now."
    assert describe_error(GameError("custom")) == "custom"
    wrapped = app_commands.CommandInvokeError(MagicMock(), GameError("inner"))
    assert describe_error(wrapped) == "inner"
    assert describe_error(RuntimeError("boom")) is None

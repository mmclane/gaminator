"""The slash-command tree: every game under its own group, organizer commands under admin."""

from unittest.mock import MagicMock

import discord
from discord import app_commands

from gaminator.core.commands import gaminator
from gaminator.core.util import GameError, NoCurrentGame, describe_error
from gaminator.games.assassin.commands import assassin
from gaminator.games.assassin.commands.group import admin
from gaminator.games.imposter.commands import imposter
from gaminator.games.imposter.commands.group import admin as imposter_admin


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


def test_core_and_imposter_groups():
    assert names(gaminator) == {"admin-role", "announce", "status"}
    assert gaminator.default_permissions == discord.Permissions(manage_guild=True)
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


def test_groups_fit_in_one_tree():
    """Top-level command names must be unique and each group within Discord's limit of 25."""
    top = [gaminator, assassin, admin, imposter, imposter_admin]
    assert len({g.name for g in top}) == len(top)
    for g in top:
        assert len(g.commands) <= 25


def test_describe_error():
    assert describe_error(NoCurrentGame()) == "There is no game right now."
    assert describe_error(GameError("custom")) == "custom"
    wrapped = app_commands.CommandInvokeError(MagicMock(), GameError("inner"))
    assert describe_error(wrapped) == "inner"
    assert describe_error(RuntimeError("boom")) is None

"""Server-wide settings shared by every game: the /gaminator group."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..games import GAMES
from .util import GameError, is_game_admin, respond

gaminator = app_commands.Group(
    name="gaminator",
    description="Settings shared by every game on this server",
    default_permissions=discord.Permissions(manage_guild=True),
    guild_only=True,
)


@gaminator.command(name="admin-role", description="Role allowed to organize games")
@app_commands.describe(role="Leave empty to clear")
async def admin_role(interaction: discord.Interaction, role: discord.Role | None = None):
    if not interaction.permissions.manage_guild:
        raise GameError("Only members with Manage Server can change the admin role.")
    settings = interaction.client.guild_settings  # type: ignore[attr-defined]
    await settings.set_admin_role(interaction.guild_id, role.id if role else None)
    await respond(
        interaction, f"Admin role set to {role.mention}." if role else "Admin role cleared."
    )


@gaminator.command(name="announce", description="Channel where game announcements are posted")
@app_commands.describe(channel="Leave empty to stop announcing")
async def announce_channel(
    interaction: discord.Interaction, channel: discord.TextChannel | None = None
):
    await is_game_admin(interaction)
    settings = interaction.client.guild_settings  # type: ignore[attr-defined]
    await settings.set_announce_channel(interaction.guild_id, channel.id if channel else None)
    await respond(
        interaction,
        f"Announcements will go to {channel.mention}." if channel else "Announcements off.",
    )


@gaminator.command(name="status", description="Which games are running on this server")
async def status(interaction: discord.Interaction):
    await is_game_admin(interaction)
    lines = []
    for spec in GAMES:
        repo = interaction.client.games[spec.key].repo  # type: ignore[attr-defined]
        game = await repo.get_current_game(interaction.guild_id)
        state = f"**{game['name']}** ({game['status']})" if game else "not running"
        lines.append(f"• {spec.title}: {state}")
    await respond(interaction, "\n".join(lines))


async def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(gaminator)


async def teardown(bot: commands.Bot) -> None:
    bot.tree.remove_command(gaminator.name)

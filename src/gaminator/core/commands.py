"""Core command groups: /gaminator for everyone, /gaminator-admin for server settings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ..games import GAMES
from .messaging import NO_MENTIONS
from .registry import GameSpec
from .util import GameError, is_game_admin, respond

gaminator = app_commands.Group(
    name="gaminator",
    description="Browse and play Gaminator games",
    guild_only=True,
)
gaminator_admin = app_commands.Group(
    name="gaminator-admin",
    description="Settings shared by every game on this server",
    default_permissions=discord.Permissions(manage_guild=True),
    guild_only=True,
)


def games_embed(specs: Sequence[GameSpec], current: Mapping[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title="🎲 Games you can play here",
        description="Pick one and get a group together.",
        colour=discord.Colour.blurple(),
    )
    for spec in specs:
        if not spec.listed:
            continue
        lines = [spec.summary, f"**Start:** {spec.start_hint}", f"**Players:** {spec.players}"]
        game = current.get(spec.key)
        if game is not None:
            state = "sign-ups open" if game["status"] == "open" else "running"
            lines.append(f"**Here now:** {game['name']} ({state})")
        embed.add_field(name=f"{spec.emoji} {spec.title}", value="\n".join(lines), inline=False)
    return embed


@gaminator.command(name="games", description="The games this bot can run")
async def games_cmd(interaction: discord.Interaction):
    current = {}
    for spec in GAMES:
        if spec.listed:
            repo = interaction.client.games[spec.key].repo  # type: ignore[attr-defined]
            current[spec.key] = await repo.get_current_game(interaction.guild_id)
    await interaction.response.send_message(
        embed=games_embed(GAMES, current), allowed_mentions=NO_MENTIONS
    )


@gaminator_admin.command(name="admin-role", description="Role allowed to organize games")
@app_commands.describe(role="Leave empty to clear")
async def admin_role(interaction: discord.Interaction, role: discord.Role | None = None):
    if not interaction.permissions.manage_guild:
        raise GameError("Only members with Manage Server can change the admin role.")
    settings = interaction.client.guild_settings  # type: ignore[attr-defined]
    await settings.set_admin_role(interaction.guild_id, role.id if role else None)
    await respond(
        interaction, f"Admin role set to {role.mention}." if role else "Admin role cleared."
    )


@gaminator_admin.command(name="announce", description="Channel where game announcements are posted")
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


@gaminator_admin.command(name="status", description="Which games are running on this server")
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
    bot.tree.add_command(gaminator_admin)


async def teardown(bot: commands.Bot) -> None:
    bot.tree.remove_command(gaminator.name)
    bot.tree.remove_command(gaminator_admin.name)

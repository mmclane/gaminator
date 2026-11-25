"""/gaminator headlines create, join, leave, start, end, status, rules, leaderboard."""

from __future__ import annotations

import aiosqlite
import discord
from discord import app_commands

from ....core.messaging import NO_MENTIONS, announce, display_name
from ....core.util import GameError, missing_channel_permissions, require_game, respond
from ..lobby import lobby_view
from ..services import game as flow
from ..services.game import (
    DEFAULT_GAME,
    answer_seconds,
    lobby_embed,
    required_players,
    rules_text,
    title,
    vote_seconds,
    write_seconds,
)
from ..services.rules import DEFAULT_ROUNDS, MAX_ROUNDS
from .group import ctx, headlines, require_host_or_admin

ALREADY_RUNNING = (
    "There's already a Headlines game on this server. Its host or an admin can end it "
    "with `/gaminator headlines end`."
)


@headlines.command(name="create", description="Open a Headlines game in this channel")
@app_commands.describe(name="Name of the game (default: yours)")
async def create(interaction: discord.Interaction, name: str | None = None):
    bot = interaction.client
    repo = ctx(interaction).repo
    if await repo.get_current_game(interaction.guild_id):
        raise GameError(ALREADY_RUNNING)
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel | discord.Thread):
        raise GameError("Run this in a text channel or a thread.")
    me = interaction.guild.me if interaction.guild else None
    missing = missing_channel_permissions(channel, me)
    if me is not None and not channel.permissions_for(me).embed_links:
        missing.append("Embed Links")
    if missing:
        raise GameError("I can't run a game here. I'm missing: " + ", ".join(missing) + ".")
    host_name = await display_name(bot, interaction.guild_id, interaction.user.id)
    game_name = (name or f"{host_name}'s game")[:100]
    try:
        game = await repo.create_game(
            interaction.guild_id, game_name, interaction.user.id, channel.id
        )
    except aiosqlite.IntegrityError:
        raise GameError(ALREADY_RUNNING) from None
    await repo.join(game["id"], interaction.user.id)
    need = required_players(bot)
    await interaction.response.send_message(
        embed=lobby_embed(
            game,
            [host_name],
            host_name,
            need,
            answer_seconds(bot),
            write_seconds(bot),
            vote_seconds(bot),
        ),
        view=lobby_view(game),
        allowed_mentions=NO_MENTIONS,
    )
    card = await interaction.original_response()
    await repo.set_lobby_message(game["id"], card.id)
    await flow.refresh_lobby(bot, game["id"])
    await respond(
        interaction,
        "**Host notes**\n"
        f"• You're in as the first player. Once at least {need} players have joined, press "
        f"**Start** for {flow.plural(DEFAULT_ROUNDS, 'round')}, or run "
        "`/gaminator headlines start rounds:` to play fewer.\n"
        "• End the game at any time with `/gaminator headlines end`. If you leave, the "
        "longest-joined player becomes the host.",
    )
    if channel.id != await bot.guild_settings.get_announce_channel(game["guild_id"]):
        await announce(
            bot,
            game["guild_id"],
            f"📰 A new {title(game)} is open in <#{channel.id}>! "
            "Use `/gaminator headlines join` to play.",
        )


@headlines.command(name="join", description="Join the Headlines game")
async def join(interaction: discord.Interaction):
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    await respond(interaction, await flow.join_game(interaction.client, game, interaction.user.id))


@headlines.command(name="leave", description="Leave the Headlines game")
async def leave(interaction: discord.Interaction):
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    await respond(interaction, await flow.leave_game(interaction.client, game, interaction.user.id))


@headlines.command(name="start", description="Start the game (host or admin)")
@app_commands.describe(rounds=f"How many rounds to play (default {DEFAULT_ROUNDS})")
async def start(
    interaction: discord.Interaction,
    rounds: app_commands.Range[int, 1, MAX_ROUNDS] = DEFAULT_ROUNDS,
):
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await require_host_or_admin(interaction, game)
    await interaction.response.defer(ephemeral=True)
    await respond(interaction, await flow.begin_game(interaction.client, game, rounds))


@headlines.command(name="end", description="End the game (host or admin)")
async def end(interaction: discord.Interaction):
    bot = interaction.client
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await require_host_or_admin(interaction, game)
    await interaction.response.defer(ephemeral=True)
    name = flow.safe(await display_name(bot, game["guild_id"], interaction.user.id))
    if game["status"] == "active":
        await flow.finish(bot, game, note=f"ended by {name}")
    else:
        await flow.cancel_lobby(bot, game, f"ended by {name}")
    await respond(interaction, f"{title(game)} has ended.")


@headlines.command(name="status", description="Where the game stands, and what you still owe")
async def status(interaction: discord.Interaction):
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    text = await flow.status_reply(interaction.client, game, interaction.user.id)
    await respond(interaction, text, allowed_mentions=NO_MENTIONS)


@headlines.command(name="rules", description="How Headlines works")
async def rules(interaction: discord.Interaction):
    bot = interaction.client
    game = await ctx(interaction).repo.get_current_game(interaction.guild_id)
    await respond(
        interaction,
        rules_text(
            game if game is not None else DEFAULT_GAME,
            answer_seconds(bot),
            write_seconds(bot),
            vote_seconds(bot),
            required_players(bot),
        ),
    )


@headlines.command(name="leaderboard", description="All-time Headlines standings")
@app_commands.describe(member="Show one member's numbers")
async def leaderboard(interaction: discord.Interaction, member: discord.User | None = None):
    await interaction.response.defer()
    text = await flow.leaderboard_reply(
        interaction.client,
        interaction.guild_id,
        interaction.user.id,
        member.id if member is not None else None,
    )
    await interaction.followup.send(text, allowed_mentions=NO_MENTIONS)

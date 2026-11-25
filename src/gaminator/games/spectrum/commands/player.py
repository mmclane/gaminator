"""/gaminator spectrum create, join, leave, start, end, status, rules, leaderboard."""

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
    clue_seconds,
    guess_seconds,
    lobby_embed,
    required_players,
    rules_text,
    title,
)
from ..services.rules import DEFAULT_ROUNDS, MAX_ROUNDS
from .group import ctx, require_host_or_admin, spectrum

ALREADY_RUNNING = (
    "There's already a Spectrum game on this server. Its host or an admin can end it "
    "with `/gaminator spectrum end`."
)


@spectrum.command(name="create", description="Open a Spectrum game in this channel")
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
            game, [host_name], host_name, need, clue_seconds(bot), guess_seconds(bot)
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
        f"`/gaminator spectrum start rounds:` to play up to {MAX_ROUNDS}.\n"
        "• End the game at any time with `/gaminator spectrum end`. If you leave, the "
        "longest-joined player becomes the host.",
    )
    if channel.id != await bot.guild_settings.get_announce_channel(game["guild_id"]):
        await announce(
            bot,
            game["guild_id"],
            f"🌈 A new {title(game)} is open in <#{channel.id}>! "
            "Use `/gaminator spectrum join` to play.",
        )


@spectrum.command(name="join", description="Join the Spectrum game")
async def join(interaction: discord.Interaction):
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    await respond(interaction, await flow.join_game(interaction.client, game, interaction.user.id))


@spectrum.command(name="leave", description="Leave the Spectrum game")
async def leave(interaction: discord.Interaction):
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    await respond(interaction, await flow.leave_game(interaction.client, game, interaction.user.id))


@spectrum.command(name="start", description="Start the game (host or admin)")
@app_commands.describe(rounds=f"How many rounds to play (default {DEFAULT_ROUNDS})")
async def start(
    interaction: discord.Interaction,
    rounds: app_commands.Range[int, 1, MAX_ROUNDS] = DEFAULT_ROUNDS,
):
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await require_host_or_admin(interaction, game)
    await interaction.response.defer(ephemeral=True)
    await respond(interaction, await flow.begin_game(interaction.client, game, rounds))


@spectrum.command(name="end", description="End the game (host or admin)")
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


@spectrum.command(name="status", description="Where the game stands, and your own part in it")
async def status(interaction: discord.Interaction):
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    text = await flow.status_reply(interaction.client, game, interaction.user.id)
    await respond(interaction, text, allowed_mentions=NO_MENTIONS)


@spectrum.command(name="rules", description="How Spectrum works")
async def rules(interaction: discord.Interaction):
    bot = interaction.client
    game = await ctx(interaction).repo.get_current_game(interaction.guild_id)
    await respond(
        interaction,
        rules_text(
            game if game is not None else DEFAULT_GAME,
            clue_seconds(bot),
            guess_seconds(bot),
            required_players(bot),
        ),
    )


@spectrum.command(name="leaderboard", description="All-time Spectrum standings")
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

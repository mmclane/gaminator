"""/gaminator mostlikely create, join, leave, start, end, status, rules, leaderboard."""

from __future__ import annotations

import aiosqlite
import discord
from discord import app_commands

from ....core.messaging import NO_MENTIONS, announce, display_name
from ....core.util import (
    GameError,
    missing_channel_permissions,
    parse_iso,
    require_game,
    respond,
)
from ..lobby import lobby_view
from ..services import game as flow
from ..services.game import (
    DEFAULT_GAME,
    DEFAULT_ROUNDS,
    lobby_embed,
    prompt_line,
    required_players,
    rules_text,
    title,
    vote_seconds,
)
from .group import ctx, mostlikely, require_host_or_admin

ALREADY_RUNNING = (
    "There's already a Most Likely To game on this server. Its host or an admin can end it "
    "with `/gaminator mostlikely end`."
)


@mostlikely.command(name="create", description="Open a Most Likely To game in this channel")
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
        embed=lobby_embed(game, [host_name], host_name, need, vote_seconds(bot)),
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
        f"**Start** for {DEFAULT_ROUNDS} prompts, or run `/gaminator mostlikely start rounds:` "
        "to pick another number.\n"
        "• End the game at any time with `/gaminator mostlikely end`. If you leave, the "
        "longest-joined player becomes the host.",
    )
    if channel.id != await bot.guild_settings.get_announce_channel(game["guild_id"]):
        await announce(
            bot,
            game["guild_id"],
            f"🤔 A new {title(game)} is open in <#{channel.id}>! "
            "Use `/gaminator mostlikely join` to play.",
        )


@mostlikely.command(name="join", description="Join the Most Likely To game")
async def join(interaction: discord.Interaction):
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    await respond(interaction, await flow.join_game(interaction.client, game, interaction.user.id))


@mostlikely.command(name="leave", description="Leave the Most Likely To game")
async def leave(interaction: discord.Interaction):
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    await respond(interaction, await flow.leave_game(interaction.client, game, interaction.user.id))


@mostlikely.command(name="start", description="Start the game (host or admin)")
@app_commands.describe(rounds=f"How many prompts to play (default {DEFAULT_ROUNDS})")
async def start(
    interaction: discord.Interaction, rounds: app_commands.Range[int, 1, 50] = DEFAULT_ROUNDS
):
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await require_host_or_admin(interaction, game)
    await interaction.response.defer(ephemeral=True)
    await respond(interaction, await flow.begin_game(interaction.client, game, rounds))


@mostlikely.command(name="end", description="End the game (host or admin)")
async def end(interaction: discord.Interaction):
    bot = interaction.client
    game = await require_game(ctx(interaction).repo, interaction.guild_id)
    await require_host_or_admin(interaction, game)
    await interaction.response.defer(ephemeral=True)
    name = await display_name(bot, game["guild_id"], interaction.user.id)
    if game["status"] == "active":
        await flow.finish(bot, game, note=f"ended by {name}")
    else:
        await flow.cancel_lobby(bot, game, f"ended by {name}")
    await respond(interaction, f"{title(game)} has ended.")


@mostlikely.command(name="status", description="Where the game stands, and your own vote")
async def status(interaction: discord.Interaction):
    bot = interaction.client
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    players = await repo.list_players(game["id"], active_only=True)
    names = await flow.names_by_id(bot, game, players)
    host = await display_name(bot, game["guild_id"], game["host_id"])
    roster = ", ".join(names[p["id"]] for p in players) or "nobody"
    lines = [f"{title(game)}, hosted by **{host}**.", f"**Players ({len(players)}):** {roster}"]
    me = await repo.get_player(game["id"], interaction.user.id)
    if game["status"] == "open":
        lines.append(f"Sign-ups are open. It needs at least {required_players(bot)} to start.")
        await respond(interaction, "\n".join(lines), allowed_mentions=NO_MENTIONS)
        return
    rnd = await repo.get_current_round(game["id"])
    if rnd is None or rnd["closed_at"]:
        lines.append("The next prompt is on its way.")
        await respond(interaction, "\n".join(lines), allowed_mentions=NO_MENTIONS)
        return
    voted, eligible = await repo.vote_progress(rnd["id"])
    deadline = parse_iso(rnd["deadline_at"])
    lines.append(prompt_line(game, rnd))
    lines.append(f"{voted}/{eligible} votes in. Closes <t:{int(deadline.timestamp())}:R>.")
    if me is None or me["left_at"] is not None:
        lines.append("You're not in this game. Join with `/gaminator mostlikely join`.")
    elif not await repo.on_roster(rnd["id"], me["id"]):
        lines.append("You're in from the next prompt.")
    else:
        vote = await repo.get_vote(rnd["id"], me["id"])
        if vote is None:
            lines.append("🗳️ You haven't voted on this prompt yet.")
        else:
            target = await repo.get_player_by_id(vote["target_id"])
            tname = await display_name(bot, game["guild_id"], target["user_id"])
            lines.append(f"🗳️ You voted for **{tname}**.")
    await respond(interaction, "\n".join(lines), allowed_mentions=NO_MENTIONS)


@mostlikely.command(name="rules", description="How Most Likely To works")
async def rules(interaction: discord.Interaction):
    game = await ctx(interaction).repo.get_current_game(interaction.guild_id)
    await respond(
        interaction,
        rules_text(game if game is not None else DEFAULT_GAME, vote_seconds(interaction.client)),
    )


@mostlikely.command(name="leaderboard", description="All-time Most Likely To standings")
@app_commands.describe(member="Show one member's numbers and recent prompt wins")
async def leaderboard(interaction: discord.Interaction, member: discord.User | None = None):
    await interaction.response.defer()
    text = await flow.leaderboard_reply(
        interaction.client,
        interaction.guild_id,
        interaction.user.id,
        member.id if member is not None else None,
    )
    await interaction.followup.send(text, allowed_mentions=NO_MENTIONS)

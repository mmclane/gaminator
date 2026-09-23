"""Player commands: /assassin join, leave, status, report, rules, players, resend."""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from ....core.messaging import announce, display_name, dm
from ....core.repo import AlreadyJoined
from ....core.util import (
    GameError,
    NotAPlayer,
    require_game,
    require_player,
    respond,
    signup_text,
)
from ..services.game import (
    DEFAULT_GAME,
    eliminate_player,
    inactivity_deadline,
    required_players,
    rules_text,
    send_assignment,
    title,
)
from ..services.rules import judge_report
from .group import assassin, ctx

log = logging.getLogger(__name__)


@assassin.command(name="join", description="Join the Assassin game")
async def join(interaction: discord.Interaction):
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    if game["status"] != "open":
        raise GameError("The game has already started. Catch the next one!")
    try:
        await repo.add_player(game["id"], interaction.user.id)
    except AlreadyJoined:
        raise GameError("You're already in.") from None
    # The welcome DM and name lookup can take longer than Discord's 3-second window.
    await interaction.response.defer(ephemeral=True)
    count = await repo.alive_count(game["id"])
    dm_ok = await dm(
        interaction.client,
        interaction.user.id,
        "# 🗡️ Welcome to Assassin\n"
        f"You've joined {title(game)}. There's nothing to do yet: just wait for the organizer "
        "to start the game. I'll DM your target and poison word here when it does.\n\n"
        + rules_text(game, footer=False),
    )
    text = f"You're in {title(game)} ({count} signed up)."
    if not dm_ok:
        text += (
            "\n⚠️ I couldn't DM you. Allow direct messages from server members, or you'll "
            "have to rely on `/assassin status` to see your target."
        )
    await respond(interaction, text)
    name = await display_name(interaction.client, game["guild_id"], interaction.user.id)
    await announce(
        interaction.client,
        game["guild_id"],
        f"🙋 **{name}** joined {title(game)}. {signup_text(count, required_players(interaction.client))}",
    )


@assassin.command(name="leave", description="Leave the game")
async def leave(interaction: discord.Interaction):
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    player = await require_player(repo, game, interaction.user.id)
    if game["status"] == "open":
        await repo.delete_player(player["id"])
        count = await repo.alive_count(game["id"])
        name = await display_name(interaction.client, game["guild_id"], interaction.user.id)
        await announce(
            interaction.client,
            game["guild_id"],
            f"🚪 **{name}** left {title(game)}. {signup_text(count, required_players(interaction.client))}",
        )
    else:
        await eliminate_player(interaction.client, game, player, None, "left")
    await respond(interaction, "You've left the game.")


@assassin.command(name="status", description="Your target, poison word, and standing")
async def status(interaction: discord.Interaction):
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    player = await require_player(repo, game, interaction.user.id, alive=False)
    alive = await repo.alive_count(game["id"])
    if game["status"] == "open":
        await respond(interaction, f"Sign-ups are open; {alive} players so far.")
        return
    if not player["alive"]:
        await respond(
            interaction,
            f"You're out ({player['cause']}) with {player['kills']} kill"
            f"{'s' if player['kills'] != 1 else ''}. {alive} players remain.",
        )
        return
    target = await repo.get_player_by_id(player["target_id"]) if player["target_id"] else None
    target_name = (
        await display_name(interaction.client, game["guild_id"], target["user_id"])
        if target
        else "nobody"
    )
    deadline = inactivity_deadline(game, player)
    left = game["max_wrong_reports"] - player["wrong_reports"]
    lines = [
        f"🎯 Target: **{target_name}**",
        f"☠️ Poison word: **{player['poison_word']}**",
        f"🪤 Bait word: **{player['bait_word']}**",
        f"🔪 Kills: {player['kills']}",
        f"🚔 Wrong reports left before you're out: {left}",
        f"👥 Players remaining: {alive}",
    ]
    if game["bounty_player_id"] == player["id"]:
        lines.append("💰 There's a bounty on you: anyone can quick-draw you right now.")
    if deadline:
        lines.append(f"💤 Post something before <t:{int(deadline.timestamp())}:R> to stay in.")
    await respond(interaction, "\n".join(lines))


@assassin.command(name="report", description="Report the player you think is hunting you")
@app_commands.describe(user="Who you think your assassin is")
async def report(interaction: discord.Interaction, user: discord.User):
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id, "active")
    reporter = await require_player(repo, game, interaction.user.id)
    accused = await repo.get_player(game["id"], user.id)
    if accused is None or not accused["alive"]:
        raise NotAPlayer()
    verdict = judge_report(reporter, accused)
    if verdict.ok:
        await interaction.response.defer(ephemeral=True)
        await eliminate_player(interaction.client, game, accused, reporter, "arrested")
        await respond(
            interaction,
            f"🚔 Right call. **{user.display_name}** was your assassin and has been arrested.",
        )
        return
    if verdict.reason != "wrong":
        raise GameError(verdict.reason)
    count = await repo.add_wrong_report(reporter["id"])
    await repo.log_event(game["id"], "false_report", reporter["id"], accused["id"])
    left = game["max_wrong_reports"] - count
    if left <= 0:
        await interaction.response.defer(ephemeral=True)
        await eliminate_player(interaction.client, game, reporter, None, "false_reports")
        await respond(
            interaction,
            f"❌ **{user.display_name}** isn't hunting you, and that was your last wrong "
            "report. You've been eliminated.",
        )
        return
    await respond(
        interaction,
        f"❌ **{user.display_name}** isn't hunting you. {left} wrong report"
        f"{'s' if left != 1 else ''} left before you're eliminated.",
    )


@assassin.command(name="rules", description="How the Assassin game works")
async def rules(interaction: discord.Interaction):
    game = await ctx(interaction).repo.get_current_game(interaction.guild_id)
    await respond(interaction, rules_text(game if game is not None else DEFAULT_GAME))


@assassin.command(name="players", description="Who is still alive")
async def players(interaction: discord.Interaction):
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    alive = await repo.list_players(game["id"], alive_only=True)
    names = sorted(
        [await display_name(interaction.client, game["guild_id"], p["user_id"]) for p in alive],
        key=str.lower,
    )
    label = "Signed up" if game["status"] == "open" else "Still alive"
    await respond(interaction, f"**{label} ({len(names)}):** " + (", ".join(names) or "nobody"))


@assassin.command(name="resend", description="DM me my target and poison word again")
async def resend(interaction: discord.Interaction):
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id, "active")
    player = await require_player(repo, game, interaction.user.id)
    ok = await send_assignment(interaction.client, game, player)
    await respond(
        interaction, "Sent, check your DMs." if ok else "I couldn't DM you. Use `/assassin status`."
    )

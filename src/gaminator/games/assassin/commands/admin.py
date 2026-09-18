"""Organizer commands: the /assassin-admin group.

Discord hides the group from members without Manage Server. Every command also checks
``is_game_admin`` at run time so the role set with ``/gaminator admin-role`` works once a
server admin makes the group visible to that role under Integrations.
"""

from __future__ import annotations

import logging
from typing import Literal

import discord
from discord import app_commands

from ....core.messaging import NO_MENTIONS, announce, display_name, dm
from ....core.util import (
    GameError,
    NotAPlayer,
    is_game_admin,
    parse_iso,
    require_game,
    respond,
)
from ..repo import SETTING_COLUMNS
from ..services.chain import build_assignments
from ..services.game import (
    RNG,
    eliminate_player,
    intro_text,
    required_players,
    send_assignment,
    title,
)
from .group import admin, ctx

log = logging.getLogger(__name__)


def format_settings(game) -> str:
    return (
        f"Kill emoji (poison, trap, and quick draw): {game['kill_emoji']}\n"
        f"Shield emoji: {game['shield_emoji']}\n"
        f"Bounty after: {game['bounty_hours'] or 'off'}"
        f"{' hours' if game['bounty_hours'] else ''}\n"
        f"Inactivity limit: {game['inactivity_hours']} hours\n"
        f"Wrong reports allowed: {game['max_wrong_reports']}\n"
        f"Reveal killer in announcements: {'yes' if game['reveal_killer'] else 'no'}"
    )


# -- lifecycle -------------------------------------------------------------------------------


@admin.command(name="create", description="Open sign-ups for a new game")
@app_commands.describe(name="Name of the game")
async def create(interaction: discord.Interaction, name: str = "Assassin"):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    if await repo.get_current_game(interaction.guild_id):
        raise GameError("There's already a game running. End it first with `/assassin-admin end`.")
    game = await repo.create_game(interaction.guild_id, name[:100], interaction.user.id)
    # The intro is the public reply, so it lands in the channel the organizer chose.
    await interaction.response.send_message(intro_text(game), allowed_mentions=NO_MENTIONS)
    await respond(
        interaction,
        "Organizer notes: check `/assassin-admin settings` and `/gaminator announce`, then "
        f"start with `/assassin-admin start` once at least {required_players(interaction.client)} players have joined.",
    )
    if interaction.channel_id != await interaction.client.guild_settings.get_announce_channel(  # type: ignore[attr-defined]
        game["guild_id"]
    ):
        await announce(
            interaction.client,
            game["guild_id"],
            f"🎯 A new {title(game)} is open for sign-ups in "
            f"<#{interaction.channel_id}>! Use `/assassin join` to play.",
        )


@admin.command(name="start", description="Assign targets and start the game")
async def start(interaction: discord.Interaction):
    await is_game_admin(interaction)
    bot = interaction.client
    game_ctx = ctx(interaction)
    repo = game_ctx.repo
    game = await require_game(repo, interaction.guild_id, "open")
    players = await repo.list_players(game["id"])
    if len(players) < required_players(interaction.client):
        raise GameError(
            f"Need at least {required_players(interaction.client)} players; {len(players)} have joined."
        )
    await interaction.response.defer(ephemeral=True)
    assignments = build_assignments([p["id"] for p in players], game_ctx.words, RNG)
    await repo.start_game(game["id"], assignments)
    game = await repo.get_game(game["id"])
    failed = []
    for p in await repo.list_players(game["id"]):
        if not await send_assignment(bot, game, p):
            failed.append(await display_name(bot, game["guild_id"], p["user_id"]))
    text = f"{title(game)} has started with {len(players)} players."
    if failed:
        text += (
            "\n⚠️ Couldn't DM these players (DMs closed?). They can use `/assassin status` to "
            "see their target: " + ", ".join(failed)
        )
    await respond(interaction, text)
    await announce(
        bot,
        game["guild_id"],
        f"🔪 {title(game)} has begun with {len(players)} players. Check your DMs for "
        "your target. Trust no one.",
    )


@admin.command(name="end", description="End the current game")
async def end(interaction: discord.Interaction):
    await is_game_admin(interaction)
    bot = interaction.client
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    alive = await repo.list_players(game["id"], alive_only=True)
    winner = alive[0] if len(alive) == 1 and game["status"] == "active" else None
    await repo.finish_game(game["id"], winner["id"] if winner else None)
    await respond(interaction, f"{title(game)} has ended.")
    if game["status"] == "active":
        names = [await display_name(bot, game["guild_id"], p["user_id"]) for p in alive]
        survivors = ", ".join(f"**{n}**" for n in names) or "nobody"
        await announce(
            bot,
            game["guild_id"],
            f"🏁 {title(game)} was ended by an organizer. Still standing: {survivors}.",
        )


@admin.command(name="remove", description="Remove a player from the game")
async def remove(interaction: discord.Interaction, user: discord.User):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    player = await repo.get_player(game["id"], user.id)
    if player is None or (game["status"] == "active" and not player["alive"]):
        raise NotAPlayer()
    if game["status"] == "open":
        await repo.delete_player(player["id"])
    else:
        await eliminate_player(interaction.client, game, player, None, "removed")
    await respond(interaction, f"Removed {user.mention} from the game.")


@admin.command(name="execute", description="Eliminate a player by the state, with a reason")
@app_commands.describe(user="The player", reason="Announced with the elimination")
async def execute(interaction: discord.Interaction, user: discord.User, reason: str | None = None):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id, "active")
    player = await repo.get_player(game["id"], user.id)
    if player is None or not player["alive"]:
        raise NotAPlayer()
    await interaction.response.defer(ephemeral=True)
    await eliminate_player(
        interaction.client, game, player, None, "executed", reason[:200] if reason else None
    )
    await respond(interaction, f"⚖️ {user.mention} has been executed by the state.")


# -- settings --------------------------------------------------------------------------------


@admin.command(name="settings", description="Show or change game settings")
@app_commands.describe(
    inactivity_hours="Hours without a message before a player is eliminated",
    max_wrong_reports="Wrong reports a player may make before being eliminated",
    reveal_killer="Name the assassin in elimination announcements",
    kill_emoji="Reaction that triggers a kill (poison, trap, or quick draw)",
    shield_emoji="Reaction players put on their own message to protect it",
    bounty_hours="Hours with no elimination before a bounty is placed (0 turns bounties off)",
)
async def settings(
    interaction: discord.Interaction,
    inactivity_hours: app_commands.Range[int, 1, 720] | None = None,
    max_wrong_reports: app_commands.Range[int, 1, 20] | None = None,
    reveal_killer: bool | None = None,
    kill_emoji: str | None = None,
    shield_emoji: str | None = None,
    bounty_hours: app_commands.Range[int, 0, 720] | None = None,
):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    changes = {
        "inactivity_hours": inactivity_hours,
        "max_wrong_reports": max_wrong_reports,
        "reveal_killer": None if reveal_killer is None else int(reveal_killer),
        "kill_emoji": kill_emoji.strip() if kill_emoji else None,
        "shield_emoji": shield_emoji.strip() if shield_emoji else None,
        "bounty_hours": bounty_hours,
    }
    changes = {k: v for k, v in changes.items() if v is not None and k in SETTING_COLUMNS}
    if changes.get("kill_emoji", game["kill_emoji"]) == changes.get(
        "shield_emoji", game["shield_emoji"]
    ):
        raise GameError("The kill and shield emoji must be different.")
    if changes:
        await repo.update_settings(game["id"], **changes)
        game = await repo.get_game(game["id"])
    await respond(interaction, ("Updated.\n" if changes else "") + format_settings(game))


@admin.command(name="channels", description="Restrict which channels the game is played in")
@app_commands.describe(
    action="add/remove a channel, clear the list (play everywhere), or list the channels",
    channel="The channel to add or remove",
)
async def channels(
    interaction: discord.Interaction,
    action: Literal["add", "remove", "clear", "list"],
    channel: discord.TextChannel | None = None,
):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    if action in ("add", "remove") and channel is None:
        raise GameError(f"Pick a channel to {action}.")
    if action == "add":
        await repo.add_channel(game["id"], channel.id)
        await respond(interaction, f"Added {channel.mention} to the game's channels.")
    elif action == "remove":
        ok = await repo.remove_channel(game["id"], channel.id)
        await respond(
            interaction,
            f"Removed {channel.mention}." if ok else f"{channel.mention} wasn't in the list.",
        )
    elif action == "clear":
        await repo.clear_channels(game["id"])
        await respond(interaction, "The game is now played in every channel I can read.")
    else:
        ids = await repo.list_channels(game["id"])
        if not ids:
            await respond(interaction, "No restriction: every channel I can read counts.")
        else:
            await respond(interaction, "Game channels: " + ", ".join(f"<#{c}>" for c in ids))


# -- visibility ------------------------------------------------------------------------------


@admin.command(name="status", description="Who's alive, who's hunting whom")
async def status(interaction: discord.Interaction):
    await is_game_admin(interaction)
    bot = interaction.client
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    players = await repo.list_players(game["id"])
    names = {p["id"]: await display_name(bot, game["guild_id"], p["user_id"]) for p in players}
    alive = [p for p in players if p["alive"]]
    dead = [p for p in players if not p["alive"]]
    lines = [f"{title(game)} is **{game['status']}**: {len(alive)} alive, {len(dead)} out."]
    if game["status"] == "open":
        lines.append("Signed up: " + (", ".join(names[p["id"]] for p in players) or "nobody"))
    else:
        for p in alive:
            target = names.get(p["target_id"], "nobody") if p["target_id"] else "nobody"
            last = parse_iso(p["last_message_at"])
            seen = f"<t:{int(last.timestamp())}:R>" if last else "never"
            bounty = " 💰" if p["id"] == game["bounty_player_id"] else ""
            lines.append(
                f"• {names[p['id']]}{bounty} → {target} (poison: *{p['poison_word']}*, "
                f"bait: *{p['bait_word']}*, kills: {p['kills']}, "
                f"wrong reports: {p['wrong_reports']}, last seen {seen})"
            )
        if dead:
            lines.append("Out: " + ", ".join(f"{names[p['id']]} ({p['cause']})" for p in dead))
    lines.append("\nPlayers can read the rules with `/assassin rules`.")
    await respond(interaction, "\n".join(lines)[:1990])


@admin.command(name="player", description="Details on one player")
async def player(interaction: discord.Interaction, user: discord.User):
    await is_game_admin(interaction)
    bot = interaction.client
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    p = await repo.get_player(game["id"], user.id)
    if p is None:
        raise GameError(f"{user.mention} isn't in this game.")
    await interaction.response.defer(ephemeral=True)
    name = await display_name(bot, game["guild_id"], user.id)
    lines = [f"**{name}**: {'alive' if p['alive'] else 'out (' + str(p['cause']) + ')'}"]
    if game["status"] != "open":
        target = await repo.get_player_by_id(p["target_id"]) if p["target_id"] else None
        hunter = await repo.get_hunter(p["id"]) if p["alive"] else None
        tname = await display_name(bot, game["guild_id"], target["user_id"]) if target else "nobody"
        hname = await display_name(bot, game["guild_id"], hunter["user_id"]) if hunter else "nobody"
        last = parse_iso(p["last_message_at"])
        lines += [
            f"🎯 Target: {tname}",
            f"🕵️ Hunted by: {hname}",
            f"☠️ Poison word: {p['poison_word'] or '-'}",
            f"🪤 Bait word: {p['bait_word'] or '-'}",
            f"💰 Bounty: {'yes' if p['id'] == game['bounty_player_id'] else 'no'}",
            f"🔪 Kills: {p['kills']}",
            f"🚔 Wrong reports: {p['wrong_reports']} / {game['max_wrong_reports']}",
            f"💬 Last message: {f'<t:{int(last.timestamp())}:R>' if last else 'never'}",
        ]
        if p["eliminated_by"]:
            by = await repo.get_player_by_id(p["eliminated_by"])
            if by:
                lines.append(
                    "💀 Eliminated by: " + await display_name(bot, game["guild_id"], by["user_id"])
                )
    await respond(interaction, "\n".join(lines))


@admin.command(name="log", description="Recent game events")
async def log_cmd(interaction: discord.Interaction):
    await is_game_admin(interaction)
    bot = interaction.client
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    events = await repo.list_events(game["id"], limit=20)
    if not events:
        await respond(interaction, "Nothing has happened yet.")
        return
    players = {p["id"]: p for p in await repo.list_players(game["id"])}

    async def name(pid):
        if pid is None or pid not in players:
            return "-"
        return await display_name(bot, game["guild_id"], players[pid]["user_id"])

    lines = []
    for e in events:
        when = parse_iso(e["created_at"])
        stamp = f"<t:{int(when.timestamp())}:f>" if when else ""
        actor, victim = await name(e["actor_id"]), await name(e["victim_id"])
        detail = f" ({e['detail']})" if e["detail"] else ""
        lines.append(f"{stamp} `{e['kind']}` {actor} → {victim}{detail}")
    await respond(interaction, "\n".join(lines)[:1990])


@admin.command(name="nudge", description="DM every living player their current target")
async def nudge(interaction: discord.Interaction):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id, "active")
    await interaction.response.defer(ephemeral=True)
    sent = 0
    for p in await repo.list_players(game["id"], alive_only=True):
        if await send_assignment(interaction.client, game, p):
            sent += 1
    await respond(interaction, f"Re-sent assignments to {sent} players.")


@admin.command(name="broadcast", description="DM a message to every living player")
async def broadcast(interaction: discord.Interaction, message: str):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    sent = 0
    for p in await repo.list_players(game["id"], alive_only=True):
        if await dm(interaction.client, p["user_id"], f"📣 {title(game)}: {message[:1800]}"):
            sent += 1
    await respond(interaction, f"Sent to {sent} players.")

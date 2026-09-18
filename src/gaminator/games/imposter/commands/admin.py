"""Organizer commands: the /imposter-admin group.

Discord hides the group from members without Manage Server. Every command also checks
``is_game_admin`` at run time so the role set with ``/gaminator admin-role`` works once a
server admin makes the group visible to that role under Integrations.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from ....core.messaging import NO_MENTIONS, announce, display_name
from ....core.util import (
    GameError,
    NotAPlayer,
    is_game_admin,
    missing_channel_permissions,
    require_game,
    respond,
)
from ..repo import SETTING_COLUMNS
from ..services import game as flow
from ..services.game import MAX_VOTE_OPTIONS, RNG, intro_text, required_players, title
from ..services.rules import choose_imposters
from .group import admin, ctx

log = logging.getLogger(__name__)


async def game_channel_or_here(interaction: discord.Interaction, game):
    """The game's channel (set at creation), falling back to where the command was run.

    Raises when the bot can't post there, since every announcement and vote goes to it.
    """
    channel = None
    if game["channel_id"]:
        channel = interaction.guild.get_channel_or_thread(game["channel_id"])
        if channel is None:
            try:
                channel = await interaction.client.fetch_channel(game["channel_id"])
            except discord.HTTPException:
                channel = None
    if channel is None:
        channel = interaction.channel
    if not isinstance(channel, discord.TextChannel | discord.Thread):
        raise GameError("The game needs a text channel. Set one with `/imposter-admin channel`.")
    missing = missing_channel_permissions(channel, interaction.guild.me)
    if missing:
        raise GameError(
            f"I can't post in {channel.mention} (missing: {', '.join(missing)}). Give my role "
            "access there, or move the game with `/imposter-admin channel`."
        )
    return channel


def permission_warning(interaction: discord.Interaction) -> str:
    """A note for the organizer when the bot can't actually post in this channel."""
    me = interaction.guild.me if interaction.guild else None
    missing = missing_channel_permissions(interaction.channel, me)
    if not missing:
        return ""
    return (
        "\n\n⚠️ **I can't post in this channel.** I'm missing: " + ", ".join(missing) + ". "
        "Round announcements, join notices, and votes go here, so give my role access to this "
        "channel (or create the game somewhere I can post)."
    )


def format_settings(game) -> str:
    return (
        f"Imposters: {game['imposters']}\n"
        f"Rounds: {game['max_rounds']} (the imposters win if any are still uncaught after that)\n"
        f"Imposters get the prompt's clue: {'yes' if game['imposter_hint'] else 'no'}"
    )


# -- lifecycle -------------------------------------------------------------------------------


@admin.command(name="create", description="Open sign-ups for a new game")
@app_commands.describe(
    name="Name of the game",
    imposters="How many imposters (default 1)",
    rounds="How many rounds the game lasts (default 5)",
    hint="Whether imposters get the prompt's clue (default yes)",
)
async def create(
    interaction: discord.Interaction,
    name: str = "Imposter",
    imposters: app_commands.Range[int, 1, 10] | None = None,
    rounds: app_commands.Range[int, 1, 50] | None = None,
    hint: bool | None = None,
):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    if await repo.get_current_game(interaction.guild_id):
        raise GameError("There's already a game running. End it first with `/imposter-admin end`.")
    game = await repo.create_game(interaction.guild_id, name[:100], interaction.user.id)
    await repo.set_channel(game["id"], interaction.channel_id)
    changes = {k: v for k, v in {"imposters": imposters, "max_rounds": rounds}.items() if v}
    if hint is not None:
        changes["imposter_hint"] = int(hint)
    if changes:
        await repo.update_settings(game["id"], **changes)
    game = await repo.get_game(game["id"])
    # The intro is the public reply, so it lands in the channel the organizer chose.
    await interaction.response.send_message(intro_text(game), allowed_mentions=NO_MENTIONS)
    await respond(
        interaction,
        f"**Organizer notes**\n"
        f"• {game['imposters']} imposter{'s' if game['imposters'] != 1 else ''}, "
        f"{game['max_rounds']} rounds, imposters "
        f"{'get' if game['imposter_hint'] else 'do not get'} the prompt's clue. Change with "
        "`/imposter-admin settings`.\n"
        f"• The game is played in this channel; move it with `/imposter-admin channel`. Start "
        f"with `/imposter-admin start` once at least {required_players(interaction.client, game)} "
        "players have joined.\n"
        "• Prompts are random from the built-in list. To use your own for the next round: "
        "`/imposter-admin prompt text:<the prompt> hint:<optional clue for the imposters>`. "
        "Queue one now and round 1 will use it.\n"
        "• When everyone has answered a prompt, open the vote with `/imposter-admin vote`."
        + permission_warning(interaction),
    )
    if interaction.channel_id != await interaction.client.guild_settings.get_announce_channel(  # type: ignore[attr-defined]
        game["guild_id"]
    ):
        await announce(
            interaction.client,
            game["guild_id"],
            f"🎭 A new {title(game)} is open for sign-ups in "
            f"<#{interaction.channel_id}>! Use `/imposter join` to play.",
        )


@admin.command(name="start", description="Pick the imposters and send round 1 to the game channel")
async def start(interaction: discord.Interaction):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id, "open")
    channel = await game_channel_or_here(interaction, game)
    players = await repo.list_players(game["id"])
    need = required_players(interaction.client, game)
    if len(players) < need:
        raise GameError(
            f"Need at least {need} players for {game['imposters']} imposter"
            f"{'s' if game['imposters'] != 1 else ''}; {len(players)} have joined."
        )
    if len(players) >= MAX_VOTE_OPTIONS:
        raise GameError(f"The vote menu holds at most {MAX_VOTE_OPTIONS - 1} players.")
    await interaction.response.defer(ephemeral=True)
    imposters = choose_imposters([p["id"] for p in players], game["imposters"], RNG)
    await repo.start_game(game["id"], channel.id, imposters)
    game = await repo.get_game(game["id"])
    failed = await flow.start_round(interaction.client, game)
    text = f"{title(game)} has started with {len(players)} players in {channel.mention}."
    if failed:
        text += "\n⚠️ Couldn't DM: " + ", ".join(failed)
    await respond(interaction, text)


@admin.command(name="channel", description="Move the game to another channel")
@app_commands.describe(channel="Where round announcements, join notices, and votes are posted")
async def channel_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    missing = missing_channel_permissions(channel, interaction.guild.me)
    if missing:
        raise GameError(f"I can't post in {channel.mention}. Missing: {', '.join(missing)}.")
    await repo.set_channel(game["id"], channel.id)
    await respond(interaction, f"{title(game)} is now played in {channel.mention}.")


@admin.command(
    name="nudge", description="Re-post this round's announcement and re-DM everyone their prompt"
)
async def nudge(interaction: discord.Interaction):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id, "active")
    if game["phase"] != "discussion":
        raise GameError("Close the vote first; the announcement would tell people to answer.")
    rnd = await repo.get_current_round(game["id"])
    if rnd is None:
        raise GameError("No round has started yet.")
    await interaction.response.defer(ephemeral=True)
    failed = await flow.deal_round(interaction.client, game, rnd)
    text = f"Round {rnd['number']} re-sent to everyone still in the game."
    if failed:
        text += "\n⚠️ Couldn't DM: " + ", ".join(failed)
    await respond(interaction, text)


@admin.command(name="vote", description="Open the vote for this round")
async def vote(interaction: discord.Interaction):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id, "active")
    if game["phase"] == "voting":
        raise GameError("Voting is already open. Close it with `/imposter-admin close`.")
    rnd = await repo.get_current_round(game["id"])
    if rnd is None:
        raise GameError("No round has started yet.")
    await interaction.response.defer(ephemeral=True)
    message = await flow.open_vote(interaction.client, game, rnd)
    if message is None:
        raise GameError("I couldn't post the vote in the game channel. Check my permissions there.")
    await respond(interaction, f"Voting is open: {message.jump_url}")


@admin.command(name="close", description="Close the vote now and resolve the round")
async def close(interaction: discord.Interaction):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id, "active")
    if game["phase"] != "voting":
        raise GameError("There's no vote open right now.")
    rnd = await repo.get_current_round(game["id"])
    await interaction.response.defer(ephemeral=True)
    if await flow.close_vote(interaction.client, game, rnd):
        await respond(interaction, "Vote closed.")
    else:
        await respond(interaction, "That vote had just closed on its own.")


@admin.command(
    name="reroll", description="Replace this round's prompt with a new one and re-DM everyone"
)
async def reroll(interaction: discord.Interaction):
    await is_game_admin(interaction)
    game_ctx = ctx(interaction)
    repo = game_ctx.repo
    game = await require_game(repo, interaction.guild_id, "active")
    if game["phase"] != "discussion":
        raise GameError("Close the vote first.")
    rnd = await repo.get_current_round(game["id"])
    if rnd is None:
        raise GameError("No round has started yet.")
    await interaction.response.defer(ephemeral=True)
    prompt = flow.next_prompt(game, game_ctx.prompts, await repo.used_prompts(game["id"]))
    await repo.reroll_round(rnd["id"], prompt.text, prompt.hint)
    if game["next_prompt"]:
        await repo.set_next_prompt(game["id"], None, None)
    rnd = await repo.get_round(rnd["id"])
    failed = await flow.deal_round(interaction.client, game, rnd)
    text = f"Round {rnd['number']} now uses a new prompt: *{rnd['prompt']}*"
    if failed:
        text += "\n⚠️ Couldn't DM: " + ", ".join(failed)
    await respond(interaction, text)


@admin.command(name="prompt", description="Set a custom prompt for the next round")
@app_commands.describe(
    text="The prompt everyone except the imposters will get",
    hint="Optional clue for the imposters, e.g. 'It's about food'",
    clear="Forget the custom prompt and go back to random ones",
)
async def prompt(
    interaction: discord.Interaction,
    text: str | None = None,
    hint: str | None = None,
    clear: bool = False,
):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    if clear or not text:
        if not clear:
            current = (
                f"Next prompt: *{game['next_prompt']}*"
                if game["next_prompt"]
                else "No custom prompt set."
            )
            await respond(interaction, current)
            return
        await repo.set_next_prompt(game["id"], None, None)
        await respond(interaction, "Custom prompt cleared; the next round uses a random one.")
        return
    await repo.set_next_prompt(game["id"], text.strip()[:500], hint.strip()[:200] if hint else None)
    await respond(
        interaction,
        f"The next round will use: *{text.strip()[:500]}*"
        + (f" (imposter clue: *{hint.strip()[:200]}*)" if hint else ""),
    )


@admin.command(name="end", description="End the current game")
async def end(interaction: discord.Interaction):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    if game["status"] == "active":
        await flow.finish(interaction.client, game, None)
    else:
        await repo.finish_game(game["id"], None)
    await respond(interaction, f"{title(game)} has ended.")


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
        await respond(interaction, f"Removed {user.mention} from the game.")
        return
    await interaction.response.defer(ephemeral=True)
    await repo.eliminate(player["id"], game["round"], "removed")
    name = await display_name(interaction.client, game["guild_id"], user.id)
    await flow.post(interaction.client, game, f"💀 **{name}** was removed by an organizer.")
    await respond(interaction, f"Removed {user.mention} from the game.")
    await flow.after_departure(interaction.client, game)


# -- settings and visibility -----------------------------------------------------------------


@admin.command(name="settings", description="Show or change game settings")
@app_commands.describe(
    imposters="How many imposters (only before the game starts)",
    rounds="How many rounds the game lasts; imposters still uncaught after that win",
    hint="Whether imposters get the prompt's clue",
)
async def settings(
    interaction: discord.Interaction,
    imposters: app_commands.Range[int, 1, 10] | None = None,
    rounds: app_commands.Range[int, 1, 50] | None = None,
    hint: bool | None = None,
):
    await is_game_admin(interaction)
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    if imposters is not None and game["status"] != "open":
        raise GameError("The number of imposters can only be changed before the game starts.")
    if rounds is not None and game["status"] == "active" and rounds < game["round"]:
        raise GameError(f"The game is already in round {game['round']}.")
    changes = {
        "imposters": imposters,
        "max_rounds": rounds,
        "imposter_hint": None if hint is None else int(hint),
    }
    changes = {k: v for k, v in changes.items() if v is not None and k in SETTING_COLUMNS}
    if changes:
        await repo.update_settings(game["id"], **changes)
        game = await repo.get_game(game["id"])
    await respond(interaction, ("Updated.\n" if changes else "") + format_settings(game))


@admin.command(
    name="status", description="Who's in, who the imposters are, and how the vote stands"
)
async def status(interaction: discord.Interaction):
    await is_game_admin(interaction)
    bot = interaction.client
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    players = await repo.list_players(game["id"])
    names = await flow.names_by_id(bot, game, players)
    alive = [p for p in players if p["alive"]]
    out = [p for p in players if not p["alive"]]
    lines = [f"{title(game)} is **{game['status']}**: {len(alive)} in, {len(out)} out."]
    if game["status"] == "open":
        lines.append("Signed up: " + (", ".join(names[p["id"]] for p in players) or "nobody"))
    else:
        rnd = await repo.get_current_round(game["id"])
        lines.append(f"Round {game['round']}, phase **{game['phase']}**.")
        if rnd:
            lines.append(
                f"Prompt: *{rnd['prompt']}*" + (f" (clue: *{rnd['hint']}*)" if rnd["hint"] else "")
            )
        imposters = [names[p["id"]] for p in players if p["imposter"]]
        lines.append("🕵️ Imposters: " + ", ".join(imposters))
        lines.append("👥 In: " + ", ".join(names[p["id"]] for p in alive))
        if out:
            lines.append("💀 Out: " + ", ".join(f"{names[p['id']]} ({p['cause']})" for p in out))
        if game["phase"] == "voting" and rnd:
            votes = await repo.list_votes(rnd["id"])
            voted = {v["voter_id"] for v in votes}
            waiting = [names[p["id"]] for p in alive if p["id"] not in voted]
            lines.append(
                f"🗳️ {len(voted)}/{len(alive)} voted."
                + (" Waiting on: " + ", ".join(waiting) if waiting else "")
            )
        if game["next_prompt"]:
            lines.append(f"Next prompt (custom): *{game['next_prompt']}*")
    await respond(interaction, "\n".join(lines)[:1990])

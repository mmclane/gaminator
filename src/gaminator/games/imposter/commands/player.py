"""Player commands: /imposter join, leave, status, rules, players, resend."""

from __future__ import annotations

import discord

from ....core.messaging import display_name, dm
from ....core.repo import AlreadyJoined
from ....core.util import GameError, require_game, require_player, respond, signup_text
from ..services import game as flow
from ..services.game import DEFAULT_GAME, required_players, rules_text, title
from .group import ctx, imposter


@imposter.command(name="join", description="Join the Imposter game")
async def join(interaction: discord.Interaction):
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    if game["status"] != "open":
        raise GameError("The game has already started. Catch the next one!")
    try:
        await repo.add_player(game["id"], interaction.user.id)
    except AlreadyJoined:
        raise GameError("You're already in.") from None
    count = len(await repo.list_players(game["id"]))
    dm_ok = await dm(
        interaction.client,
        interaction.user.id,
        "# 🎭 Welcome to Imposter\n"
        f"You've joined {title(game)}. There's nothing to do yet: just wait for the organizer "
        "to start the game. Your first prompt will arrive here as a DM when it does.\n\n"
        + rules_text(game, footer=False),
    )
    text = f"You're in {title(game)} ({count} signed up)."
    if not dm_ok:
        text += (
            "\n⚠️ I couldn't DM you. Allow direct messages from server members, or you'll have "
            "to rely on `/imposter status` to see your prompt."
        )
    await respond(interaction, text)
    name = await display_name(interaction.client, game["guild_id"], interaction.user.id)
    await flow.post(
        interaction.client,
        game,
        f"🙋 **{name}** joined {title(game)}. {signup_text(count, required_players(interaction.client, game))}",
    )


@imposter.command(name="leave", description="Leave the game")
async def leave(interaction: discord.Interaction):
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    player = await require_player(repo, game, interaction.user.id)
    if game["status"] == "open":
        await repo.delete_player(player["id"])
        await respond(interaction, "You've left the game.")
        count = len(await repo.list_players(game["id"]))
        name = await display_name(interaction.client, game["guild_id"], interaction.user.id)
        await flow.post(
            interaction.client,
            game,
            f"🚪 **{name}** left {title(game)}. {signup_text(count, required_players(interaction.client, game))}",
        )
        return
    await interaction.response.defer(ephemeral=True)
    await repo.eliminate(player["id"], game["round"], "left")
    name = await display_name(interaction.client, game["guild_id"], player["user_id"])
    await flow.post(interaction.client, game, f"🚪 **{name}** left the game.")
    await respond(interaction, "You've left the game.")
    await flow.after_departure(interaction.client, game)


@imposter.command(name="status", description="Your role, your prompt, and where the game stands")
async def status(interaction: discord.Interaction):
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    player = await require_player(repo, game, interaction.user.id, alive=False)
    players = await repo.list_players(game["id"])
    alive = [p for p in players if p["alive"]]
    if game["status"] == "open":
        await respond(interaction, f"Sign-ups are open; {len(players)} players so far.")
        return
    lines = [f"{title(game)}, round {game['round']} ({game['phase']})."]
    if not player["alive"]:
        lines.append(f"You're out ({player['cause']}). {len(alive)} players remain.")
        await respond(interaction, "\n".join(lines))
        return
    rnd = await repo.get_current_round(game["id"])
    if player["imposter"]:
        lines.append("🕵️ You are **the imposter**.")
        if rnd and rnd["hint"] and game["imposter_hint"]:
            lines.append(f"Your clue: *{rnd['hint']}*")
        fellows = [p for p in alive if p["imposter"] and p["id"] != player["id"]]
        if fellows:
            names = [
                await display_name(interaction.client, game["guild_id"], p["user_id"])
                for p in fellows
            ]
            lines.append("Fellow imposters: " + ", ".join(f"**{n}**" for n in names))
    elif rnd:
        lines.append(f"🎭 Your prompt: **{rnd['prompt']}**")
    if game["phase"] == "voting" and rnd:
        vote = await repo.get_vote(rnd["id"], player["id"])
        if vote is None:
            lines.append("🗳️ Voting is open and you haven't voted yet.")
        elif vote["target_id"] is None:
            lines.append("🗳️ You voted for no accusation.")
        else:
            target = await repo.get_player_by_id(vote["target_id"])
            tname = (
                await display_name(interaction.client, game["guild_id"], target["user_id"])
                if target
                else "?"
            )
            lines.append(f"🗳️ You voted for **{tname}**.")
    lines.append(f"👥 Players remaining: {len(alive)}")
    await respond(interaction, "\n".join(lines))


@imposter.command(name="rules", description="How the Imposter game works")
async def rules(interaction: discord.Interaction):
    game = await ctx(interaction).repo.get_current_game(interaction.guild_id)
    await respond(interaction, rules_text(game if game is not None else DEFAULT_GAME))


@imposter.command(name="players", description="Who is still in the game")
async def players(interaction: discord.Interaction):
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id)
    await interaction.response.defer(ephemeral=True)
    alive = await repo.list_players(game["id"], alive_only=True)
    names = sorted(
        [await display_name(interaction.client, game["guild_id"], p["user_id"]) for p in alive],
        key=str.lower,
    )
    label = "Signed up" if game["status"] == "open" else "Still in"
    await respond(interaction, f"**{label} ({len(names)}):** " + (", ".join(names) or "nobody"))


@imposter.command(name="resend", description="DM me this round's prompt again")
async def resend(interaction: discord.Interaction):
    repo = ctx(interaction).repo
    game = await require_game(repo, interaction.guild_id, "active")
    player = await require_player(repo, game, interaction.user.id)
    rnd = await repo.get_current_round(game["id"])
    if rnd is None:
        raise GameError("No round has started yet.")
    ok = await flow.send_prompt(interaction.client, game, rnd, player)
    await respond(
        interaction, "Sent, check your DMs." if ok else "I couldn't DM you. Use `/imposter status`."
    )

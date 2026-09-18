"""Game flow that touches Discord: dealing prompts, opening and closing votes, ending the game."""

from __future__ import annotations

import logging
import random

import discord

from ....core.messaging import NO_MENTIONS, announce, display_name, dm
from ..context import context
from .prompts import Prompt, pick_prompt
from .rules import CREW, IMPOSTERS, Tally, min_players, outcome, tally

log = logging.getLogger(__name__)
RNG = random.SystemRandom()
MAX_VOTE_OPTIONS = 25  # Discord's limit on select-menu options, including "No accusation"

DEFAULT_GAME = {"imposters": 1, "max_rounds": 5, "imposter_hint": 1}


def required_players(bot, game) -> int:
    """Players needed to start, honouring the MIN_PLAYERS testing override."""
    return min_players(game["imposters"], getattr(bot.settings, "min_players", None))


def title(game) -> str:
    """How messages refer to the game: 'Imposter game **Friday Night**'."""
    return f"Imposter game **{game['name']}**"


# -- text ------------------------------------------------------------------------------------


def rules_text(game, footer: bool = True) -> str:
    """The rules. ``footer`` adds the slash-command hint; leave it out of DMs, where it can't run."""
    n = game["imposters"]
    who = "one player is" if n == 1 else f"{n} players are"
    rounds = game["max_rounds"]
    clue = "given only a vague clue" if game["imposter_hint"] else "given no clue at all"
    return (
        "**How to play Imposter**\n"
        f"Each round everyone gets the same secret prompt by DM, except {who} secretly told "
        f"they're **the imposter** and {clue}. Answer the prompt in the game "
        "channel and watch for whoever is bluffing.\n\n"
        "**Vote**: when everyone has answered, the organizer opens a vote. Pick the player you "
        'think is the imposter from the dropdown, or pick "No accusation" to abstain. Votes are '
        "secret until the "
        "vote closes, which happens as soon as everyone has voted.\n"
        "**Right**: the imposter is caught and out of the game. Catch them all and the crew "
        "wins.\n"
        "**Wrong**: nobody is eliminated. The accused stays in, and the crew has lost a round. A "
        "tie means nobody is accused.\n"
        f"**Imposters win** if any of them are still uncaught after {rounds} round"
        f"{'s' if rounds != 1 else ''}."
        + (
            "\n\nUse `/imposter status` in the server to see your prompt, your role, and where "
            "the game stands."
            if footer
            else ""
        )
    )


def intro_text(game) -> str:
    """Public message posted where the game is created: how to join and how it works."""
    n, rounds = game["imposters"], game["max_rounds"]
    imposters = "one imposter" if n == 1 else f"{n} imposters"
    clue = "only gets a vague clue" if game["imposter_hint"] else "gets no clue at all"
    return (
        f"🎭 {title(game)} is open for sign-ups! Join with `/imposter join`.\n\n"
        f"**How it works**: {rounds} round{'s' if rounds != 1 else ''}, {imposters}. Each round "
        f"everyone gets the same secret prompt by DM, except the imposter, who {clue}. Answer "
        "the prompt here, watch for whoever is bluffing, then vote. Catch the "
        f"imposter{'s' if n != 1 else ''} before the rounds run out and the crew wins. Nobody "
        "innocent is ever eliminated.\n\n"
        "`/imposter rules` has the full rules."
    )


def crew_dm_text(game, rnd, channel_mention: str) -> str:
    return (
        f"# 🎭 {title(game)}, round {rnd['number']}\n"
        f"Your prompt: **{rnd['prompt']}**\n\n"
        f"Everyone except the imposter got this same prompt. Answer it in {channel_mention}, "
        "then watch for whoever seems to be guessing."
    )


def imposter_dm_text(game, rnd, fellow_names: list[str], channel_mention: str) -> str:
    hint = f"\nYour only clue: *{rnd['hint']}*" if rnd["hint"] and game["imposter_hint"] else ""
    fellows = (
        "\nYour fellow imposter"
        + ("s are " if len(fellow_names) > 1 else " is ")
        + ", ".join(f"**{n}**" for n in fellow_names)
        + "."
        if fellow_names
        else ""
    )
    return (
        f"# 🕵️ {title(game)}, round {rnd['number']}\n"
        "You are **the imposter**. Everyone else got a prompt that you don't know."
        f"{hint}{fellows}\n\n"
        f"Read the others' answers in {channel_mention}, blend in, and don't get voted out."
    )


def round_text(game, rnd, alive: int) -> str:
    return (
        f"🎭 **Round {rnd['number']} of {game['max_rounds']}** in {title(game)} has begun. "
        f"Check your DMs for your prompt, then answer it here. {alive} players are in.\n"
        "When everyone has answered, the organizer opens the vote with `/imposter-admin vote`."
    )


def vote_text(rnd, voted: int, total: int) -> str:
    return (
        f"🗳️ **Round {rnd['number']} vote**: who is the imposter? Pick from the menu below, or "
        f'pick "No accusation" to abstain. Votes are secret until the vote closes.\n'
        f"{voted}/{total} votes in."
    )


def tally_text(t: Tally, names: dict[int | None, str]) -> str:
    if not t.counts:
        return "No votes were cast."
    ordered = sorted(t.counts.items(), key=lambda kv: (-kv[1], names.get(kv[0], "")))
    return "Results: " + ", ".join(f"{names.get(k, 'No accusation')} {n}" for k, n in ordered)


def result_text(game, rnd, t: Tally, ejected, ejected_name: str | None, imposters_left: int) -> str:
    if t.result == "ejected" and ejected is not None:
        votes = t.counts.get(ejected["id"], 0)
        if ejected["imposter"]:
            left = (
                f" {imposters_left} imposter{'s' if imposters_left != 1 else ''} remain."
                if imposters_left
                else ""
            )
            line = (
                f"🚨 The group accused **{ejected_name}** ({votes} vote{'s' if votes != 1 else ''}) "
                f"and they **were an imposter!** They're out.{left}"
            )
        else:
            line = (
                f"😬 The group accused **{ejected_name}** ({votes} vote{'s' if votes != 1 else ''}), "
                "but they're **not** an imposter. Nobody is eliminated; the imposters are still "
                "among you."
            )
    elif t.result == "tie":
        line = "🤝 The vote was tied, so nobody was accused."
    elif t.result == "nobody":
        line = "🤷 The group made no accusation this round."
    else:
        line = "😶 Nobody voted, so nobody was accused."
    rounds_left = game["max_rounds"] - rnd["number"]
    if rounds_left > 0 and imposters_left:
        line += f" {rounds_left} round{'s' if rounds_left != 1 else ''} left."
    return f"{line}\nThe prompt was: *{rnd['prompt']}*"


def winner_text(game, winner: str, imposter_names: list[str], reason: str) -> str:
    who = ", ".join(f"**{n}**" for n in imposter_names) or "nobody"
    label = "imposter was" if len(imposter_names) == 1 else "imposters were"
    if winner == CREW:
        return f"🏆 The crew wins {title(game)}! The {label} {who}."
    return f"😈 The imposters win {title(game)}! {reason} The {label} {who}."


# -- helpers ---------------------------------------------------------------------------------


async def game_channel(bot, game):
    if not game["channel_id"]:
        return None
    channel = bot.get_channel(game["channel_id"])
    if channel is None:
        try:
            channel = await bot.fetch_channel(game["channel_id"])
        except discord.HTTPException:
            return None
    return channel


async def post(bot, game, text: str, **kwargs):
    """Post in the game channel; fall back to the shared announce channel."""
    channel = await game_channel(bot, game)
    if channel is None:
        await announce(bot, game["guild_id"], text)
        return None
    try:
        return await channel.send(text, allowed_mentions=NO_MENTIONS, **kwargs)
    except discord.HTTPException as e:
        log.warning("imposter game %s: could not post in %s: %s", game["id"], channel.id, e)
        return None


def channel_mention(game) -> str:
    return f"<#{game['channel_id']}>" if game["channel_id"] else "the game channel"


async def names_by_id(bot, game, players) -> dict[int, str]:
    return {p["id"]: await display_name(bot, game["guild_id"], p["user_id"]) for p in players}


# -- rounds ----------------------------------------------------------------------------------


def next_prompt(game, prompts: list[Prompt], used: set[str]) -> Prompt:
    if game["next_prompt"]:
        return Prompt(game["next_prompt"], game["next_hint"])
    return pick_prompt(prompts, RNG, exclude=used)


async def send_prompt(bot, game, rnd, player, players=None) -> bool:
    """DM one player their prompt (or their imposter briefing). Returns False if DMs failed."""
    if player["imposter"]:
        players = players or await context(bot).repo.list_players(game["id"], alive_only=True)
        fellows = [
            await display_name(bot, game["guild_id"], p["user_id"])
            for p in players
            if p["imposter"] and p["alive"] and p["id"] != player["id"]
        ]
        text = imposter_dm_text(game, rnd, fellows, channel_mention(game))
    else:
        text = crew_dm_text(game, rnd, channel_mention(game))
    return await dm(bot, player["user_id"], text)


async def deal_round(bot, game, rnd) -> list[str]:
    """DM every living player and announce the round. Returns names whose DMs failed."""
    repo = context(bot).repo
    players = await repo.list_players(game["id"], alive_only=True)
    failed = []
    for p in players:
        if not await send_prompt(bot, game, rnd, p, players):
            failed.append(await display_name(bot, game["guild_id"], p["user_id"]))
    text = round_text(game, rnd, len(players))
    if failed:
        text += "\n⚠️ I couldn't DM " + ", ".join(failed) + ". Use `/imposter resend`."
    await post(bot, game, text)
    return failed


async def start_round(bot, game) -> list[str]:
    ctx = context(bot)
    prompt = next_prompt(game, ctx.prompts, await ctx.repo.used_prompts(game["id"]))
    rnd = await ctx.repo.begin_round(game["id"], prompt.text, prompt.hint)
    game = await ctx.repo.get_game(game["id"])
    return await deal_round(bot, game, rnd)


# -- voting ----------------------------------------------------------------------------------


def vote_options(players, names: dict[int, str]) -> list[discord.SelectOption]:
    options = [
        discord.SelectOption(label=names[p["id"]][:100], value=str(p["id"])) for p in players
    ]
    options.append(
        discord.SelectOption(
            label="No accusation",
            value="0",
            emoji="🤷",
            description="Abstain this round",
        )
    )
    return options


async def open_vote(bot, game, rnd) -> discord.Message | None:
    from ..vote import vote_view

    repo = context(bot).repo
    players = await repo.list_players(game["id"], alive_only=True)
    names = await names_by_id(bot, game, players)
    view = vote_view(rnd["id"], vote_options(players, names))
    message = await post(bot, game, vote_text(rnd, 0, len(players)), view=view)
    if message is None:
        return None
    await repo.open_voting(rnd["id"], message.channel.id, message.id)
    return message


async def refresh_vote_message(bot, game, rnd, message: discord.Message | None = None) -> None:
    repo = context(bot).repo
    voted = await repo.living_vote_count(rnd["id"])
    total = len(await repo.list_players(game["id"], alive_only=True))
    message = message or await fetch_vote_message(bot, rnd)
    if message is None:
        return
    try:
        await message.edit(content=vote_text(rnd, voted, total))
    except discord.HTTPException:
        pass


async def fetch_vote_message(bot, rnd) -> discord.Message | None:
    if not rnd["vote_channel_id"] or not rnd["vote_message_id"]:
        return None
    channel = bot.get_channel(rnd["vote_channel_id"])
    if channel is None:
        try:
            channel = await bot.fetch_channel(rnd["vote_channel_id"])
        except discord.HTTPException:
            return None
    try:
        return await channel.fetch_message(rnd["vote_message_id"])
    except discord.HTTPException:
        return None


async def everyone_voted(bot, game, rnd) -> bool:
    repo = context(bot).repo
    total = len(await repo.list_players(game["id"], alive_only=True))
    return total > 0 and await repo.living_vote_count(rnd["id"]) >= total


async def close_vote(bot, game, rnd) -> bool:
    """Resolve the vote, apply the result, and either end the game or start the next round.

    Returns False when the round was already closed (two triggers raced).
    """
    repo = context(bot).repo
    players = await repo.list_players(game["id"])
    alive_ids = {p["id"] for p in players if p["alive"]}
    votes = [
        v["target_id"]
        for v in await repo.list_votes(rnd["id"])
        if v["voter_id"] in alive_ids and (v["target_id"] is None or v["target_id"] in alive_ids)
    ]
    t = tally(votes)
    ejected = next((p for p in players if p["id"] == t.ejected), None) if t.ejected else None
    result = t.result
    if ejected is not None:
        result = "imposter" if ejected["imposter"] else "innocent"
    if not await repo.close_round(rnd["id"], ejected["id"] if ejected else None, result):
        return False
    if ejected is not None and ejected["imposter"]:
        await repo.eliminate(ejected["id"], rnd["number"], "caught")

    names = await names_by_id(bot, game, players)
    message = await fetch_vote_message(bot, rnd)
    if message is not None:
        try:
            await message.edit(
                content=f"🗳️ **Round {rnd['number']} vote** is closed. {tally_text(t, names)}",
                view=None,
            )
        except discord.HTTPException:
            pass
    crew_alive, imposters_alive = await repo.alive_counts(game["id"])
    ejected_name = names.get(ejected["id"]) if ejected else None
    await post(bot, game, result_text(game, rnd, t, ejected, ejected_name, imposters_alive))
    if ejected is not None and ejected["imposter"]:
        await dm(bot, ejected["user_id"], "🚨 You've been found out. You're out of the game.")

    winner = outcome(crew_alive, imposters_alive, rnd["number"], game["max_rounds"])
    if winner:
        reason = (
            "Nobody was left to catch them."
            if crew_alive == 0
            else f"They survived {game['max_rounds']} round{'s' if game['max_rounds'] != 1 else ''}."
        )
        await finish(bot, game, winner, reason)
    else:
        fresh = await repo.get_game(game["id"])
        await start_round(bot, fresh)
    return True


async def check_outcome(bot, game) -> bool:
    """After someone leaves mid-game: end the game if that decided it. True when it ended."""
    repo = context(bot).repo
    current = await repo.get_game(game["id"])
    if current is None or current["status"] != "active":
        return False
    crew_alive, imposters_alive = await repo.alive_counts(game["id"])
    winner = outcome(crew_alive, imposters_alive, 0, current["max_rounds"])
    if winner is None:
        return False
    reason = "Nobody was left to catch them." if winner == IMPOSTERS else ""
    await finish(bot, current, winner, reason)
    return True


async def finish(bot, game, winner: str | None, reason: str = "") -> None:
    repo = context(bot).repo
    await repo.finish_game(game["id"], winner)
    players = await repo.list_players(game["id"])
    imposter_names = [
        await display_name(bot, game["guild_id"], p["user_id"]) for p in players if p["imposter"]
    ]
    if winner:
        text = winner_text(game, winner, imposter_names, reason)
    else:
        who = ", ".join(f"**{n}**" for n in imposter_names) or "nobody"
        text = f"🏁 {title(game)} was ended by an organizer. The imposters were: {who}."
    await post(bot, game, text)
    if game["channel_id"] != await bot.guild_settings.get_announce_channel(game["guild_id"]):
        await announce(bot, game["guild_id"], text)


async def after_departure(bot, game) -> None:
    """Someone left or was removed mid-game: end the game if that decides it, else keep the
    vote moving (the departed player's vote no longer counts, so everyone may now have voted)."""
    if await check_outcome(bot, game):
        return
    repo = context(bot).repo
    current = await repo.get_game(game["id"])
    if current is None or current["phase"] != "voting":
        return
    rnd = await repo.get_current_round(game["id"])
    if rnd is None or rnd["closed_at"]:
        return
    if await everyone_voted(bot, current, rnd):
        await close_vote(bot, current, rnd)
    else:
        await refresh_vote_message(bot, current, rnd)

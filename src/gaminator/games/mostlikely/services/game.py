"""Most Likely To text and the game flow that touches Discord."""

from __future__ import annotations

import logging
import random
from datetime import timedelta

import discord

from ....core.messaging import NETWORK_ERRORS, NO_MENTIONS, announce, display_name
from ....core.repo import AlreadyJoined
from ....core.util import GameError, is_game_admin, now, now_iso, parse_iso, signup_text
from ..context import context
from .prompts import pick_prompt
from .rules import (
    ClosedPrompt,
    Standing,
    Tally,
    game_winners,
    leaderboard_rows,
    min_players,
    next_host,
    nobody_voted_streak,
    recap,
    recent_prompt_wins,
    standings,
    tally,
    too_few_to_continue,
)

log = logging.getLogger(__name__)
RNG = random.SystemRandom()
DEFAULT_ROUNDS = 10
DEFAULT_GAME = {"status": "open", "max_rounds": DEFAULT_ROUNDS}
GAME_OVER = "That game is over."
LEADERBOARD_HINT = "All-time standings: `/gaminator mostlikely leaderboard`"
NAME_LIMIT = 32
GAP = "_ _\n"
STALL_GRACE_SECONDS = 30


class ChannelGone(Exception):
    """The game channel was deleted, or the bot can no longer see or post in it."""


def required_players(bot) -> int:
    return min_players(bot.settings.min_players)


def vote_seconds(bot) -> int:
    return bot.settings.mostlikely_vote_seconds


def title(game) -> str:
    return f"Most Likely To game **{game['name']}**"


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def duration_text(seconds: int) -> str:
    if seconds % 60 == 0:
        return plural(seconds // 60, "minute")
    return plural(seconds, "second")


def how_it_works(game, seconds: int = 120) -> str:
    if game["status"] == "open":
        length = f"A game runs {DEFAULT_ROUNDS} prompts unless the host picks another number."
    else:
        length = f"This game runs {plural(game['max_rounds'], 'prompt')}."
    return (
        "• Each prompt asks who in the group is most likely to do something. Pick a player from "
        "the menu. You can vote for yourself, and change your vote until the prompt closes.\n"
        f"• A prompt closes when everyone has voted or after {duration_text(seconds)}. Votes are "
        "secret: only the totals are shown.\n"
        f"• {length} Whoever wins the most prompts wins the game.\n"
        "• Join or leave at any time with `/gaminator mostlikely join` and "
        "`/gaminator mostlikely leave`. If you join during a prompt, you're in from the next one.\n"
        "• The game ends early if nobody votes on 2 prompts in a row, or if fewer than 2 players "
        "are left."
    )


def rules_text(game, seconds: int = 120) -> str:
    return "## 🤔 How Most Likely To works\n" + how_it_works(game, seconds)


def lobby_embed(game, names: list[str], host_name: str, need: int, seconds: int = 120):
    """The lobby card: state, host, player count, and the players in join order."""
    count = len(names)
    host = discord.utils.escape_markdown(host_name[:NAME_LIMIT])
    if game["status"] == "open":
        state, colour = "sign-ups open", discord.Colour.blurple()
        more = need - count
        progress = f"{more} more needed to start." if more > 0 else "Enough to start!"
        lines = [f"Host: **{host}**", f"{plural(count, 'player')}. {progress}"]
    elif game["status"] == "active":
        state, colour = "in progress", discord.Colour.green()
        lines = [f"Host: **{host}**", f"{plural(count, 'player')}."]
    else:
        state = "finished" if game["started_at"] else "cancelled"
        colour = discord.Colour.light_grey()
        lines = [f"{plural(count, 'player')} at the end."]
    icon = "🏁" if game["status"] == "finished" else "🤔"
    roster = [
        f"{n}. {discord.utils.escape_markdown(name[:NAME_LIMIT])}"
        for n, name in enumerate(names, start=1)
    ]
    embed = discord.Embed(
        title=f"{icon} {game['name']}: {state}"[:256],
        description="\n".join(lines + [""] + roster).strip(),
        colour=colour,
    )
    embed.add_field(name="How it works", value=how_it_works(game, seconds), inline=False)
    return embed


def prompt_line(game, rnd) -> str:
    return (
        f"{GAP}🤔 **Prompt {rnd['number']} of {game['max_rounds']}**\n"
        f"## Most likely to {rnd['prompt']}"
    )


def vote_text(game, rnd, voted: int, eligible: int) -> str:
    deadline = parse_iso(rnd["deadline_at"])
    epoch = int(deadline.timestamp()) if deadline else 0
    return (
        f"{prompt_line(game, rnd)}\n"
        "Pick a player from the menu. You can vote for yourself, and change your vote until it "
        "closes.\n"
        f"{voted}/{eligible} votes in. Closes <t:{epoch}:R>."
    )


def closed_text(game, rnd) -> str:
    return f"{prompt_line(game, rnd)}\nVoting is closed."


def name_list(names: list[str]) -> str:
    bold = [f"**{n}**" for n in names]
    if len(bold) <= 1:
        return "".join(bold)
    return ", ".join(bold[:-1]) + " and " + bold[-1]


def reveal_text(game, rnd, t: Tally, names: dict[int, str]) -> str:
    head = f"📊 **Prompt {rnd['number']}**: Most likely to {rnd['prompt']}"
    if not t.leaders:
        return f"{head}\nNobody voted."
    leaders = [names.get(i, "?") for i in t.leaders]
    if len(leaders) == 1:
        verdict = f"**{leaders[0]}** with {plural(t.top, 'vote')}."
    else:
        verdict = f"a tie between {name_list(leaders)} with {plural(t.top, 'vote')} each."
    ordered = sorted(t.counts.items(), key=lambda kv: (-kv[1], names.get(kv[0], "?").lower()))
    results = ", ".join(f"{names.get(i, '?')} {n}" for i, n in ordered)
    return f"{head}\nIt's {verdict}\nResults: {results}"


def recap_text(
    game, wins: list[tuple[int, int]], names: dict[int, str], prompts_played: int, note: str = ""
) -> str:
    head = f"🏁 {title(game)} is over after {plural(prompts_played, 'prompt')}"
    head += f" ({note})." if note else "."
    winners = game_winners(wins)
    if not winners:
        return f"{head}\nNobody won a prompt.\n{LEADERBOARD_HINT}"
    top = wins[0][1]
    winner_names = [names.get(i, "?") for i in winners]
    if len(winners) == 1:
        lines = [head, f"🏆 **{winner_names[0]}** wins with {plural(top, 'prompt')}!"]
    else:
        lines = [head, f"🏆 {name_list(winner_names)} tie with {plural(top, 'prompt')} each!"]
    rest = [(i, n) for i, n in wins if i not in winners]
    if rest:
        lines.append("Also: " + ", ".join(f"{names.get(i, '?')} {n}" for i, n in rest))
    lines.append(LEADERBOARD_HINT)
    return "\n".join(lines)


def standing_text(standing: Standing) -> str:
    return (
        f"{plural(standing.games_won, 'game')} won, "
        f"{plural(standing.prompts_won, 'prompt')} won, {standing.games_played} played"
    )


def leaderboard_text(rows: list[tuple[int, Standing]], names: dict[int, str]) -> str:
    if not rows:
        return (
            "No finished Most Likely To games yet. Start one with `/gaminator mostlikely create`."
        )
    lines = ["🏆 **Most Likely To: all-time standings**"]
    previous = 0
    for rank, standing in rows:
        if rank != previous + 1:
            lines.append("")
        name = names.get(standing.user_id, "?")
        lines.append(f"`{rank}.` **{name}**: {standing_text(standing)}")
        previous = rank
    return "\n".join(lines)


def member_text(name: str, standing: Standing | None, recent: list[ClosedPrompt]) -> str:
    if standing is None:
        return f"**{name}** hasn't played a finished game yet."
    lines = [f"**{name}**: {standing_text(standing)}"]
    if recent:
        lines.append("Recent prompt wins:")
        for prompt in recent:
            closed = parse_iso(prompt.closed_at)
            stamp = f" (<t:{int(closed.timestamp())}:d>)" if closed else ""
            lines.append(f"• Most likely to {prompt.prompt}{stamp}")
    return "\n".join(lines)


def join_line(name: str, game, count: int, need: int, mid_prompt: bool) -> str:
    if game["status"] == "open":
        return f"🙋 **{name}** joined {title(game)}. {signup_text(count, need)}"
    if mid_prompt:
        return f"🙋 **{name}** joined and is in from the next prompt."
    return f"🙋 **{name}** joined."


def leave_line(name: str, game, count: int, need: int) -> str:
    if game["status"] == "open":
        return f"🚪 **{name}** left {title(game)}. {signup_text(count, need)}"
    return f"🚪 **{name}** left. {plural(count, 'player')} remain{'s' if count == 1 else ''}."


def host_line(name: str) -> str:
    return f"👑 **{name}** is now the host."


async def game_channel(bot, game):
    """The game channel, None on a passing error. Raises ChannelGone when it is out of reach."""
    channel = bot.get_channel(game["channel_id"])
    if channel is None:
        try:
            channel = await bot.fetch_channel(game["channel_id"])
        except (discord.Forbidden, discord.NotFound) as e:
            raise ChannelGone() from e
        except NETWORK_ERRORS:
            return None
    return channel


async def post(bot, game, text: str):
    """Post in the game channel; fall back to the shared announce channel."""
    try:
        channel = await game_channel(bot, game)
    except ChannelGone:
        channel = None
    if channel is None:
        await announce(bot, game["guild_id"], text)
        return None
    try:
        return await channel.send(text, allowed_mentions=NO_MENTIONS)
    except NETWORK_ERRORS as e:
        log.warning("mostlikely game %s: could not post in %s: %s", game["id"], channel.id, e)
        return None


async def post_prompt(bot, game, text: str, view) -> discord.Message | None:
    """Post a prompt with its menu, retrying once. Raises ChannelGone when the channel is lost."""
    for attempt in (1, 2):
        try:
            channel = await game_channel(bot, game)
            if channel is not None:
                return await channel.send(text, view=view, allowed_mentions=NO_MENTIONS)
        except (discord.Forbidden, discord.NotFound) as e:
            raise ChannelGone() from e
        except NETWORK_ERRORS as e:
            log.warning(
                "mostlikely game %s: could not post prompt (attempt %d): %s", game["id"], attempt, e
            )
    return None


async def names_by_id(bot, game, players) -> dict[int, str]:
    return {p["id"]: await display_name(bot, game["guild_id"], p["user_id"]) for p in players}


async def require_host_or_admin(interaction: discord.Interaction, game) -> None:
    """Pass for the game's host or a game admin. Raises NotGameAdmin for anyone else."""
    if interaction.user.id == game["host_id"]:
        return
    await is_game_admin(interaction)


def vote_options(roster, names: dict[int, str]) -> list[discord.SelectOption]:
    return [discord.SelectOption(label=names[p["id"]][:100], value=str(p["id"])) for p in roster]


async def fetch_message(bot, channel_id: int | None, message_id: int | None):
    if not channel_id or not message_id:
        return None
    channel = bot.get_channel(channel_id)
    try:
        if channel is None:
            channel = await bot.fetch_channel(channel_id)
        return await channel.fetch_message(message_id)
    except NETWORK_ERRORS:
        return None


async def start_round(bot, game) -> bool:
    """Post the next prompt. False when no prompt went up."""
    from ..vote import vote_view

    ctx = context(bot)
    repo = ctx.repo
    prompt = pick_prompt(ctx.prompts, RNG, await repo.used_prompts(game["id"]))
    deadline = (now() + timedelta(seconds=vote_seconds(bot))).isoformat(timespec="seconds")
    rnd = await repo.begin_round(game["id"], prompt, deadline)
    if rnd is None:
        return False
    game = await repo.get_game(game["id"])
    roster = await repo.round_roster(rnd["id"])
    names = await names_by_id(bot, game, roster)
    view = vote_view(rnd["id"], vote_options(roster, names))
    try:
        message = await post_prompt(bot, game, vote_text(game, rnd, 0, len(roster)), view)
    except ChannelGone:
        log.warning("mostlikely game %s: game channel is gone, ending the game", game["id"])
        await finish(bot, game, note="I couldn't post in the game channel")
        return False
    if message is None:
        log.warning("mostlikely game %s: could not post prompt %s", game["id"], rnd["id"])
        await repo.abandon_round(rnd["id"])
        return False
    await repo.set_vote_message(rnd["id"], message.channel.id, message.id)
    return True


async def refresh_vote_message(bot, game, rnd, message=None) -> None:
    voted, eligible = await context(bot).repo.vote_progress(rnd["id"])
    message = message or await fetch_message(bot, rnd["vote_channel_id"], rnd["vote_message_id"])
    if message is None:
        return
    try:
        await message.edit(content=vote_text(game, rnd, voted, eligible))
    except NETWORK_ERRORS:
        pass


async def everyone_voted(bot, rnd) -> bool:
    voted, eligible = await context(bot).repo.vote_progress(rnd["id"])
    return voted >= eligible


async def close_round(bot, game, rnd, cause: str = "everyone voted") -> bool:
    """Reveal the tally, then end the game or post the next prompt.

    Returns False when the game is not running or the prompt was already closed.
    """
    repo = context(bot).repo
    game = await repo.get_game(game["id"])
    if game is None or game["status"] != "active":
        return False
    if not await repo.close_round(rnd["id"]):
        return False
    log.info("mostlikely game %s: round %s closed (%s)", game["id"], rnd["id"], cause)
    t = tally(v["target_id"] for v in await repo.list_votes(rnd["id"]))
    try:
        names = await names_by_id(bot, game, await repo.list_players(game["id"]))
        message = await fetch_message(bot, rnd["vote_channel_id"], rnd["vote_message_id"])
        if message is not None:
            try:
                await message.edit(content=closed_text(game, rnd), view=None)
            except NETWORK_ERRORS:
                pass
        await post(bot, game, reveal_text(game, rnd, t, names))
    except Exception:
        log.exception("mostlikely game %s: reveal of round %s failed", game["id"], rnd["id"])
    if rnd["number"] >= game["max_rounds"]:
        await finish(bot, game)
    elif nobody_voted_streak(await repo.closed_round_votes(game["id"])):
        await finish(bot, game, note="nobody voted on the last 2 prompts")
    else:
        await start_round(bot, game)
    return True


async def finish(bot, game, note: str = "") -> bool:
    """End the game and post the recap. False, posting nothing, when it had already ended."""
    repo = context(bot).repo
    rnd = await repo.get_current_round(game["id"])
    if not await repo.finish_game(game["id"]):
        return False
    if rnd is not None and not rnd["closed_at"]:
        message = await fetch_message(bot, rnd["vote_channel_id"], rnd["vote_message_id"])
        if message is not None:
            try:
                await message.edit(content=closed_text(game, rnd), view=None)
            except NETWORK_ERRORS:
                pass
    names = await names_by_id(bot, game, await repo.list_players(game["id"]))
    rounds = await repo.closed_round_votes(game["id"])
    text = recap_text(game, recap(rounds), names, len(rounds), note)
    await post(bot, game, text)
    if game["channel_id"] != await bot.guild_settings.get_announce_channel(game["guild_id"]):
        await announce(bot, game["guild_id"], text)
    await post_standings(bot, game)
    await refresh_lobby(bot, game["id"])
    return True


async def post_standings(bot, game) -> None:
    """Post the all-time standings after a game. Skipped when nobody has a finished game."""
    repo = context(bot).repo
    guild_id = game["guild_id"]
    try:
        rows = standings(await repo.finished_prompts(guild_id), await repo.games_played(guild_id))
        if not rows:
            return
        shown = leaderboard_rows(rows, 0)
        names = {s.user_id: await display_name(bot, guild_id, s.user_id) for _, s in shown}
        await post(bot, game, GAP + leaderboard_text(shown, names))
    except Exception:
        log.exception("mostlikely game %s: standings after the game failed", game["id"])


async def cancel_lobby(bot, game, note: str) -> bool:
    """Close a game that never started. False when it had already ended."""
    if not await context(bot).repo.finish_game(game["id"]):
        return False
    await post(bot, game, f"🚫 {title(game)} was cancelled ({note}).")
    await refresh_lobby(bot, game["id"])
    return True


async def refresh_lobby(bot, game_id: int) -> None:
    """Bring the lobby card up to date. Edits are serialized per game so the last one wins."""
    from ..lobby import lobby_view

    ctx = context(bot)
    async with ctx.lobby_locks[game_id]:
        game = await ctx.repo.get_game(game_id)
        if game is None or not game["lobby_message_id"]:
            return
        players = await ctx.repo.list_players(game_id, active_only=True)
        names = [await display_name(bot, game["guild_id"], p["user_id"]) for p in players]
        host_name = await display_name(bot, game["guild_id"], game["host_id"])
        message = await fetch_message(bot, game["channel_id"], game["lobby_message_id"])
        if message is None:
            log.info("mostlikely game %s: lobby card is gone, not updating it", game_id)
            return
        embed = lobby_embed(game, names, host_name, required_players(bot), vote_seconds(bot))
        try:
            await message.edit(embed=embed, view=lobby_view(game))
        except NETWORK_ERRORS as e:
            log.info("mostlikely game %s: could not update the lobby card: %s", game_id, e)


async def begin_game(bot, game, rounds: int) -> str:
    repo = context(bot).repo
    if game["status"] != "open":
        raise GameError("The game has already started.")
    count = len(await repo.list_players(game["id"], active_only=True))
    need = required_players(bot)
    if count < need:
        raise GameError(f"Need at least {need} players to start; {count} joined so far.")
    if not await repo.start_game(game["id"], rounds):
        raise GameError("The game has already started.")
    game = await repo.get_game(game["id"])
    log.info("mostlikely game %s: started with %d players", game["id"], count)
    await start_round(bot, game)
    await refresh_lobby(bot, game["id"])
    return f"{title(game)} has started with {count} players and {plural(rounds, 'prompt')}."


async def join_game(bot, game, user_id: int) -> str:
    repo = context(bot).repo
    if game["status"] == "finished":
        raise GameError(GAME_OVER)
    try:
        player, _ = await repo.join(game["id"], user_id)
    except AlreadyJoined:
        raise GameError("You're already in.") from None
    game = await repo.get_game(game["id"])
    count = len(await repo.list_players(game["id"], active_only=True))
    name = await display_name(bot, game["guild_id"], user_id)
    rnd = await repo.get_current_round(game["id"]) if game["status"] == "active" else None
    open_round = rnd is not None and not rnd["closed_at"]
    on_roster = open_round and await repo.on_roster(rnd["id"], player["id"])
    mid_prompt = open_round and not on_roster
    await post(bot, game, join_line(name, game, count, required_players(bot), mid_prompt))
    await refresh_lobby(bot, game["id"])
    if mid_prompt:
        return "You're in from the next prompt."
    if on_roster:
        await refresh_vote_message(bot, game, rnd)
        return "You're back in."
    return "You're in."


async def leave_game(bot, game, user_id: int) -> str:
    repo = context(bot).repo
    if game["status"] == "finished":
        raise GameError(GAME_OVER)
    player = await repo.get_player(game["id"], user_id)
    if player is None or not await repo.leave(player["id"]):
        raise GameError("You're not in this game.")
    await after_leave(bot, game, player)
    current = await repo.get_game(game["id"])
    if current is not None and current["status"] != "finished":
        await refresh_lobby(bot, game["id"])
    if game["status"] == "open":
        return "You've left the game."
    return "You've left. Your votes so far still count."


async def after_leave(bot, game, player) -> None:
    """A player left: pass the host role on, then end the game or keep the prompt moving."""
    repo = context(bot).repo
    game = await repo.get_game(game["id"])
    if game is None or game["status"] == "finished":
        return
    active = await repo.list_players(game["id"], active_only=True)
    name = await display_name(bot, game["guild_id"], player["user_id"])
    await post(bot, game, leave_line(name, game, len(active), required_players(bot)))
    if player["user_id"] == game["host_id"]:
        heir = next_host(active, player["id"])
        if heir is not None:
            await repo.set_host(game["id"], heir["user_id"])
            log.info("mostlikely game %s: host passed to %s", game["id"], heir["user_id"])
            await post(
                bot, game, host_line(await display_name(bot, game["guild_id"], heir["user_id"]))
            )
        elif game["status"] == "open":
            await cancel_lobby(bot, game, "everyone left")
            return
    if game["status"] == "open":
        return
    if too_few_to_continue(len(active)):
        await finish(bot, game, note="too few players left")
        return
    rnd = await repo.get_current_round(game["id"])
    if rnd is None or rnd["closed_at"]:
        return
    if await everyone_voted(bot, rnd):
        await close_round(bot, game, rnd, cause="a player left")
    else:
        await refresh_vote_message(bot, game, rnd)


async def close_overdue(bot) -> int:
    """Close every prompt whose deadline has passed. Returns how many were closed."""
    repo = context(bot).repo
    closed = 0
    for rnd in await repo.list_overdue_rounds(now_iso()):
        game = await repo.get_game(rnd["game_id"])
        if game is not None and await close_round(bot, game, rnd, cause="deadline"):
            closed += 1
    return closed


async def resume_stalled(bot, grace: int = STALL_GRACE_SECONDS) -> int:
    """Move on every running game that has had no open prompt for ``grace`` seconds."""
    repo = context(bot).repo
    before = (now() - timedelta(seconds=grace)).isoformat(timespec="seconds")
    resumed = 0
    for game in await repo.list_stalled_games(before):
        log.info("mostlikely game %s: no open prompt, resuming", game["id"])
        if game["round"] >= game["max_rounds"]:
            await finish(bot, game)
        elif nobody_voted_streak(await repo.closed_round_votes(game["id"])):
            await finish(bot, game, note="nobody voted on the last 2 prompts")
        else:
            await start_round(bot, game)
        resumed += 1
    return resumed


async def leaderboard_reply(bot, guild_id: int, caller_id: int, member_id: int | None = None):
    """The all-time standings for a server, or one member's numbers and recent prompt wins."""
    repo = context(bot).repo
    games = await repo.finished_prompts(guild_id)
    rows = standings(games, await repo.games_played(guild_id))
    if member_id is not None:
        name = await display_name(bot, guild_id, member_id)
        standing = next((s for s in rows if s.user_id == member_id), None)
        return member_text(name, standing, recent_prompt_wins(games, member_id))
    shown = leaderboard_rows(rows, caller_id)
    names = {s.user_id: await display_name(bot, guild_id, s.user_id) for _, s in shown}
    return leaderboard_text(shown, names)

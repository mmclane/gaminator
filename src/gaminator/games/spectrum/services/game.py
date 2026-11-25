"""Spectrum text and the game flow that touches Discord."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import timedelta

import discord

from ....core.messaging import NETWORK_ERRORS, NO_MENTIONS, announce, display_name
from ....core.repo import AlreadyJoined
from ....core.util import GameError, is_game_admin, now, now_iso, parse_iso, signup_text
from ..context import context
from .rules import (
    ABANDONED_TO_STOP,
    BAD_GUESS,
    CLUE_OVER,
    DEFAULT_ROUNDS,
    GAME_OVER,
    GIVER_SITS_OUT,
    GUESS_OVER,
    JOINED_LATE,
    MAX_PLAYERS,
    MIN_PLAYERS,
    NOT_YOUR_TARGET,
    ScoredTurn,
    Standing,
    clean_clue,
    closest_guess,
    clue_problem,
    clue_submit_problem,
    game_points,
    game_winners,
    giver_points,
    guess_points,
    guess_problem,
    leaderboard_rows,
    min_players,
    next_host,
    parse_guess,
    round_without_clue,
    standings,
    target_problem,
    too_few_to_continue,
    unguessed_streak,
)
from .scales import Scale, pick_scales, scale_text

log = logging.getLogger(__name__)
RNG = random.SystemRandom()
DEFAULT_GAME = {"status": "open", "max_rounds": DEFAULT_ROUNDS}
LEADERBOARD_HINT = "All-time standings: `/gaminator spectrum leaderboard`"
NAME_LIMIT = 32
ECHO_LIMIT = 200
GAP = "_ _\n"
STALL_GRACE_SECONDS = 30
REVEAL_PAUSE = 3
CHANNEL_NOTE = "I couldn't post in the game channel"
UNGUESSED_NOTE = "2 turns in a row got no guesses"
NO_CLUE_NOTE = "nobody gave a clue this round"
NOT_PLAYING = "You're not in this game. Join with `/gaminator spectrum join`."
NEXT_ROUND = "You're in from the next round."
BACK_IN = "You're back in."
TURN_COMING = "Your turn to give a clue is coming up in this round."
TURN_ENDINGS = {
    "no_clue": "No clue in time, so this turn is skipped.",
    "failed": "I couldn't post the clue, so this turn is skipped.",
    "ended": "The game ended.",
}


class ChannelGone(Exception):
    """The game channel was deleted, or the bot can no longer see or post in it."""


def GIVER_MENTION(user_id: int) -> discord.AllowedMentions:
    """Lets a message ping the clue giver and nobody else."""
    return discord.AllowedMentions(
        users=[discord.Object(user_id)], everyone=False, roles=False, replied_user=False
    )


def required_players(bot) -> int:
    return min_players(bot.settings.min_players)


def clue_seconds(bot) -> int:
    return bot.settings.spectrum_clue_seconds


def guess_seconds(bot) -> int:
    return bot.settings.spectrum_guess_seconds


def title(game) -> str:
    return f"Spectrum game **{game['name']}**"


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def duration_text(seconds: int) -> str:
    if seconds % 60 == 0:
        return plural(seconds // 60, "minute")
    return plural(seconds, "second")


def safe(name: str) -> str:
    """A display name cut to a sane length, with its markdown made harmless."""
    return discord.utils.escape_markdown(name[:NAME_LIMIT])


def when(stamp: str | None) -> str:
    deadline = parse_iso(stamp)
    return f"<t:{int(deadline.timestamp()) if deadline else 0}:R>"


def closes(stamp: str | None) -> str:
    return f"Closes {when(stamp)}."


def name_list(names: list[str]) -> str:
    bold = [f"**{n}**" for n in names]
    if len(bold) <= 1:
        return "".join(bold)
    return ", ".join(bold[:-1]) + " and " + bold[-1]


def scale_of(turn) -> str:
    """A turn row's scale on one line."""
    return scale_text(Scale(turn["left_end"], turn["right_end"]))


def clue_of(turn) -> str:
    """A turn row's clue, with whatever the giver typed made harmless."""
    return discord.utils.escape_markdown(turn["clue"])


def how_it_works(game, clue_s: int = 120, guess_s: int = 120, need: int = MIN_PLAYERS) -> str:
    if game["status"] == "open":
        length = (
            f"A game is {plural(DEFAULT_ROUNDS, 'round')} unless the host picks more with "
            "`/gaminator spectrum start rounds:`."
        )
    else:
        length = f"This game runs {plural(game['max_rounds'], 'round')}."
    return (
        "• Each turn one player, the clue giver, sees a secret number from 1 to 100 on a scale "
        "and gives a clue with no digits.\n"
        "• Everyone else talks it over and guesses the number. Guesses stay hidden until the "
        "reveal, and you can change yours until the turn closes.\n"
        f"• Guessing closes when everyone has guessed or after {duration_text(guess_s)}. A clue "
        f"not given within {duration_text(clue_s)} skips the turn.\n"
        "• A guess scores `100 - 3 × distance` from the target (0 at 34 or more away). The clue "
        "giver gets the guessers' average.\n"
        f"• {length} Everyone gives one clue per round. The most points wins.\n"
        "• Join or leave at any time with `/gaminator spectrum join` and `leave`. Join mid "
        "round and you guess from the next clue and give yours at the end of the round.\n"
        "• The game ends early after 2 turns in a row with no guesses, a round with no clues, "
        f"or with fewer than {need} players left."
    )


def rules_text(game, clue_s: int = 120, guess_s: int = 120, need: int = MIN_PLAYERS) -> str:
    return "## 🌈 How Spectrum works\n" + how_it_works(game, clue_s, guess_s, need)


def lobby_embed(
    game, names: list[str], host_name: str, need: int, clue_s: int = 120, guess_s: int = 120
):
    """The lobby card: state, host, player count, and the players in join order."""
    count = len(names)
    host = safe(host_name)
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
    icon = "🏁" if game["status"] == "finished" else "🌈"
    roster = [f"{n}. {safe(name)}" for n, name in enumerate(names, start=1)]
    embed = discord.Embed(
        title=f"{icon} {game['name']}: {state}"[:256],
        description="\n".join(lines + [""] + roster).strip(),
        colour=colour,
    )
    embed.add_field(
        name="How it works", value=how_it_works(game, clue_s, guess_s, need), inline=False
    )
    return embed


def turn_head(game, turn) -> str:
    return (
        f"{GAP}## 🌈 Turn {turn['position']} of {turn['turn_count']} "
        f"(round {turn['round_number']} of {game['max_rounds']})\n"
        f"### {scale_of(turn)}"
    )


def turn_text(game, turn, giver_id: int) -> str:
    """The message that opens a turn. It is the only one that pings, and only the clue giver."""
    return (
        f"{turn_head(game, turn)}\n"
        f"<@{giver_id}>, you give the clue. Press **Show my target** to see the secret number, "
        "then **Write clue**.\n"
        f"Clue due {when(turn['clue_deadline_at'])}."
    )


def turn_done_text(game, turn, giver_name: str, why: str) -> str:
    """The turn message once its clue step is over.

    ``why`` is "clue" when the clue was posted, "left" when the giver left, or a key of
    TURN_ENDINGS.
    """
    if why == "clue":
        tail = f"**{safe(giver_name)}**'s clue: **{clue_of(turn)}**"
    elif why == "left":
        tail = f"**{safe(giver_name)}** left, so this turn is skipped."
    else:
        tail = TURN_ENDINGS[why]
    return f"{turn_head(game, turn)}\n{tail}"


def target_text(turn, in_form: bool = False) -> str:
    """The private text that shows the clue giver their target, in a reply or in the clue form."""
    left = discord.utils.escape_markdown(turn["left_end"])
    right = discord.utils.escape_markdown(turn["right_end"])
    ready = "" if in_form else "Press **Write clue** on the turn message when you're ready. "
    return (
        f"🎯 Your target is **{turn['target']}**.\n"
        f"### {scale_of(turn)}\n"
        f"1 is fully {left}, 100 is fully {right}.\n"
        f"{ready}No digits allowed. Clue due {when(turn['clue_deadline_at'])}."
    )


def guess_head(game, turn, giver_name: str) -> str:
    return f"{GAP}## 🌈 {scale_of(turn)}\n**{safe(giver_name)}**'s clue: **{clue_of(turn)}**"


def progress_text(guessed: int, expected: int) -> str:
    return f"{guessed}/{expected} guesses in."


def guess_text(game, turn, giver_name: str, guessed: int, expected: int) -> str:
    left = discord.utils.escape_markdown(turn["left_end"])
    right = discord.utils.escape_markdown(turn["right_end"])
    return (
        f"{guess_head(game, turn, giver_name)}\n"
        f"Talk it over, then press **Guess** and enter a number from 1 (fully {left}) to 100 "
        f"(fully {right}). Guesses stay hidden until the reveal, and you can change yours until "
        "the turn closes.\n"
        f"{progress_text(guessed, expected)} {closes(turn['guess_deadline_at'])}"
    )


def guess_closed_text(game, turn, giver_name: str) -> str:
    return f"{guess_head(game, turn, giver_name)}\nGuessing is closed."


def drumroll_text() -> str:
    return f"{GAP}🥁 The guesses are in. The target is..."


def number_line(value: int, text: str) -> str:
    return f"`{value:>3}` {text}"


def reveal_text(turn, giver_name: str, guesses, names: dict[int, str]) -> str:
    """The reveal: the target and every named guess in order on the scale, with points.

    ``guesses`` holds (user id, value). The target comes before a guess of the same value.
    """
    target = turn["target"]
    giver = safe(giver_name)
    spots = [(target, 0, 0, "🎯 target")]
    scores = []
    for user_id, value in guesses:
        points = guess_points(value, target)
        scores.append(points)
        spots.append((value, 1, user_id, f"**{safe(names.get(user_id, '?'))}**: {points}"))
    lines = [
        f"{GAP}## 🎯 The target was {target}",
        f"{scale_of(turn)}. Clue from **{giver}**: **{clue_of(turn)}**",
        number_line(1, discord.utils.escape_markdown(turn["left_end"])),
    ]
    lines += [number_line(value, text) for value, _, _, text in sorted(spots)]
    lines.append(number_line(100, discord.utils.escape_markdown(turn["right_end"])))
    if scores:
        lines.append(
            f"**{giver}** scores **{giver_points(scores)}** for the clue, the guessers' average."
        )
    else:
        lines.append("Nobody guessed, so nobody scores.")
    return "\n".join(lines)


def no_clue_line(giver_name: str) -> str:
    return f"{GAP}⏭️ No clue from **{safe(giver_name)}** in time, so this turn is skipped."


def skipped_line(giver_name: str, why: str) -> str:
    """The channel line for a skipped turn. ``why`` is "left" or "alone"."""
    if why == "left":
        return f"{GAP}⏭️ **{safe(giver_name)}** left, so their turn is skipped."
    return f"{GAP}⏭️ Nobody else is here to guess, so **{safe(giver_name)}**'s turn is skipped."


def points_list(points: list[tuple[int, int]], names: dict[int, str]) -> str:
    return ", ".join(f"{safe(names.get(uid, '?'))} {total}" for uid, total in points)


def round_text(
    game,
    number: int,
    round_points: list[tuple[int, int]],
    totals: list[tuple[int, int]],
    names: dict[int, str],
) -> str:
    return (
        f"{GAP}🏁 **Round {number} of {game['max_rounds']}** is done.\n"
        f"This round: {points_list(round_points, names)}\n"
        f"Totals: {points_list(totals, names)}\n"
        f"Round {number + 1} starts now."
    )


def recap_text(
    game,
    points: list[tuple[int, int]],
    names: dict[int, str],
    rounds: int,
    closest: tuple[ScoredTurn, int, int] | None,
    note: str = "",
) -> str:
    head = f"{GAP}🏁 {title(game)} is over after {plural(rounds, 'round')}"
    head += f" ({note})." if note else "."
    winners = game_winners(points)
    if not winners:
        return f"{head}\nNobody scored.\n{LEADERBOARD_HINT}"
    top = points[0][1]
    winner_names = [safe(names.get(i, "?")) for i in winners]
    if len(winners) == 1:
        lines = [head, f"🏆 **{winner_names[0]}** wins with {plural(top, 'point')}!"]
    else:
        lines = [head, f"🏆 {name_list(winner_names)} tie with {plural(top, 'point')} each!"]
    rest = [(i, n) for i, n in points if i not in winners]
    if rest:
        lines.append("Also: " + points_list(rest, names))
    if closest is not None:
        turn, user_id, value = closest
        scale = scale_text(Scale(turn.left, turn.right))
        lines.append(
            f"🎯 Closest guess: **{safe(names.get(user_id, '?'))}** said {value} with the target "
            f"at {turn.target} on {scale}."
        )
    lines.append(LEADERBOARD_HINT)
    return "\n".join(lines)


def standing_text(standing: Standing) -> str:
    return (
        f"{plural(standing.games_won, 'game')} won, {plural(standing.points, 'point')}, "
        f"{standing.games_played} played"
    )


def leaderboard_text(rows: list[tuple[int, Standing]], names: dict[int, str]) -> str:
    if not rows:
        return "No finished Spectrum games yet. Start one with `/gaminator spectrum create`."
    lines = [f"{GAP}## 🏆 Spectrum: all-time standings"]
    previous = 0
    for rank, standing in rows:
        if rank != previous + 1:
            lines.append("")
        name = safe(names.get(standing.user_id, "?"))
        lines.append(f"`{rank}.` **{name}**: {standing_text(standing)}")
        previous = rank
    return "\n".join(lines)


def member_text(name: str, standing: Standing | None) -> str:
    if standing is None:
        return f"**{safe(name)}** hasn't played a finished game yet."
    return f"**{safe(name)}**: {standing_text(standing)}"


def clue_saved_text() -> str:
    return "Your clue is up."


def clue_failed_text() -> str:
    return "I couldn't post your clue, so this turn is skipped."


def guess_saved_text(value: int) -> str:
    return f"Your guess is **{value}**. You can change it until the turn closes."


def refused_clue_text(problem: str, text: str) -> str:
    """Why a clue was refused, with what the giver wrote so they can fix it and send it again."""
    lines = [problem]
    wrote = " ".join(text.split())[:ECHO_LIMIT]
    if wrote:
        lines.append(f"You wrote: {discord.utils.escape_markdown(wrote)}")
    lines.append("Press **Write clue** to try again.")
    return "\n".join(lines)


def late_join_text() -> str:
    return (
        "You're in. You can guess from the next clue, and your turn to give a clue comes at "
        "the end of this round."
    )


def status_lines(
    game,
    rnd,
    turn=None,
    giver_name: str = "",
    progress: tuple[int, int] = (0, 0),
    place: str = "in",
    my_guess: int | None = None,
    turn_coming: bool = False,
) -> list[str]:
    """Where a running game stands, for one caller. The target is never part of it.

    ``turn`` is the open turn, if any. ``place`` is "giver" for its clue giver, "guesser" for a
    player who may guess on it, "late" for one who joined after its clue, "in" for any other
    player in the game and "out" for everyone else. ``turn_coming`` is True for a player whose
    own turn in this round is still waiting.
    """
    if rnd is None or rnd["closed_at"]:
        lines = ["The next round is on its way."]
    elif turn is None:
        lines = ["The next turn is on its way."]
    else:
        giver = safe(giver_name)
        place_in_round = (
            f"**Turn {turn['position']} of {turn['turn_count']}** "
            f"(round {turn['round_number']} of {game['max_rounds']})"
        )
        lines = [f"🌈 {place_in_round}: {scale_of(turn)}"]
        if turn["clue"] is None:
            lines.append(
                f"**{giver}** is writing the clue. Clue due {when(turn['clue_deadline_at'])}."
            )
            if place == "giver":
                lines.append(
                    "It's your turn to give the clue. Press **Show my target** on the turn message."
                )
        else:
            lines.append(f"**{giver}**'s clue: **{clue_of(turn)}**")
            lines.append(f"{progress_text(*progress)} {closes(turn['guess_deadline_at'])}")
            if place == "giver":
                lines.append(GIVER_SITS_OUT)
            elif place == "late":
                lines.append(JOINED_LATE)
            elif place == "guesser" and my_guess is None:
                lines.append("You haven't guessed yet. Press **Guess** on the clue message.")
            elif place == "guesser":
                lines.append(f"Your guess is **{my_guess}**.")
    if place == "out":
        lines.append(NOT_PLAYING)
    elif turn_coming:
        lines.append(TURN_COMING)
    return lines


def join_line(name: str, game, count: int, need: int) -> str:
    if game["status"] == "open":
        return f"{GAP}🙋 **{safe(name)}** joined {title(game)}. {signup_text(count, need)}"
    return f"{GAP}🙋 **{safe(name)}** joined."


def leave_line(name: str, game, count: int, need: int) -> str:
    if game["status"] == "open":
        return f"{GAP}🚪 **{safe(name)}** left {title(game)}. {signup_text(count, need)}"
    remain = f"{plural(count, 'player')} remain{'s' if count == 1 else ''}"
    return f"{GAP}🚪 **{safe(name)}** left. {remain}."


def host_line(name: str) -> str:
    return f"{GAP}👑 **{safe(name)}** is now the host."


def cancelled_text(game, note: str) -> str:
    return f"{GAP}🚫 {title(game)} was cancelled ({note})."


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
        log.warning("spectrum game %s: could not post in %s: %s", game["id"], channel.id, e)
        return None


async def post_with_view(
    bot, game, text: str, view, *, allowed_mentions: discord.AllowedMentions = NO_MENTIONS
) -> discord.Message | None:
    """Post a message with buttons, retrying once. Raises ChannelGone when the channel is lost."""
    for attempt in (1, 2):
        try:
            channel = await game_channel(bot, game)
            if channel is not None:
                return await channel.send(text, view=view, allowed_mentions=allowed_mentions)
        except (discord.Forbidden, discord.NotFound) as e:
            raise ChannelGone() from e
        except NETWORK_ERRORS as e:
            log.warning(
                "spectrum game %s: could not post a step (attempt %d): %s", game["id"], attempt, e
            )
    return None


async def names_by_user(bot, game, user_ids) -> dict[int, str]:
    return {uid: await display_name(bot, game["guild_id"], uid) for uid in user_ids}


async def require_host_or_admin(interaction: discord.Interaction, game) -> None:
    """Pass for the game's host or a game admin. Raises NotGameAdmin for anyone else."""
    if interaction.user.id == game["host_id"]:
        return
    await is_game_admin(interaction)


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


async def edit_message(bot, game, message_id: int | None, text: str) -> None:
    """Replace a game message's text and take its buttons away. Network errors are ignored."""
    message = await fetch_message(bot, game["channel_id"], message_id)
    if message is None:
        return
    try:
        await message.edit(content=text, view=None, allowed_mentions=NO_MENTIONS)
    except NETWORK_ERRORS:
        pass


def deadline_in(seconds: int) -> str:
    return (now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


async def giver_name_of(bot, game, turn) -> str:
    return await display_name(bot, game["guild_id"], turn["giver_user_id"])


async def start_round(bot, game) -> bool:
    """Deal the next round, one turn per player, and open its first turn. False when none began."""
    ctx = context(bot)
    repo = ctx.repo
    scales = pick_scales(ctx.scales, RNG, MAX_PLAYERS, await repo.used_scales(game["id"]))
    rnd = await repo.begin_round(game["id"], scales, RNG)
    if rnd is None:
        return False
    log.info("spectrum game %s: round %s started", game["id"], rnd["id"])
    await advance(bot, game)
    return True


async def idle_note(bot, game) -> str | None:
    """Why a game nobody is playing should stop, or None.

    Read from what was stored when each turn closed, never from who is in the game now. Turns
    with no clue don't count toward the unguessed streak, and abandoned turns count for nothing.
    """
    repo = context(bot).repo
    if unguessed_streak(await repo.revealed_guess_counts(game["id"])):
        return UNGUESSED_NOTE
    rnd = await repo.last_closed_round(game["id"])
    if rnd is not None and round_without_clue(*await repo.round_clue_counts(rnd["id"])):
        return NO_CLUE_NOTE
    return None


async def advance(bot, game) -> None:
    """Take the game's next step: open a turn, end the round, start a round, or finish."""
    repo = context(bot).repo
    game = await repo.get_game(game["id"])
    if game is None or game["status"] != "active":
        return
    rnd = await repo.get_current_round(game["id"])
    if rnd is None or rnd["closed_at"]:
        if await repo.rounds_played(game["id"]) >= game["max_rounds"]:
            await finish(bot, game)
        elif note := await idle_note(bot, game):
            await finish(bot, game, note=note)
        else:
            await start_round(bot, game)
        return
    if await repo.current_turn(rnd["id"]) is not None:
        return
    while (turn := await repo.next_turn(rnd["id"])) is not None:
        if await repo.abandoned_streak(game["id"]) >= ABANDONED_TO_STOP:
            await finish(bot, game, note=CHANNEL_NOTE)
            return
        giver = await repo.get_player_by_id(turn["giver_id"])
        active = await repo.list_players(game["id"], active_only=True)
        if giver["left_at"] is not None:
            why = "left"
        elif all(p["id"] == giver["id"] for p in active):
            why = "alone"
        else:
            break
        if not await skip_turn(bot, game, turn, "skipped", why, advance_after=False):
            return
    if turn is None:
        await end_round(bot, game, rnd)
    elif note := await idle_note(bot, game):
        await finish(bot, game, note=note)
    else:
        await open_next_turn(bot, game, turn)


async def open_next_turn(bot, game, turn) -> bool:
    """Open a turn's clue step and post it, pinging the clue giver. False when it did not go up.

    A turn that can't be posted is abandoned and the sweep opens the next one after the stall
    grace period, so a short outage costs one turn, not the rest of the round.
    """
    from ..turn import turn_view

    repo = context(bot).repo
    if not await repo.open_turn(turn["id"], deadline_in(clue_seconds(bot))):
        return False
    turn = await repo.get_turn(turn["id"])
    giver_id = turn["giver_user_id"]
    try:
        message = await post_with_view(
            bot,
            game,
            turn_text(game, turn, giver_id),
            turn_view(turn["id"]),
            allowed_mentions=GIVER_MENTION(giver_id),
        )
    except ChannelGone:
        log.warning("spectrum game %s: game channel is gone, ending the game", game["id"])
        await finish(bot, game, note=CHANNEL_NOTE)
        return False
    if message is None:
        log.warning("spectrum game %s: could not post turn %s", game["id"], turn["id"])
        if await repo.close_turn(turn["id"], "abandoned"):
            log.info("spectrum game %s: turn %s closed (abandoned)", game["id"], turn["id"])
        return False
    await repo.set_turn_message(turn["id"], message.id)
    log.info("spectrum game %s: turn %s opened", game["id"], turn["id"])
    return True


async def show_target(bot, turn_id: int, user_id: int) -> str:
    """The private reply to Show my target: the number for the clue giver, a refusal otherwise."""
    ctx = await context(bot).repo.turn_context(turn_id, user_id)
    problem = target_problem(ctx)
    if problem == NOT_YOUR_TARGET:
        name = await display_name(bot, ctx["guild_id"], ctx["giver_user_id"])
        return problem.format(giver=safe(name))
    if problem:
        return problem
    return target_text(ctx)


async def submit_clue(bot, turn_id: int, user_id: int, raw: str) -> str:
    """Save the clue giver's clue and open guessing. Returns the reply for them.

    The guess message goes up before the turn message changes, so a clue that couldn't be
    posted is never shown as accepted.
    """
    from ..guess import guess_view

    repo = context(bot).repo
    ctx = await repo.turn_context(turn_id, user_id)
    problem = clue_submit_problem(ctx)
    if problem:
        return problem
    problem = clue_problem(raw)
    if problem:
        return refused_clue_text(problem, raw)
    clue = clean_clue(raw) or ""
    if not await repo.save_clue(turn_id, ctx["player_id"], clue, deadline_in(guess_seconds(bot))):
        return CLUE_OVER
    game = await repo.get_game(ctx["game_id"])
    turn = await repo.get_turn(turn_id)
    log.info("spectrum game %s: clue saved for turn %s", game["id"], turn_id)
    giver_name = await giver_name_of(bot, game, turn)
    guessed, expected = await repo.guess_progress(turn_id)
    try:
        message = await post_with_view(
            bot, game, guess_text(game, turn, giver_name, guessed, expected), guess_view(turn_id)
        )
    except ChannelGone:
        log.warning("spectrum game %s: game channel is gone, ending the game", game["id"])
        await repo.close_turn(turn_id, "abandoned")
        await finish(bot, game, note=CHANNEL_NOTE)
        return clue_failed_text()
    if message is None:
        log.warning("spectrum game %s: could not post the clue of turn %s", game["id"], turn_id)
        if await repo.close_turn(turn_id, "abandoned"):
            log.info("spectrum game %s: turn %s closed (abandoned)", game["id"], turn_id)
            await edit_message(
                bot, game, turn["turn_message_id"], turn_done_text(game, turn, giver_name, "failed")
            )
        return clue_failed_text()
    await repo.set_guess_message(turn_id, message.id)
    await edit_message(
        bot, game, turn["turn_message_id"], turn_done_text(game, turn, giver_name, "clue")
    )
    return clue_saved_text()


async def submit_guess(bot, turn_id: int, user_id: int, raw: str) -> str:
    """Save or change a player's guess. Returns the reply for them."""
    repo = context(bot).repo
    ctx = await repo.turn_context(turn_id, user_id)
    problem = guess_problem(ctx)
    if problem:
        return problem
    value = parse_guess(raw)
    if value is None:
        return BAD_GUESS
    if not await repo.save_guess(turn_id, ctx["player_id"], value):
        return GUESS_OVER
    if ctx["my_guess"] is None:
        game = await repo.get_game(ctx["game_id"])
        turn = await repo.get_turn(turn_id)
        await refresh_guess_message(bot, game, turn)
    return guess_saved_text(value)


async def everyone_guessed(bot, turn) -> bool:
    """True when every guesser still in the game has guessed, or there is none left."""
    guessed, expected = await context(bot).repo.guess_progress(turn["id"])
    return guessed >= expected


async def close_if_done(bot, turn_id: int) -> bool:
    """Close and reveal a turn once everyone has guessed. Called after a pop-up is answered."""
    repo = context(bot).repo
    turn = await repo.get_turn(turn_id)
    if turn is None or turn["clue"] is None or turn["closed_at"]:
        return False
    if not await everyone_guessed(bot, turn):
        return False
    game = await repo.get_game(turn["game_id"])
    return await close_turn(bot, game, turn, cause="everyone guessed")


async def refresh_guess_message(bot, game, turn, message=None) -> None:
    repo = context(bot).repo
    guessed, expected = await repo.guess_progress(turn["id"])
    message = message or await fetch_message(bot, game["channel_id"], turn["guess_message_id"])
    if message is None:
        return
    giver_name = await giver_name_of(bot, game, turn)
    try:
        await message.edit(
            content=guess_text(game, turn, giver_name, guessed, expected),
            allowed_mentions=NO_MENTIONS,
        )
    except NETWORK_ERRORS:
        pass


async def close_turn(bot, game, turn, cause: str = "everyone guessed") -> bool:
    """Close guessing, pause, reveal the target, the named guesses and the points, then move on.

    The turn is closed in the database before the pause, so a crash during it is picked up by
    ``resume_stalled``.

    Returns False when the game is not running or the turn was already closed.
    """
    repo = context(bot).repo
    game = await repo.get_game(game["id"])
    if game is None or game["status"] != "active":
        return False
    if not await repo.close_turn(turn["id"], "revealed"):
        return False
    log.info("spectrum game %s: turn %s closed (%s)", game["id"], turn["id"], cause)
    try:
        turn = await repo.get_turn(turn["id"])
        await close_guess_message(bot, game, turn)
        await post(bot, game, drumroll_text())
        await asyncio.sleep(REVEAL_PAUSE)
        await post(bot, game, await reveal_for(bot, game, turn))
    except Exception:
        log.exception("spectrum game %s: reveal of turn %s failed", game["id"], turn["id"])
    await advance(bot, game)
    return True


async def close_guess_message(bot, game, turn) -> None:
    giver_name = await giver_name_of(bot, game, turn)
    await edit_message(
        bot, game, turn["guess_message_id"], guess_closed_text(game, turn, giver_name)
    )


async def reveal_for(bot, game, turn) -> str:
    """The reveal text for a closed turn, read from its stored guesses."""
    rows = await context(bot).repo.turn_guesses(turn["id"])
    guesses = [(r["user_id"], r["value"]) for r in rows]
    names = await names_by_user(bot, game, [turn["giver_user_id"], *(uid for uid, _ in guesses)])
    return reveal_text(turn, names[turn["giver_user_id"]], guesses, names)


async def skip_turn(bot, game, turn, outcome: str, why: str, advance_after: bool = True) -> bool:
    """Close a turn that gets no clue and say so in the channel. False when it lost a race.

    ``outcome`` is "no_clue" for a clue that ran out of time, else "skipped" with ``why`` set to
    "left" or "alone". Neither can close a turn whose clue was saved a moment earlier.
    """
    repo = context(bot).repo
    if not await repo.close_turn(turn["id"], outcome):
        return False
    log.info("spectrum game %s: turn %s closed (%s)", game["id"], turn["id"], outcome)
    giver_name = await giver_name_of(bot, game, turn)
    if turn["turn_message_id"]:
        ending = "no_clue" if outcome == "no_clue" else why
        await edit_message(
            bot, game, turn["turn_message_id"], turn_done_text(game, turn, giver_name, ending)
        )
    if outcome == "no_clue":
        await post(bot, game, no_clue_line(giver_name))
    else:
        await post(bot, game, skipped_line(giver_name, why))
    if advance_after:
        await advance(bot, game)
    return True


async def end_round(bot, game, rnd) -> None:
    """Close a round whose turns have all been played, post its results, and move on.

    A round that can't be closed has a turn a late joiner was just given, or was closed by
    someone else; ``advance`` deals with either.
    """
    repo = context(bot).repo
    if not await repo.close_round(rnd["id"]):
        await advance(bot, game)
        return
    log.info("spectrum game %s: round %s closed", game["id"], rnd["id"])
    more = await repo.rounds_played(game["id"]) < game["max_rounds"]
    if more and not await idle_note(bot, game):
        try:
            round_points = game_points(
                await repo.scored_turns(game["id"], rnd["id"]),
                await repo.game_user_ids(game["id"], rnd["id"]),
            )
            totals = game_points(
                await repo.scored_turns(game["id"]), await repo.game_user_ids(game["id"])
            )
            names = await names_by_user(bot, game, [uid for uid, _ in totals])
            await post(bot, game, round_text(game, rnd["number"], round_points, totals, names))
        except Exception:
            log.exception("spectrum game %s: results of round %s failed", game["id"], rnd["id"])
    await advance(bot, game)


async def finish(bot, game, note: str = "") -> bool:
    """End the game and post the recap. False, posting no recap, when it had already ended.

    A turn that has its clue is revealed first, with no pause, so its guesses score and the
    target is shown. A turn still waiting for its clue is abandoned.
    """
    repo = context(bot).repo
    rnd = await repo.get_current_round(game["id"])
    open_round = rnd is not None and not rnd["closed_at"]
    turn = await repo.current_turn(rnd["id"]) if open_round else None
    rounds = await repo.rounds_played(game["id"])
    clued = turn is not None and turn["clue"] is not None
    if clued and await repo.close_turn(turn["id"], "revealed"):
        log.info("spectrum game %s: turn %s closed (game ended)", game["id"], turn["id"])
        try:
            turn = await repo.get_turn(turn["id"])
            await close_guess_message(bot, game, turn)
            await post(bot, game, await reveal_for(bot, game, turn))
        except Exception:
            log.exception("spectrum game %s: reveal of turn %s failed", game["id"], turn["id"])
    if open_round and await repo.scored_turns(game["id"], rnd["id"]):
        rounds += 1
    if not await repo.finish_game(game["id"]):
        return False
    log.info("spectrum game %s: finished (%s)", game["id"], note or "all rounds played")
    if turn is not None and turn["clue"] is None and turn["turn_message_id"]:
        giver_name = await giver_name_of(bot, game, turn)
        await edit_message(
            bot, game, turn["turn_message_id"], turn_done_text(game, turn, giver_name, "ended")
        )
    turns = await repo.scored_turns(game["id"])
    points = game_points(turns, await repo.game_user_ids(game["id"]))
    names = await names_by_user(bot, game, [uid for uid, _ in points])
    text = recap_text(game, points, names, rounds, closest_guess(turns), note)
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
        rows = standings(await repo.finished_turns(guild_id), await repo.games_played(guild_id))
        if not rows:
            return
        shown = leaderboard_rows(rows, 0)
        names = await names_by_user(bot, game, [s.user_id for _, s in shown])
        await post(bot, game, leaderboard_text(shown, names))
    except Exception:
        log.exception("spectrum game %s: standings after the game failed", game["id"])


async def cancel_lobby(bot, game, note: str) -> bool:
    """Close a game that never started. False when it had already ended."""
    if not await context(bot).repo.finish_game(game["id"]):
        return False
    await post(bot, game, cancelled_text(game, note))
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
            log.info("spectrum game %s: lobby card is gone, not updating it", game_id)
            return
        embed = lobby_embed(
            game, names, host_name, required_players(bot), clue_seconds(bot), guess_seconds(bot)
        )
        try:
            await message.edit(embed=embed, view=lobby_view(game))
        except NETWORK_ERRORS as e:
            log.info("spectrum game %s: could not update the lobby card: %s", game_id, e)


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
    log.info("spectrum game %s: started with %d players", game["id"], count)
    await start_round(bot, game)
    await refresh_lobby(bot, game["id"])
    return f"{title(game)} has started with {count} players and {plural(rounds, 'round')}."


async def give_late_turn(bot, game, rnd, player) -> str:
    """Give a player who joined during a round a turn at its end. Returns the reply for them."""
    ctx = context(bot)
    repo = ctx.repo
    if await repo.has_turn(rnd["id"], player["id"]):
        return BACK_IN
    scale = pick_scales(ctx.scales, RNG, 1, await repo.used_scales(game["id"]))[0]
    if not await repo.add_turn(rnd["id"], player["id"], scale, RNG.randint(1, 100)):
        return NEXT_ROUND
    log.info(
        "spectrum game %s: turn added to round %s for player %s",
        game["id"],
        rnd["id"],
        player["id"],
    )
    return late_join_text()


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
    reply = "You're in."
    rnd = await repo.get_current_round(game["id"]) if game["status"] == "active" else None
    open_round = rnd is not None and not rnd["closed_at"]
    if open_round:
        reply = await give_late_turn(bot, game, rnd, player)
    elif game["status"] == "active":
        reply = NEXT_ROUND
    await post(bot, game, join_line(name, game, count, required_players(bot)))
    await refresh_lobby(bot, game["id"])
    turn = await repo.current_turn(rnd["id"]) if open_round else None
    if turn is not None and turn["clue"] is not None:
        await refresh_guess_message(bot, game, turn)
    return reply


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
    return "You've left. Your clues and guesses so far still count."


async def after_leave(bot, game, player) -> None:
    """A player left: pass the host role on, then end the game or keep the open turn moving."""
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
            log.info("spectrum game %s: host passed to %s", game["id"], heir["user_id"])
            await post(
                bot, game, host_line(await display_name(bot, game["guild_id"], heir["user_id"]))
            )
        elif game["status"] == "open":
            await cancel_lobby(bot, game, "everyone left")
            return
    if game["status"] == "open":
        return
    if too_few_to_continue(len(active), required_players(bot)):
        await finish(bot, game, note="too few players left")
        return
    rnd = await repo.get_current_round(game["id"])
    if rnd is None or rnd["closed_at"]:
        return
    turn = await repo.current_turn(rnd["id"])
    if turn is None:
        return
    if turn["clue"] is None:
        if turn["giver_id"] == player["id"]:
            await skip_turn(bot, game, turn, "skipped", "left")
        return
    if turn["giver_id"] == player["id"]:
        return
    if await everyone_guessed(bot, turn):
        await close_turn(bot, game, turn, cause="a player left")
    else:
        await refresh_guess_message(bot, game, turn)


async def status_reply(bot, game, user_id: int) -> str:
    """The private status for one caller: the game, its players, and where they stand in it."""
    repo = context(bot).repo
    players = await repo.list_players(game["id"], active_only=True)
    names = await names_by_user(bot, game, [p["user_id"] for p in players])
    host = safe(await display_name(bot, game["guild_id"], game["host_id"]))
    roster = ", ".join(safe(names[p["user_id"]]) for p in players) or "nobody"
    lines = [f"{title(game)}, hosted by **{host}**.", f"**Players ({len(players)}):** {roster}"]
    if game["status"] == "open":
        lines.append(f"Sign-ups are open. It needs at least {required_players(bot)} to start.")
        return "\n".join(lines)
    mine = next((p for p in players if p["user_id"] == user_id), None)
    place = "in" if mine is not None else "out"
    rnd = await repo.get_current_round(game["id"])
    if rnd is None or rnd["closed_at"]:
        return "\n".join(lines + status_lines(game, rnd, place=place))
    coming = mine is not None and await repo.waiting_turn(rnd["id"], mine["id"])
    turn = await repo.current_turn(rnd["id"])
    if turn is None:
        return "\n".join(lines + status_lines(game, rnd, place=place, turn_coming=coming))
    giver_name = await giver_name_of(bot, game, turn)
    progress, my_guess = (0, 0), None
    if mine is not None and mine["id"] == turn["giver_id"]:
        place = "giver"
    if turn["clue"] is not None:
        progress = await repo.guess_progress(turn["id"])
        if place == "in":
            ctx = await repo.turn_context(turn["id"], user_id)
            place = "guesser" if ctx["may_guess"] else "late"
            my_guess = ctx["my_guess"]
    step = status_lines(game, rnd, turn, giver_name, progress, place, my_guess, coming)
    return "\n".join(lines + step)


async def close_overdue(bot) -> int:
    """Close every clue step and every guessing step whose deadline has passed. Returns how many.

    A step whose message never went up (a crash between the database write and the post) is
    abandoned, so nobody is blamed for it and it is never read as idle.
    """
    repo = context(bot).repo
    closed = 0
    for turn in await repo.list_overdue_clues(now_iso()):
        game = await repo.get_game(turn["game_id"])
        if game is None:
            continue
        if turn["turn_message_id"] is None:
            closed += await abandon_unposted(bot, game, turn)
        elif await skip_turn(bot, game, turn, "no_clue", "no_clue"):
            closed += 1
    for turn in await repo.list_overdue_guessing(now_iso()):
        game = await repo.get_game(turn["game_id"])
        if game is None:
            continue
        if turn["guess_message_id"] is None:
            closed += await abandon_unposted(bot, game, turn)
        elif await close_turn(bot, game, turn, cause="deadline"):
            closed += 1
    return closed


async def abandon_unposted(bot, game, turn) -> int:
    """Abandon an overdue turn whose message was never posted, then move on. 1 when it closed."""
    if not await context(bot).repo.close_turn(turn["id"], "abandoned"):
        return 0
    log.info("spectrum game %s: turn %s closed (abandoned)", game["id"], turn["id"])
    await advance(bot, game)
    return 1


async def resume_stalled(bot, grace: int = STALL_GRACE_SECONDS) -> int:
    """Move on every running game that has had no open turn for ``grace`` seconds."""
    repo = context(bot).repo
    before = (now() - timedelta(seconds=grace)).isoformat(timespec="seconds")
    resumed = 0
    for game in await repo.list_stalled_games(before):
        log.info("spectrum game %s: no open turn, resuming", game["id"])
        await advance(bot, game)
        resumed += 1
    return resumed


async def leaderboard_reply(bot, guild_id: int, caller_id: int, member_id: int | None = None):
    """The all-time standings for a server, or one member's numbers."""
    repo = context(bot).repo
    rows = standings(await repo.finished_turns(guild_id), await repo.games_played(guild_id))
    if member_id is not None:
        name = await display_name(bot, guild_id, member_id)
        return member_text(name, next((s for s in rows if s.user_id == member_id), None))
    shown = leaderboard_rows(rows, caller_id)
    names = {s.user_id: await display_name(bot, guild_id, s.user_id) for _, s in shown}
    return leaderboard_text(shown, names)

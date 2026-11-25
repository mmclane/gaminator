"""Fifty-Fifty text and the game flow that touches Discord."""

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
from .prompts import fill, pick_topics, question_text, topic_text
from .rules import (
    ABANDONED_TO_STOP,
    DEFAULT_ROUNDS,
    GAME_OVER,
    MAX_PLAYERS,
    MIN_PLAYERS,
    OPTION_LIMIT,
    WRITE_OVER,
    WRITING_CLOSED,
    ScoredQuestion,
    Standing,
    best_question,
    best_questions,
    clean_option,
    game_points,
    game_winners,
    leaderboard_rows,
    min_players,
    next_host,
    nobody_voted_streak,
    score,
    split_options,
    split_text,
    standings,
    too_few_to_continue,
    too_long,
    write_problem,
)

log = logging.getLogger(__name__)
RNG = random.SystemRandom()
DEFAULT_GAME = {"status": "open", "max_rounds": DEFAULT_ROUNDS}
LEADERBOARD_HINT = "All-time standings: `/gaminator fiftyfifty leaderboard`"
NAME_LIMIT = 32
GAP = "_ _\n"
STALL_GRACE_SECONDS = 30
REVEAL_PAUSE = 3
MESSAGE_LIMIT = 2000
CHANNEL_NOTE = "I couldn't post in the game channel"
EMPTY_OPTION = "Both options are needed, and neither can be empty."
DM_FORMAT = (
    "I need both options: put them on two lines, or split them with `|`, like\n"
    "`eat only soup | eat only salad`"
)
VOTES_ARE_IN = "🥁 The votes are in..."
NOT_PLAYING = "You're not in this game. Join with `/gaminator fiftyfifty join`."
NEXT_ROUND = "You're in from the next round."


class ChannelGone(Exception):
    """The game channel was deleted, or the bot can no longer see or post in it."""


def required_players(bot) -> int:
    return min_players(bot.settings.min_players)


def write_seconds(bot) -> int:
    return bot.settings.fiftyfifty_write_seconds


def vote_seconds(bot) -> int:
    return bot.settings.fiftyfifty_vote_seconds


def title(game) -> str:
    return f"Fifty-Fifty game **{game['name']}**"


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def duration_text(seconds: int) -> str:
    if seconds % 60 == 0:
        return plural(seconds // 60, "minute")
    return plural(seconds, "second")


def safe(name: str) -> str:
    """A display name cut to a sane length, with its markdown made harmless."""
    return discord.utils.escape_markdown(name[:NAME_LIMIT])


def closes(stamp: str | None) -> str:
    deadline = parse_iso(stamp)
    return f"Closes <t:{int(deadline.timestamp()) if deadline else 0}:R>."


def how_it_works(game, write_s: int = 120, vote_s: int = 60, need: int = MIN_PLAYERS) -> str:
    if game["status"] == "open":
        length = (
            f"A game is {plural(DEFAULT_ROUNDS, 'round')} unless the host picks more with "
            "`/gaminator fiftyfifty start rounds:`."
        )
    else:
        length = f"This game runs {plural(game['max_rounds'], 'round')}."
    return (
        "• Each round, everyone writes a would-you-rather, both options, for the group to split "
        "as close to 50/50 as it can. You get a topic idea you can ignore.\n"
        "• Press **Write** for the form, or **Answer by DM** to reply by DM.\n"
        f"• Writing closes when everyone has written or after {duration_text(write_s)}. Then you "
        "vote A or B on each question but your own and guess how many will pick A. Votes stay "
        "hidden until the reveal. A question closes when everyone has voted and guessed or after "
        f"{duration_text(vote_s)}.\n"
        "• The writer scores up to 100 for the most even split the votes allow. Voters score 50 "
        "for guessing the exact A count, or 20 for one off when 4 or more voted.\n"
        f"• {length} The most points wins.\n"
        "• Join or leave at any time with `/gaminator fiftyfifty join` and `leave`. Join during "
        "a round and you're in from the next one.\n"
        "• The game ends early if nobody writes in a round, 2 questions in a row get no votes, "
        f"or fewer than {need} players are left."
    )


def rules_text(game, write_s: int = 120, vote_s: int = 60, need: int = MIN_PLAYERS) -> str:
    return "## ⚖️ How Fifty-Fifty works\n" + how_it_works(game, write_s, vote_s, need)


def lobby_embed(
    game, names: list[str], host_name: str, need: int, write_s: int = 120, vote_s: int = 60
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
    icon = "🏁" if game["status"] == "finished" else "⚖️"
    roster = [f"{n}. {safe(name)}" for n, name in enumerate(names, start=1)]
    embed = discord.Embed(
        title=f"{icon} {game['name']}: {state}"[:256],
        description="\n".join(lines + [""] + roster).strip(),
        colour=colour,
    )
    embed.add_field(
        name="How it works", value=how_it_works(game, write_s, vote_s, need), inline=False
    )
    return embed


def round_line(game, number: int) -> str:
    return f"**Round {number} of {game['max_rounds']}**"


def write_head(game, rnd) -> str:
    return (
        f"{GAP}✍️ {round_line(game, rnd['number'])}: press **Write** to write your own "
        "would-you-rather, both options. Aim for a question this group splits right down the middle."
    )


def write_text(game, rnd, written: int, expected: int) -> str:
    return (
        f"{write_head(game, rnd)}\n{written}/{expected} written. {closes(rnd['write_deadline_at'])}"
    )


def write_closed_text(game, rnd, written: int | None) -> str:
    """The round message once writing is over. ``written`` is None when the game ended instead."""
    if written is None:
        tail = "Writing is closed."
    elif written == 0:
        tail = "Writing is closed: nobody wrote a question."
    else:
        tail = f"Writing is closed: {plural(written, 'question')} to vote on."
    return f"{write_head(game, rnd)}\n{tail}"


def asked(q) -> str:
    """A question row on one line."""
    return question_text(q["option_a"], q["option_b"], q["answer"])


def question_lines(q, total: int) -> str:
    return (
        f"{GAP}🗳️ **Question {q['position']} of {total}** (round {q['round_number']})\n"
        "## Would you rather\n"
        f"> 🅰️ {fill(q['option_a'], q['answer'])}\n"
        f"> 🅱️ {fill(q['option_b'], q['answer'])}\n"
    )


def progress_text(voted: int, guessed: int, eligible: int) -> str:
    return f"{voted}/{eligible} voted, {guessed}/{eligible} guessed."


def vote_text(game, q, total: int, voted: int, eligible: int, guessed: int = 0) -> str:
    return (
        f"{question_lines(q, total)}\n"
        "Pick A or B, then guess how many will pick A. Votes are hidden until the reveal, and "
        "you can change both until it closes.\n"
        f"{progress_text(voted, guessed, eligible)} {closes(q['deadline_at'])}"
    )


def voted_text(choice: str, guess: int | None) -> str:
    """The private reply to a vote, which carries the guess menu."""
    picked = f"You picked **{choice.upper()}**."
    if guess is None:
        return f"{picked} Now guess how many players will pick A, counting yourself."
    return (
        f"{picked} Your guess stands at **{guess}** for A. "
        "You can change both until the question closes."
    )


def guessed_text(guess: int) -> str:
    return f"You guessed **{guess}** for A. You can change it until the question closes."


def vote_closed_text(game, q, total: int) -> str:
    return f"{question_lines(q, total)}\nVoting is closed."


def name_list(names: list[str]) -> str:
    bold = [f"**{n}**" for n in names]
    if len(bold) <= 1:
        return "".join(bold)
    return ", ".join(bold[:-1]) + " and " + bold[-1]


def guess_line(guessers: list[tuple[str, int]], names: bool = True) -> str:
    """Who scored guess points. With ``names`` False it gives counts, for a reveal that ran long."""
    if not guessers:
        return "🎯 Nobody guessed the split."
    if names:
        return "🎯 Guessed the split: " + ", ".join(f"**{safe(n)}** +{p}" for n, p in guessers)
    tally: dict[int, int] = {}
    for _, points in guessers:
        tally[points] = tally.get(points, 0) + 1
    parts = [f"{plural(count, 'player')} +{points}" for points, count in sorted(tally.items())]
    return "🎯 Guessed the split: " + ", ".join(reversed(parts))


def reveal_text(
    q,
    a_names: list[str],
    b_names: list[str],
    writer_name: str,
    guessers: list[tuple[str, int]] | tuple = (),
) -> str:
    """The reveal: the split, who picked A and B, the writer's points, and the guess points.

    ``guessers`` is (name, points) for each voter whose guess scored.
    """
    a, b = len(a_names), len(b_names)
    lines = [f"📊 **Question {q['position']}**: {asked(q)}"]
    writer = f"✍️ Written by **{safe(writer_name)}**"
    if a + b == 0:
        return "\n".join(lines + ["Nobody voted, so it scores 0.", f"{writer}."])
    picks = [
        f"🅰️ {name_list([safe(n) for n in a_names]) or 'nobody'}",
        f"🅱️ {name_list([safe(n) for n in b_names]) or 'nobody'}",
    ]
    if a + b == 1:
        lines.append("Only 1 vote, so it scores 0. A question needs 2 votes to score.")
        return "\n".join(lines + picks + [f"{writer}."])
    lines.append(f"**A {a}, B {b}**: {split_text(a, b)}.")
    lines += picks
    lines.append(f"{writer}: **{score(a, b)}** {'point' if score(a, b) == 1 else 'points'}.")
    text = "\n".join(lines + [guess_line(list(guessers))])
    if len(text) > MESSAGE_LIMIT:
        text = "\n".join(lines + [guess_line(list(guessers), names=False)])
    return text


def points_list(points: list[tuple[int, int]], names: dict[int, str], unwritten=frozenset()) -> str:
    return ", ".join(
        f"{safe(names.get(uid, '?'))} {total}{' (no question)' if uid in unwritten else ''}"
        for uid, total in points
    )


def round_text(
    game,
    number: int,
    round_points: list[tuple[int, int]],
    totals: list[tuple[int, int]],
    names: dict[int, str],
    unwritten: set[int],
) -> str:
    return (
        f"🏁 {round_line(game, number)} is done.\n"
        f"This round: {points_list(round_points, names, unwritten)}\n"
        f"Totals: {points_list(totals, names)}\n"
        f"Round {number + 1} starts now."
    )


def recap_text(
    game,
    points: list[tuple[int, int]],
    names: dict[int, str],
    rounds: int,
    best: ScoredQuestion | None,
    note: str = "",
) -> str:
    head = f"🏁 {title(game)} is over after {plural(rounds, 'round')}"
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
    if best is not None and best.points > 0:
        question = question_text(best.option_a, best.option_b, best.answer)
        writer = safe(names.get(best.writer_id, "?"))
        lines.append(f"⚖️ Best split: {question} ({best.a} to {best.b}, by {writer})")
    lines.append(LEADERBOARD_HINT)
    return "\n".join(lines)


def standing_text(standing: Standing) -> str:
    return (
        f"{plural(standing.games_won, 'game')} won, {plural(standing.points, 'point')}, "
        f"{standing.games_played} played"
    )


def leaderboard_text(rows: list[tuple[int, Standing]], names: dict[int, str]) -> str:
    if not rows:
        return "No finished Fifty-Fifty games yet. Start one with `/gaminator fiftyfifty create`."
    lines = ["🏆 **Fifty-Fifty: all-time standings**"]
    previous = 0
    for rank, standing in rows:
        if rank != previous + 1:
            lines.append("")
        name = safe(names.get(standing.user_id, "?"))
        lines.append(f"`{rank}.` **{name}**: {standing_text(standing)}")
        previous = rank
    return "\n".join(lines)


def member_text(name: str, standing: Standing | None, best: list[ScoredQuestion]) -> str:
    if standing is None:
        return f"**{safe(name)}** hasn't played a finished game yet."
    written = f"{plural(standing.questions, 'question')} written"
    if standing.questions:
        earned = standing.points - standing.guess_points
        average = (2 * earned + standing.questions) // (2 * standing.questions)
        written += f", {plural(average, 'point')} on average"
    lines = [f"**{safe(name)}**: {standing_text(standing)}", written]
    if standing.guess_points:
        lines.append(f"{plural(standing.guess_points, 'point')} from guessing splits")
    if best:
        lines.append("Best splits:")
        for q in best:
            closed = parse_iso(q.closed_at)
            stamp = f", <t:{int(closed.timestamp())}:d>" if closed else ""
            question = question_text(q.option_a, q.option_b, q.answer)
            lines.append(f"• {q.points}: {question} ({q.a} to {q.b}{stamp})")
    return "\n".join(lines)


def saved_text(option_a: str, option_b: str, cut: bool) -> str:
    lines = ["Saved. Your question now reads:", question_text(option_a, option_b)]
    if cut:
        lines.append(f"(Each option is cut to {OPTION_LIMIT} characters.)")
    lines.append("You can change it until writing closes.")
    return "\n".join(lines)


def place_text(game_name: str, guild_name: str | None) -> str:
    """Which game a DM is about, for a player with questions open in more than one server."""
    name = discord.utils.escape_markdown(game_name)
    if guild_name is None:
        return f"**{name}** in another server"
    return f"**{name}** in **{discord.utils.escape_markdown(guild_name)}**"


def dm_question_text(topic: str | None, current: str | None, place: str | None = None) -> str:
    """The DM that asks for a question. ``current`` is the player's question so far, rendered."""
    lines = ["✍️ Write your Fifty-Fifty would-you-rather" + (f" for {place}" if place else "")]
    if topic:
        lines.append(topic_text(topic))
    if current:
        lines.append(f"Your question so far: {current}")
    lines.append(
        "Reply here with both options, on two lines or split by `|`, like\n"
        "`eat only soup | eat only salad`\n"
        f"Each option can be up to {OPTION_LIMIT} characters. You can send another reply to "
        "change them until writing closes."
    )
    return "\n".join(lines)


def written_so_far(row) -> str | None:
    """A player's own question as text, or None when they haven't written one."""
    return asked(row) if row["answered_at"] else None


def dm_sent_text() -> str:
    return "Check your DMs."


def dm_failed_text() -> str:
    return "I couldn't DM you (your DMs may be closed). Use **Write** instead."


def dm_closed_text() -> str:
    return "Writing has closed for that round."


def dm_saved_text(saved: str, place: str | None = None) -> str:
    return f"For {place}: {saved}" if place else saved


def status_lines(
    game,
    rnd,
    progress: tuple[int, int],
    place: str,
    question=None,
    total: int = 0,
    mine=None,
    pick: str | None = None,
    guessed: int = 0,
    guess: int | None = None,
) -> list[str]:
    """Where a running game stands, for one caller.

    ``progress`` is (done, expected) for the open step. ``place`` is "in" for a player in this
    round, "next" for one who joined during it, and "out" for anyone else. ``mine`` is the
    caller's write_context row, ``pick`` their vote on the open ``question`` and ``guess`` their
    guess at its A votes. ``guessed`` is how many voters have guessed.
    """
    if rnd is None or rnd["closed_at"]:
        return ["The next round is on its way."]
    done, expected = progress
    writing = not rnd["write_closed_at"]
    if writing:
        head = f"✍️ {round_line(game, rnd['number'])}: writing. {done}/{expected} written. "
        lines = [head + closes(rnd["write_deadline_at"])]
    elif question is None:
        return [f"✍️ {round_line(game, rnd['number'])}: the next question is on its way."]
    else:
        number = f"**Question {question['position']} of {total}**"
        lines = [
            f"🗳️ {number} (round {question['round_number']}): {asked(question)}",
            f"{progress_text(done, guessed, expected)} {closes(question['deadline_at'])}",
        ]
    if place == "out":
        lines.append(NOT_PLAYING)
    elif place == "next" or mine is None:
        lines.append(NEXT_ROUND)
    elif writing and mine["answered_at"]:
        lines.append(f"Your question: {asked(mine)}")
    elif writing:
        lines.append("You haven't written your question yet. Press **Write** on the round message.")
        if mine["topic"]:
            lines.append(topic_text(mine["topic"]))
    elif mine["question_id"] == question["id"]:
        lines.append("You wrote this one, so you sit this vote out.")
    elif pick is None:
        lines.append("🗳️ You haven't voted on this question yet.")
    elif guess is None:
        lines.append(f"🗳️ You picked **{pick.upper()}**, and you haven't guessed the split yet.")
    else:
        lines.append(f"🗳️ You picked **{pick.upper()}** and guessed **{guess}** for A.")
    return lines


def join_line(name: str, game, count: int, need: int, mid_round: bool) -> str:
    if game["status"] == "open":
        return f"🙋 **{safe(name)}** joined {title(game)}. {signup_text(count, need)}"
    if mid_round:
        return f"🙋 **{safe(name)}** joined and is in from the next round."
    return f"🙋 **{safe(name)}** joined."


def leave_line(name: str, game, count: int, need: int) -> str:
    if game["status"] == "open":
        return f"🚪 **{safe(name)}** left {title(game)}. {signup_text(count, need)}"
    remain = f"{plural(count, 'player')} remain{'s' if count == 1 else ''}"
    return f"🚪 **{safe(name)}** left. {remain}."


def host_line(name: str) -> str:
    return f"👑 **{safe(name)}** is now the host."


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
        log.warning("fiftyfifty game %s: could not post in %s: %s", game["id"], channel.id, e)
        return None


async def post_with_view(bot, game, text: str, view) -> discord.Message | None:
    """Post a message with buttons, retrying once. Raises ChannelGone when the channel is lost."""
    for attempt in (1, 2):
        try:
            channel = await game_channel(bot, game)
            if channel is not None:
                return await channel.send(text, view=view, allowed_mentions=NO_MENTIONS)
        except (discord.Forbidden, discord.NotFound) as e:
            raise ChannelGone() from e
        except NETWORK_ERRORS as e:
            log.warning(
                "fiftyfifty game %s: could not post a step (attempt %d): %s", game["id"], attempt, e
            )
    return None


async def names_by_id(bot, game, players) -> dict[int, str]:
    return {p["id"]: await display_name(bot, game["guild_id"], p["user_id"]) for p in players}


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


def deadline_in(seconds: int) -> str:
    return (now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


async def start_round(bot, game) -> bool:
    """Open the next round and post its Write and Answer by DM buttons. False when no round went up."""
    from ..dm import round_view

    ctx = context(bot)
    repo = ctx.repo
    topics = pick_topics(ctx.topics, RNG, MAX_PLAYERS, await repo.used_topics(game["id"]))
    rnd = await repo.begin_round(game["id"], topics, deadline_in(write_seconds(bot)))
    if rnd is None:
        return False
    game = await repo.get_game(game["id"])
    written, expected = await repo.write_progress(rnd["id"])
    try:
        message = await post_with_view(
            bot, game, write_text(game, rnd, written, expected), round_view(rnd["id"])
        )
    except ChannelGone:
        log.warning("fiftyfifty game %s: game channel is gone, ending the game", game["id"])
        await repo.abandon_round(rnd["id"])
        await finish(bot, game, note=CHANNEL_NOTE)
        return False
    if message is None:
        log.warning("fiftyfifty game %s: could not post round %s", game["id"], rnd["id"])
        await repo.abandon_round(rnd["id"])
        return False
    await repo.set_write_message(rnd["id"], message.channel.id, message.id)
    log.info(
        "fiftyfifty game %s: round %s started with %d players", game["id"], rnd["id"], expected
    )
    return True


async def refresh_write_message(bot, game, rnd, message=None) -> None:
    from ..dm import round_view

    written, expected = await context(bot).repo.write_progress(rnd["id"])
    message = message or await fetch_message(bot, rnd["write_channel_id"], rnd["write_message_id"])
    if message is None:
        return
    try:
        await message.edit(
            content=write_text(game, rnd, written, expected), view=round_view(rnd["id"])
        )
    except NETWORK_ERRORS:
        pass


async def submit_question(
    bot, round_id: int, user_id: int, text_a: str, text_b: str, via_dm: bool = False
) -> str:
    """Save a player's two options and return the reply for them: the saved question, or why not.

    ``via_dm`` is True for options sent by DM; a save from the pop-up form clears the DM flag.
    """
    repo = context(bot).repo
    ctx = await repo.write_context(round_id, user_id)
    problem = write_problem(ctx)
    if problem:
        return WRITING_CLOSED if problem == WRITE_OVER else problem
    option_a, option_b = clean_option(text_a), clean_option(text_b, second=True)
    if option_a is None or option_b is None:
        return EMPTY_OPTION
    if not await repo.write_options(ctx["question_id"], option_a, option_b, via_dm):
        return WRITING_CLOSED
    rnd = await repo.get_round(round_id)
    game = await repo.get_game(ctx["game_id"])
    if ctx["answered_at"] is None:
        await refresh_write_message(bot, game, rnd)
    if await everyone_wrote(bot, rnd):
        await close_writing(bot, game, rnd, cause="everyone wrote")
    return saved_text(option_a, option_b, too_long(text_a) or too_long(text_b, second=True))


async def submit_dm(bot, round_id: int, user_id: int, text: str) -> str:
    """Read a DM as a player's two options and save them, or explain the format."""
    options = split_options(text)
    if options is None:
        return DM_FORMAT
    return await submit_question(bot, round_id, user_id, options[0], options[1], via_dm=True)


async def everyone_wrote(bot, rnd) -> bool:
    written, expected = await context(bot).repo.write_progress(rnd["id"])
    return written >= expected


async def close_writing(bot, game, rnd, cause: str = "everyone wrote") -> bool:
    """Close the writing window, then ask the first question.

    Returns False when writing had already closed.
    """
    repo = context(bot).repo
    if not await repo.close_writing(rnd["id"], RNG):
        return False
    written = await repo.question_count(rnd["id"])
    log.info(
        "fiftyfifty game %s: writing for round %s closed with %d questions (%s)",
        game["id"],
        rnd["id"],
        written,
        cause,
    )
    message = await fetch_message(bot, rnd["write_channel_id"], rnd["write_message_id"])
    if message is not None:
        try:
            await message.edit(content=write_closed_text(game, rnd, written), view=None)
        except NETWORK_ERRORS:
            pass
    await advance(bot, game)
    return True


async def idle_note(bot, game) -> str | None:
    """Why a game nobody is playing should stop, or None. Abandoned rounds are never read as idle."""
    repo = context(bot).repo
    if await repo.last_round_answers(game["id"]) == 0:
        return "nobody wrote a question this round"
    if nobody_voted_streak(await repo.asked_vote_totals(game["id"])):
        return "nobody voted on the last 2 questions"
    return None


async def advance(bot, game) -> None:
    """Take the game's next step: ask a question, end the round, start a round, or finish."""
    repo = context(bot).repo
    game = await repo.get_game(game["id"])
    if game is None or game["status"] != "active":
        return
    rnd = await repo.get_current_round(game["id"])
    if rnd is None or rnd["closed_at"]:
        if await repo.abandoned_streak(game["id"]) >= ABANDONED_TO_STOP:
            await finish(bot, game, note=CHANNEL_NOTE)
        elif await repo.rounds_played(game["id"]) >= game["max_rounds"]:
            await finish(bot, game)
        elif note := await idle_note(bot, game):
            await finish(bot, game, note=note)
        else:
            await start_round(bot, game)
        return
    if not rnd["write_closed_at"] or await repo.current_question(rnd["id"]) is not None:
        return
    q = await repo.next_question(rnd["id"])
    if q is None:
        await end_round(bot, game, rnd)
    elif note := await idle_note(bot, game):
        await finish(bot, game, note=note)
    else:
        await open_next_question(bot, game, q)


async def open_next_question(bot, game, q) -> bool:
    """Open a question for votes and post it. False when it did not go up.

    A question that can't be posted is abandoned and the sweep asks the next one after the
    stall grace period, so a short outage costs one question, not the rest of the round.
    """
    from ..vote import vote_view

    repo = context(bot).repo
    if not await repo.open_question(q["id"], deadline_in(vote_seconds(bot))):
        return False
    q = await repo.get_question(q["id"])
    total = await repo.question_count(q["round_id"])
    voted, eligible = await repo.vote_progress(q["id"])
    try:
        message = await post_with_view(
            bot, game, vote_text(game, q, total, voted, eligible), vote_view(q["id"])
        )
    except ChannelGone:
        log.warning("fiftyfifty game %s: game channel is gone, ending the game", game["id"])
        await finish(bot, game, note=CHANNEL_NOTE)
        return False
    if message is None:
        log.warning("fiftyfifty game %s: could not post question %s", game["id"], q["id"])
        await repo.abandon_question(q["id"])
        return False
    await repo.set_vote_message(q["id"], message.channel.id, message.id)
    q = await repo.get_question(q["id"])
    if await everyone_done(bot, q):
        await close_question(bot, game, q, cause="nobody left to vote")
    return True


async def refresh_vote_message(bot, game, q, message=None) -> None:
    repo = context(bot).repo
    voted, eligible = await repo.vote_progress(q["id"])
    guessed, _ = await repo.guess_progress(q["id"])
    total = await repo.question_count(q["round_id"])
    message = message or await fetch_message(bot, q["vote_channel_id"], q["vote_message_id"])
    if message is None:
        return
    try:
        await message.edit(content=vote_text(game, q, total, voted, eligible, guessed))
    except NETWORK_ERRORS:
        pass


async def everyone_done(bot, q) -> bool:
    """True when every voter still in the game has both voted and guessed."""
    repo = context(bot).repo
    voted, eligible = await repo.vote_progress(q["id"])
    guessed, _ = await repo.guess_progress(q["id"])
    return voted >= eligible and guessed >= eligible


async def step_after_input(bot, game, q, message=None) -> None:
    """After a vote or a guess: close the question when everyone is done, or show the progress."""
    if await everyone_done(bot, q):
        await close_question(bot, game, q, cause="everyone voted and guessed")
    else:
        await refresh_vote_message(bot, game, q, message)


async def close_question(bot, game, q, cause: str = "everyone voted and guessed") -> bool:
    """Close the question, pause, reveal the split, the names and the points, then move on.

    The question is closed in the database before the pause, so a crash during it is picked up
    by ``resume_stalled``.

    Returns False when the game is not running or the question was already closed.
    """
    repo = context(bot).repo
    game = await repo.get_game(game["id"])
    if game is None or game["status"] != "active":
        return False
    if not await repo.close_question(q["id"]):
        return False
    log.info("fiftyfifty game %s: question %s closed (%s)", game["id"], q["id"], cause)
    try:
        q = await repo.get_question(q["id"])
        message = await fetch_message(bot, q["vote_channel_id"], q["vote_message_id"])
        if message is not None:
            total = await repo.question_count(q["round_id"])
            try:
                await message.edit(content=vote_closed_text(game, q, total), view=None)
            except NETWORK_ERRORS:
                pass
        await post(bot, game, VOTES_ARE_IN)
        await asyncio.sleep(REVEAL_PAUSE)
        await post(bot, game, await reveal_for(bot, game, q))
    except Exception:
        log.exception("fiftyfifty game %s: reveal of question %s failed", game["id"], q["id"])
    await advance(bot, game)
    return True


async def reveal_for(bot, game, q) -> str:
    """The reveal text for a closed question, read from its votes and guesses."""
    repo = context(bot).repo
    votes = await repo.voters(q["id"])
    writer = await repo.get_player_by_id(q["writer_id"])
    scored = ScoredQuestion(
        writer["user_id"],
        q["option_a"],
        q["option_b"],
        q["answer"],
        sum(v["choice"] == "a" for v in votes),
        sum(v["choice"] == "b" for v in votes),
        q["closed_at"],
        await repo.guesses(q["id"]),
    )
    earned = scored.guess_points
    users = {writer["user_id"], *(v["user_id"] for v in votes), *(uid for uid, _ in earned)}
    names = await names_by_user(bot, game, sorted(users))
    return reveal_text(
        q,
        [names[v["user_id"]] for v in votes if v["choice"] == "a"],
        [names[v["user_id"]] for v in votes if v["choice"] == "b"],
        names[writer["user_id"]],
        [(names[uid], points) for uid, points in earned],
    )


async def end_round(bot, game, rnd) -> None:
    """Close a round whose questions have all been asked, post its results, and move on."""
    repo = context(bot).repo
    if not await repo.close_round(rnd["id"]):
        return
    log.info("fiftyfifty game %s: round %s closed", game["id"], rnd["id"])
    more = await repo.rounds_played(game["id"]) < game["max_rounds"]
    if more and not await idle_note(bot, game):
        try:
            roster = [p["user_id"] for p in await repo.round_roster(rnd["id"])]
            round_points = game_points(await repo.closed_questions(game["id"], rnd["id"]), roster)
            totals = game_points(
                await repo.closed_questions(game["id"]), await repo.round_user_ids(game["id"])
            )
            names = await names_by_user(bot, game, [uid for uid, _ in totals])
            unwritten = set(await repo.unwritten_user_ids(rnd["id"]))
            await post(
                bot, game, round_text(game, rnd["number"], round_points, totals, names, unwritten)
            )
        except Exception:
            log.exception("fiftyfifty game %s: results of round %s failed", game["id"], rnd["id"])
    await advance(bot, game)


async def finish(bot, game, note: str = "") -> bool:
    """End the game and post the recap. False, posting nothing, when it had already ended."""
    repo = context(bot).repo
    rnd = await repo.get_current_round(game["id"])
    open_round = rnd is not None and not rnd["closed_at"]
    q = await repo.current_question(rnd["id"]) if open_round else None
    rounds = await repo.rounds_played(game["id"])
    if open_round and rnd["write_closed_at"]:
        rounds += 1
    if not await repo.finish_game(game["id"]):
        return False
    log.info("fiftyfifty game %s: finished (%s)", game["id"], note or "all rounds played")
    if q is not None:
        message = await fetch_message(bot, q["vote_channel_id"], q["vote_message_id"])
        if message is not None:
            total = await repo.question_count(q["round_id"])
            try:
                await message.edit(content=vote_closed_text(game, q, total), view=None)
            except NETWORK_ERRORS:
                pass
    elif open_round and not rnd["write_closed_at"]:
        message = await fetch_message(bot, rnd["write_channel_id"], rnd["write_message_id"])
        if message is not None:
            try:
                await message.edit(content=write_closed_text(game, rnd, None), view=None)
            except NETWORK_ERRORS:
                pass
    questions = await repo.closed_questions(game["id"])
    points = game_points(questions, await repo.round_user_ids(game["id"]))
    names = await names_by_user(bot, game, [uid for uid, _ in points])
    text = recap_text(game, points, names, rounds, best_question(questions), note)
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
        rows = standings(await repo.finished_questions(guild_id), await repo.games_played(guild_id))
        if not rows:
            return
        shown = leaderboard_rows(rows, 0)
        names = {s.user_id: await display_name(bot, guild_id, s.user_id) for _, s in shown}
        await post(bot, game, GAP + leaderboard_text(shown, names))
    except Exception:
        log.exception("fiftyfifty game %s: standings after the game failed", game["id"])


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
            log.info("fiftyfifty game %s: lobby card is gone, not updating it", game_id)
            return
        embed = lobby_embed(
            game, names, host_name, required_players(bot), write_seconds(bot), vote_seconds(bot)
        )
        try:
            await message.edit(embed=embed, view=lobby_view(game))
        except NETWORK_ERRORS as e:
            log.info("fiftyfifty game %s: could not update the lobby card: %s", game_id, e)


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
    log.info("fiftyfifty game %s: started with %d players", game["id"], count)
    await start_round(bot, game)
    await refresh_lobby(bot, game["id"])
    return f"{title(game)} has started with {count} players and {plural(rounds, 'round')}."


async def refresh_open_step(bot, game, rnd) -> None:
    """Update the progress line on whichever message is collecting questions or votes."""
    if not rnd["write_closed_at"]:
        await refresh_write_message(bot, game, rnd)
        return
    q = await context(bot).repo.current_question(rnd["id"])
    if q is not None:
        await refresh_vote_message(bot, game, q)


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
    mid_round = open_round and not on_roster
    await post(bot, game, join_line(name, game, count, required_players(bot), mid_round))
    await refresh_lobby(bot, game["id"])
    if mid_round:
        return NEXT_ROUND
    if on_roster:
        await refresh_open_step(bot, game, rnd)
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
    return "You've left. Your question, votes and guesses so far still count."


async def after_leave(bot, game, player) -> None:
    """A player left: pass the host role on, then end the game or keep the open step moving."""
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
            log.info("fiftyfifty game %s: host passed to %s", game["id"], heir["user_id"])
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
    if not rnd["write_closed_at"]:
        if await everyone_wrote(bot, rnd):
            await close_writing(bot, game, rnd, cause="a player left")
        else:
            await refresh_write_message(bot, game, rnd)
        return
    q = await repo.current_question(rnd["id"])
    if q is None:
        return
    if await everyone_done(bot, q):
        await close_question(bot, game, q, cause="a player left")
    else:
        await refresh_vote_message(bot, game, q)


async def status_reply(bot, game, user_id: int) -> str:
    """The private status for one caller: the game, its players, and their own question or vote."""
    repo = context(bot).repo
    players = await repo.list_players(game["id"], active_only=True)
    names = await names_by_id(bot, game, players)
    host = safe(await display_name(bot, game["guild_id"], game["host_id"]))
    roster = ", ".join(safe(names[p["id"]]) for p in players) or "nobody"
    lines = [f"{title(game)}, hosted by **{host}**.", f"**Players ({len(players)}):** {roster}"]
    if game["status"] == "open":
        lines.append(f"Sign-ups are open. It needs at least {required_players(bot)} to start.")
        return "\n".join(lines)
    rnd = await repo.get_current_round(game["id"])
    if rnd is None or rnd["closed_at"]:
        return "\n".join(lines + status_lines(game, rnd, (0, 0), "in"))
    mine = await repo.write_context(rnd["id"], user_id)
    if mine is None or mine["player_id"] is None or mine["left_at"] is not None:
        place = "out"
    else:
        place = "in" if mine["on_roster"] and mine["question_id"] else "next"
    if not rnd["write_closed_at"]:
        progress = await repo.write_progress(rnd["id"])
        return "\n".join(lines + status_lines(game, rnd, progress, place, mine=mine))
    q = await repo.current_question(rnd["id"])
    if q is None:
        return "\n".join(lines + status_lines(game, rnd, (0, 0), place))
    vote = await repo.get_vote(q["id"], mine["player_id"]) if place == "in" else None
    guess = await repo.get_guess(q["id"], mine["player_id"]) if place == "in" else None
    guessed, _ = await repo.guess_progress(q["id"])
    step = status_lines(
        game,
        rnd,
        await repo.vote_progress(q["id"]),
        place,
        q,
        await repo.question_count(rnd["id"]),
        mine,
        vote["choice"] if vote else None,
        guessed,
        guess["a_votes"] if guess else None,
    )
    return "\n".join(lines + step)


async def close_overdue(bot) -> int:
    """Close every writing window and question whose deadline has passed. Returns how many."""
    repo = context(bot).repo
    closed = 0
    for rnd in await repo.list_overdue_writing(now_iso()):
        game = await repo.get_game(rnd["game_id"])
        if game is not None and await close_writing(bot, game, rnd, cause="deadline"):
            closed += 1
    for q in await repo.list_overdue_questions(now_iso()):
        game = await repo.get_game(q["game_id"])
        if game is not None and await close_question(bot, game, q, cause="deadline"):
            closed += 1
    return closed


async def resume_stalled(bot, grace: int = STALL_GRACE_SECONDS) -> int:
    """Move on every running game that has had no open step for ``grace`` seconds."""
    repo = context(bot).repo
    before = (now() - timedelta(seconds=grace)).isoformat(timespec="seconds")
    resumed = 0
    for game in await repo.list_stalled_games(before):
        log.info("fiftyfifty game %s: no open step, resuming", game["id"])
        await advance(bot, game)
        resumed += 1
    return resumed


async def leaderboard_reply(bot, guild_id: int, caller_id: int, member_id: int | None = None):
    """The all-time standings for a server, or one member's numbers and best splits."""
    repo = context(bot).repo
    games = await repo.finished_questions(guild_id)
    rows = standings(games, await repo.games_played(guild_id))
    if member_id is not None:
        name = await display_name(bot, guild_id, member_id)
        standing = next((s for s in rows if s.user_id == member_id), None)
        return member_text(name, standing, best_questions(games, member_id))
    shown = leaderboard_rows(rows, caller_id)
    names = {s.user_id: await display_name(bot, guild_id, s.user_id) for _, s in shown}
    return leaderboard_text(shown, names)

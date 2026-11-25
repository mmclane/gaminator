"""Headlines text and the game flow that touches Discord."""

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
from .questions import pick_questions
from .rules import (
    ABANDONED_TO_STOP,
    ANSWER_LIMIT,
    ANSWER_OVER,
    BAD_PICK,
    DEFAULT_ROUNDS,
    EMPTY_ANSWER,
    EMPTY_HEADLINE,
    GAME_OVER,
    HEADLINE_LIMIT,
    MAX_PLAYERS,
    MIN_PLAYERS,
    OWN_HEADLINE,
    QUOTED_POINTS,
    VOTE_OVER,
    WRITE_OVER,
    WRITER_POINTS,
    ScoredPairing,
    Standing,
    answer_problem,
    clean_text,
    game_points,
    game_winners,
    idle_reason,
    leaderboard_rows,
    min_players,
    next_host,
    owed_line,
    reveal_pause,
    standings,
    too_few_to_continue,
    top_headline,
    vote_problem,
    write_problem,
)

log = logging.getLogger(__name__)
RNG = random.SystemRandom()
DEFAULT_GAME = {"status": "open", "max_rounds": DEFAULT_ROUNDS}
LEADERBOARD_HINT = "All-time standings: `/gaminator headlines leaderboard`"
NAME_LIMIT = 32
OPTION_LIMIT = 100
RESULT_HEADLINE_LIMIT = 41
PICK_DIGITS = 18
GAP = "_ _\n"
REVEAL_PAUSE = 4
REVEAL_POST_TIMEOUT = 10
STALL_GRACE_SECONDS = 30
CHANNEL_NOTE = "I couldn't post in the game channel"
MESSAGE_BUDGET = 1900
ENDED_LINE = "The game ended."
SKIPPED_LINE = "This round was skipped: I couldn't post the next step."
NEXT_ROUND_REPLY = "You're in from the next round."
BACK_IN = "You're back in."
EMPTY_ROUNDS = {
    "no_answers": "Not enough answers came in, so round {number} is over.",
    "no_headlines": "No headlines came in, so round {number} is over.",
    "one_headline": "A vote needs at least two headlines, so this one is just for fun.",
}


class ChannelGone(Exception):
    """The game channel was deleted, or the bot can no longer see or post in it."""


def required_players(bot) -> int:
    return min_players(bot.settings.min_players)


def answer_seconds(bot) -> int:
    return bot.settings.headlines_answer_seconds


def write_seconds(bot) -> int:
    return bot.settings.headlines_write_seconds


def vote_seconds(bot) -> int:
    return bot.settings.headlines_vote_seconds


def title(game) -> str:
    return f"Headlines game **{discord.utils.escape_markdown(game['name'])}**"


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


def name_list(names: list[str]) -> str:
    bold = [f"**{n}**" for n in names]
    if len(bold) <= 1:
        return "".join(bold)
    return ", ".join(bold[:-1]) + " and " + bold[-1]


def points_list(points: list[tuple[int, int]], names: dict[int, str]) -> str:
    return ", ".join(f"{safe(names.get(uid, '?'))} {total}" for uid, total in points)


def cut(text: str, limit: int) -> str:
    """Text shortened to ``limit`` characters, ending in an ellipsis when something was cut."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def how_it_works(
    game, answer_s: int = 90, write_s: int = 120, vote_s: int = 60, need: int = MIN_PLAYERS
) -> str:
    if game["status"] == "open":
        length = (
            f"A game is {plural(DEFAULT_ROUNDS, 'round')} unless the host picks more with "
            "`/gaminator headlines start rounds:`."
        )
    else:
        length = f"This game runs {plural(game['max_rounds'], 'round')}."
    return (
        "• Each round everyone privately answers their own harmless question in a few words.\n"
        "• Each answer goes to another player, who sees only the answer and writes a news "
        "headline that makes it sound as bad as possible.\n"
        "• The bot reads out each headline with its quote and who said it, but not who wrote "
        "the headline.\n"
        f"• Vote for the best one, never your own headline. Each vote is {WRITER_POINTS} "
        f"points for the headline writer and {QUOTED_POINTS} for the player quoted.\n"
        f"• Answering takes up to {duration_text(answer_s)}, headlines "
        f"{duration_text(write_s)}, the vote {duration_text(vote_s)}. Each closes early once "
        "everyone is done.\n"
        f"• {length} The most points wins.\n"
        "• Join or leave at any time. Joiners are in from the next round.\n"
        "• The game ends early after a round with no answers, no headlines or no votes, or "
        f"with fewer than {need} players left."
    )


def rules_text(
    game, answer_s: int = 90, write_s: int = 120, vote_s: int = 60, need: int = MIN_PLAYERS
) -> str:
    return "## 📰 How Headlines works\n" + how_it_works(game, answer_s, write_s, vote_s, need)


def lobby_embed(
    game,
    names: list[str],
    host_name: str,
    need: int,
    answer_s: int = 90,
    write_s: int = 120,
    vote_s: int = 60,
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
    icon = "🏁" if game["status"] == "finished" else "📰"
    roster = [f"{n}. {safe(name)}" for n, name in enumerate(names, start=1)]
    embed = discord.Embed(
        title=f"{icon} {discord.utils.escape_markdown(game['name'])}: {state}"[:256],
        description="\n".join(lines + [""] + roster).strip(),
        colour=colour,
    )
    embed.add_field(
        name="How it works",
        value=how_it_works(game, answer_s, write_s, vote_s, need),
        inline=False,
    )
    return embed


def round_head(game, rnd, step: str) -> str:
    return f"{GAP}## 📰 Round {rnd['number']} of {game['max_rounds']}: {step}"


def answer_text(game, rnd, answered: int, expected: int) -> str:
    return (
        f"{round_head(game, rnd, 'answer your question')}\n"
        "Press **Answer** to get your own question, privately, and answer it in up to "
        f"{ANSWER_LIMIT} characters. Nobody else sees your question: the player who gets your "
        "answer sees only your words.\n"
        f"{answered}/{expected} answered. {closes(rnd['answer_deadline_at'])}"
    )


def answer_closed_text(game, rnd, why: str, answered: int = 0) -> str:
    """The answer message once its step is over. ``why`` is "done", "ended" or "skipped"."""
    tail = {
        "done": f"Answering is closed: {plural(answered, 'answer')} in.",
        "ended": ENDED_LINE,
        "skipped": SKIPPED_LINE,
    }[why]
    return f"{round_head(game, rnd, 'answer your question')}\n{tail}"


def write_text(game, rnd, written: int, expected: int) -> str:
    return (
        f"{round_head(game, rnd, 'write the headlines')}\n"
        "Press **Write headline** to see the quote you've been handed, and write a news "
        f"headline of up to {HEADLINE_LIMIT} characters that makes it sound as bad as "
        "possible. You won't see the question or who said it.\n"
        f"{written}/{expected} headlines in. {closes(rnd['write_deadline_at'])}"
    )


def write_closed_text(game, rnd, why: str, written: int = 0) -> str:
    """The headline message once its step is over. ``why`` is "done", "ended" or "skipped"."""
    tail = {
        "done": f"Headlines are closed: {written} to read.",
        "ended": ENDED_LINE,
        "skipped": SKIPPED_LINE,
    }[why]
    return f"{round_head(game, rnd, 'write the headlines')}\n{tail}"


def empty_round_line(game, rnd, outcome: str) -> str:
    """The channel line for a round that ends with no vote. ``outcome`` is the round's."""
    return f"{GAP}⏭️ {EMPTY_ROUNDS[outcome].format(number=rnd['number'])}"


def reveal_intro(game, rnd, count: int) -> str:
    stories = f"{count} stor{'y' if count == 1 else 'ies'} tonight."
    if count > 1:
        stories += " The vote opens after the last one."
    return f"{round_head(game, rnd, 'the headlines')}\n{stories}"


def pairing_text(position: int, total: int, headline: str, answer: str, quoted_name: str) -> str:
    """One headline with its quote and who said it. The writer is never part of it."""
    return (
        f"{GAP}📰 **{position} of {total}**\n"
        f"## {discord.utils.escape_markdown(headline)}\n"
        f'> "{discord.utils.escape_markdown(answer)}" ({safe(quoted_name)})'
    )


def vote_text(game, rnd, voted: int, eligible: int) -> str:
    return (
        f"{round_head(game, rnd, 'vote for the best headline')}\n"
        "Pick your favorite from the menu. Votes stay hidden until the result, and you can "
        "change yours until it closes. You can't vote for a headline you wrote, but you can "
        "vote for one that quotes you.\n"
        f"{voted}/{eligible} voted. {closes(rnd['vote_deadline_at'])}"
    )


def vote_closed_text(game, rnd) -> str:
    return f"{round_head(game, rnd, 'vote for the best headline')}\nVoting is closed."


def vote_option(pairing_id: int, position: int, headline: str, answer: str):
    """One entry of the vote menu. Discord shows its text as typed, so nothing is escaped."""
    return discord.SelectOption(
        label=cut(f"{position}. {headline}", OPTION_LIMIT),
        value=str(pairing_id),
        description=cut(answer, OPTION_LIMIT),
    )


def result_messages(
    game,
    rnd,
    rows: list[ScoredPairing],
    names: dict[int, str],
    totals: list[tuple[int, int]] | None = None,
) -> list[str]:
    """The round's result, most votes first, as one or more messages of whole lines.

    ``totals`` adds the running totals and the next round's start, so pass it only while rounds
    remain.
    """
    lines = []
    for row in sorted(rows, key=lambda r: (-r.votes, r.position)):
        points = row.points()
        headline = discord.utils.escape_markdown(cut(row.headline, RESULT_HEADLINE_LIMIT))
        lines.append(
            f"`{row.position}.` {headline} ({plural(row.votes, 'vote')}): "
            f"headline by **{safe(names.get(row.writer_id, '?'))}** +{points[row.writer_id]}, "
            f"quote from **{safe(names.get(row.quoted_id, '?'))}** +{points[row.quoted_id]}"
        )
    if totals is not None:
        lines.append("Totals: " + points_list(totals, names))
        lines.append(f"Round {rnd['number'] + 1} starts now.")
    parts = []
    current = f"{GAP}## 🏆 Round {rnd['number']} of {game['max_rounds']}: the results"
    for line in lines:
        if len(current) + 1 + len(line) > MESSAGE_BUDGET:
            parts.append(current)
            current = GAP + line
        else:
            current += "\n" + line
    return [*parts, current]


def recap_text(
    game,
    points: list[tuple[int, int]],
    names: dict[int, str],
    rounds: int,
    top: ScoredPairing | None,
    note: str = "",
    unvoted_round: int | None = None,
) -> str:
    head = f"{GAP}🏁 {title(game)} is over after {plural(rounds, 'round')}"
    lines = [head + (f" ({note})." if note else ".")]
    winners = game_winners(points)
    if not winners:
        lines.append("Nobody scored.")
    else:
        best = points[0][1]
        winner_names = [safe(names.get(i, "?")) for i in winners]
        if len(winners) == 1:
            lines.append(f"🏆 **{winner_names[0]}** wins with {plural(best, 'point')}!")
        else:
            lines.append(f"🏆 {name_list(winner_names)} tie with {plural(best, 'point')} each!")
        rest = [(i, n) for i, n in points if i not in winners]
        if rest:
            lines.append("Also: " + points_list(rest, names))
    if unvoted_round is not None:
        lines.append(f"Round {unvoted_round}'s headlines were not voted on, so they score nothing.")
    if top is not None:
        lines.append(
            f'📰 Top headline: "{discord.utils.escape_markdown(top.headline)}" by '
            f"**{safe(names.get(top.writer_id, '?'))}**, quoting "
            f"**{safe(names.get(top.quoted_id, '?'))}** ({plural(top.votes, 'vote')})"
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
        return "No finished Headlines games yet. Start one with `/gaminator headlines create`."
    lines = [f"{GAP}## 🏆 Headlines: all-time standings"]
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


def question_block(question: str) -> str:
    """The text at the top of the answer pop-up."""
    return (
        "**Your question**\n"
        f"{discord.utils.escape_markdown(question)}\n"
        "Only you see this. The player who gets your answer sees only your words."
    )


def quote_block(quote: str) -> str:
    """The text at the top of the headline pop-up: the quote, never its question or author."""
    return (
        "**Your quote**\n"
        f'> "{discord.utils.escape_markdown(quote)}"\n'
        "Write a news headline that makes this quote sound as bad as possible. You don't see "
        "the question or who said it."
    )


def answer_saved_text(answer: str) -> str:
    return (
        f'Saved. Your answer: "{discord.utils.escape_markdown(answer)}". '
        "You can change it until answering closes."
    )


def headline_saved_text(headline: str) -> str:
    return (
        f"Saved. Your headline: **{discord.utils.escape_markdown(headline)}**. "
        "You can change it until headlines close."
    )


def vote_saved_text(position: int) -> str:
    return f"Your vote is on headline **{position}**. You can change it until voting closes."


def status_lines(
    game,
    rnd,
    step: str | None,
    progress: tuple[int, int] = (0, 0),
    deadline: str | None = None,
    owed: str | None = None,
) -> list[str]:
    """Where a running game stands, plus what the caller still owes. No game content.

    ``step`` is "answering", "writing", "reveal" or "voting", or None between rounds.
    """
    if rnd is None or step is None:
        return ["The next round is on its way."]
    place = f"Round {rnd['number']} of {game['max_rounds']}"
    done, expected = progress
    if step == "reveal":
        lines = [f"{place}: the headlines are being read out."]
    elif step == "answering":
        lines = [f"{place}: answering. {done}/{expected} answered. {closes(deadline)}"]
    elif step == "writing":
        lines = [f"{place}: writing headlines. {done}/{expected} headlines in. {closes(deadline)}"]
    else:
        lines = [f"{place}: voting. {done}/{expected} voted. {closes(deadline)}"]
    if owed:
        lines.append(owed)
    return lines


def join_line(name: str, game, count: int, need: int, mid_round: bool = False) -> str:
    if game["status"] == "open":
        return f"{GAP}🙋 **{safe(name)}** joined {title(game)}. {signup_text(count, need)}"
    if mid_round:
        return f"{GAP}🙋 **{safe(name)}** joined and is in from the next round."
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


async def post(bot, game, text: str, *, suppress_embeds: bool = False):
    """Post in the game channel; fall back to the shared announce channel."""
    try:
        channel = await game_channel(bot, game)
    except ChannelGone:
        channel = None
    if channel is None:
        await announce(bot, game["guild_id"], text)
        return None
    try:
        return await channel.send(
            text, allowed_mentions=NO_MENTIONS, suppress_embeds=suppress_embeds
        )
    except NETWORK_ERRORS as e:
        log.warning("headlines game %s: could not post in %s: %s", game["id"], channel.id, e)
        return None


async def post_with_view(
    bot, game, text: str, view, *, suppress_embeds: bool = False
) -> discord.Message | None:
    """Post a step message, retrying once. Raises ChannelGone when the channel is lost.

    ``view`` holds the step's button or menu, or is None for a message with neither.
    """
    for attempt in (1, 2):
        try:
            channel = await game_channel(bot, game)
            if channel is not None:
                return await channel.send(
                    text, view=view, allowed_mentions=NO_MENTIONS, suppress_embeds=suppress_embeds
                )
        except (discord.Forbidden, discord.NotFound) as e:
            raise ChannelGone() from e
        except NETWORK_ERRORS as e:
            log.warning(
                "headlines game %s: could not post a step (attempt %d): %s", game["id"], attempt, e
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
    """Replace a game message's text and take its button or menu away. Network errors are ignored."""
    message = await fetch_message(bot, game["channel_id"], message_id)
    if message is None:
        return
    try:
        await message.edit(content=text, view=None, allowed_mentions=NO_MENTIONS)
    except NETWORK_ERRORS:
        pass


def deadline_in(seconds: int) -> str:
    return (now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def step_of(rnd) -> str | None:
    """The open step of a round: "answering", "writing", "reveal" or "voting". None when closed."""
    if rnd is None or rnd["closed_at"]:
        return None
    if not rnd["answer_closed_at"]:
        return "answering"
    if not rnd["write_closed_at"]:
        return "writing"
    if not rnd["vote_deadline_at"]:
        return "reveal"
    return "voting"


async def start_round(bot, game) -> bool:
    """Deal the next round and post its answer step. False when no round is left open by it.

    A round whose message can't be posted is abandoned, and the sweep starts the next one after
    the stall grace period, so a short outage costs one round and not the game.
    """
    from ..forms import answer_view

    ctx = context(bot)
    repo = ctx.repo
    questions = pick_questions(
        ctx.questions, RNG, MAX_PLAYERS, await repo.used_questions(game["id"])
    )
    rnd = await repo.begin_round(game["id"], questions, deadline_in(answer_seconds(bot)))
    if rnd is None:
        return False
    log.info("headlines game %s: round %s started", game["id"], rnd["id"])
    answered, expected = await repo.answer_progress(rnd["id"])
    try:
        message = await post_with_view(
            bot, game, answer_text(game, rnd, answered, expected), answer_view(rnd["id"])
        )
    except ChannelGone:
        log.warning("headlines game %s: game channel is gone, ending the game", game["id"])
        await repo.abandon_round(rnd["id"])
        await finish(bot, game, note=CHANNEL_NOTE)
        return False
    if message is None:
        await abandon_unposted(bot, game, rnd, "answer step")
        return False
    await repo.set_answer_message(rnd["id"], message.id)
    await close_if_round_closed(
        bot, game, rnd["id"], message.id, answer_closed_text(game, rnd, "ended")
    )
    return True


async def abandon_unposted(bot, game, rnd, what: str) -> bool:
    """Abandon a round whose next message could not be posted. False when it had closed."""
    log.warning("headlines game %s: could not post the %s of round %s", game["id"], what, rnd["id"])
    if not await context(bot).repo.abandon_round(rnd["id"]):
        return False
    log.info("headlines game %s: round %s closed (abandoned)", game["id"], rnd["id"])
    return True


async def close_if_round_closed(bot, game, round_id: int, message_id: int, text: str) -> bool:
    """Close a step message that went up while its round was being closed. True when it did.

    An ``end``, or a leave below the minimum, can run while a post is in flight. The message is
    then edited to ``text`` with no button or menu, so nothing live sits under the recap.
    """
    rnd = await context(bot).repo.get_round(round_id)
    if rnd is not None and not rnd["closed_at"]:
        return False
    await edit_message(bot, game, message_id, text)
    return True


async def idle_note(bot, game) -> str | None:
    """Why a game nobody is playing should stop, or None.

    Read from what was stored when the last round closed, never from who is in the game now.
    Abandoned rounds count for nothing.
    """
    row = await context(bot).repo.last_round_activity(game["id"])
    if row is None:
        return None
    return idle_reason(row["outcome"], row["eligible_voters"], row["votes"])


async def advance(bot, game) -> None:
    """Take the game's next step: start a round, pick a stalled reveal back up, or finish."""
    repo = context(bot).repo
    game = await repo.get_game(game["id"])
    if game is None or game["status"] != "active":
        return
    rnd = await repo.get_current_round(game["id"])
    step = step_of(rnd)
    if step is None:
        if await repo.abandoned_streak(game["id"]) >= ABANDONED_TO_STOP:
            await finish(bot, game, note=CHANNEL_NOTE)
        elif await repo.rounds_played(game["id"]) >= game["max_rounds"]:
            await finish(bot, game)
        elif note := await idle_note(bot, game):
            await finish(bot, game, note=note)
        else:
            await start_round(bot, game)
    elif step == "reveal":
        await reveal_round(bot, game, rnd)


async def submit_answer(bot, round_id: int, user_id: int, raw: str) -> str:
    """Save or change a player's answer. Returns the reply for them."""
    repo = context(bot).repo
    ctx = await repo.round_context(round_id, user_id)
    problem = answer_problem(ctx)
    if problem:
        return problem
    text = clean_text(raw, ANSWER_LIMIT)
    if text is None:
        return EMPTY_ANSWER
    if not await repo.save_answer(round_id, ctx["player_id"], text):
        return ANSWER_OVER
    if ctx["answer"] is None:
        await refresh_open_step(bot, await repo.get_game(ctx["game_id"]), round_id)
    return answer_saved_text(text)


async def submit_headline(bot, round_id: int, user_id: int, raw: str) -> str:
    """Save or change a player's headline for the quote they were handed. Returns their reply."""
    repo = context(bot).repo
    ctx = await repo.round_context(round_id, user_id)
    problem = write_problem(ctx)
    if problem:
        return problem
    text = clean_text(raw, HEADLINE_LIMIT)
    if text is None:
        return EMPTY_HEADLINE
    if not await repo.save_headline(ctx["pairing_id"], ctx["player_id"], text):
        return WRITE_OVER
    if ctx["headline"] is None:
        await refresh_open_step(bot, await repo.get_game(ctx["game_id"]), round_id)
    return headline_saved_text(text)


async def submit_vote(bot, round_id: int, user_id: int, value: str) -> str:
    """Save or change a player's vote. ``value`` is the picked menu entry. Returns their reply."""
    repo = context(bot).repo
    ctx = await repo.round_context(round_id, user_id)
    problem = vote_problem(ctx)
    if problem:
        return problem
    if not (value.isascii() and value.isdecimal() and len(value) <= PICK_DIGITS):
        return BAD_PICK
    pairing = await repo.get_pairing(int(value))
    if pairing is None or pairing["round_id"] != round_id or pairing["position"] is None:
        return BAD_PICK
    if pairing["writer_id"] == ctx["player_id"]:
        return OWN_HEADLINE
    if not await repo.cast_vote(round_id, ctx["player_id"], pairing["id"]):
        return VOTE_OVER
    if ctx["my_vote"] is None:
        await refresh_open_step(bot, await repo.get_game(ctx["game_id"]), round_id)
    return vote_saved_text(pairing["position"])


async def close_step_if_done(bot, round_id: int, cause: str | None = None) -> bool:
    """Close the open step once everyone still in has done their part. True when it closed.

    Called after a pop-up or a vote has been answered, so a reveal never delays a reply.
    """
    repo = context(bot).repo
    rnd = await repo.get_round(round_id)
    step = step_of(rnd)
    if step in (None, "reveal"):
        return False
    game = await repo.get_game(rnd["game_id"])
    if game is None or game["status"] != "active":
        return False
    if step == "answering":
        done, expected = await repo.answer_progress(round_id)
        return done >= expected and await close_answering(
            bot, game, rnd, cause or "everyone answered"
        )
    if step == "writing":
        done, expected = await repo.write_progress(round_id)
        return done >= expected and await close_writing_step(
            bot, game, rnd, cause or "every headline is in"
        )
    done, expected = await repo.vote_progress(round_id)
    return done >= expected and await close_voting(bot, game, rnd, cause or "everyone voted")


async def refresh_open_step(bot, game, round_id: int) -> None:
    """Update the progress line of the open step's message.

    Only the text is edited, so the message keeps its button or menu.
    """
    repo = context(bot).repo
    rnd = await repo.get_round(round_id)
    step = step_of(rnd)
    if step == "answering":
        message_id = rnd["answer_message_id"]
        text = answer_text(game, rnd, *await repo.answer_progress(round_id))
    elif step == "writing":
        message_id = rnd["write_message_id"]
        text = write_text(game, rnd, *await repo.write_progress(round_id))
    elif step == "voting":
        message_id = rnd["vote_message_id"]
        text = vote_text(game, rnd, *await repo.vote_progress(round_id))
    else:
        return
    message = await fetch_message(bot, game["channel_id"], message_id)
    if message is None:
        return
    try:
        await message.edit(content=text, allowed_mentions=NO_MENTIONS)
    except NETWORK_ERRORS:
        pass


async def close_answering(bot, game, rnd, cause: str) -> bool:
    """Close answering, hand the answers out and open headline writing.

    The headline message goes up before the answer message changes. False when answering had
    already been closed.
    """
    from ..forms import write_view

    repo = context(bot).repo
    counts = await repo.close_answers(rnd["id"], RNG, deadline_in(write_seconds(bot)))
    if counts is None:
        return False
    answers, pairings = counts
    log.info(
        "headlines game %s: answering closed for round %s (%s): %d answers, %d pairings",
        game["id"],
        rnd["id"],
        cause,
        answers,
        pairings,
    )
    rnd = await repo.get_round(rnd["id"])
    closed_text = answer_closed_text(game, rnd, "done", answers)
    if rnd["closed_at"]:
        log.info("headlines game %s: round %s closed (%s)", game["id"], rnd["id"], rnd["outcome"])
        await edit_message(bot, game, rnd["answer_message_id"], closed_text)
        await post(bot, game, empty_round_line(game, rnd, "no_answers"))
        await advance(bot, game)
        return True
    written, expected = await repo.write_progress(rnd["id"])
    try:
        message = await post_with_view(
            bot, game, write_text(game, rnd, written, expected), write_view(rnd["id"])
        )
    except ChannelGone:
        log.warning("headlines game %s: game channel is gone, ending the game", game["id"])
        await finish(bot, game, note=CHANNEL_NOTE)
        return True
    if message is None:
        if await abandon_unposted(bot, game, rnd, "headline step"):
            await edit_message(
                bot, game, rnd["answer_message_id"], answer_closed_text(game, rnd, "skipped")
            )
        return True
    await repo.set_write_message(rnd["id"], message.id)
    await close_if_round_closed(
        bot, game, rnd["id"], message.id, write_closed_text(game, rnd, "ended")
    )
    await edit_message(bot, game, rnd["answer_message_id"], closed_text)
    await close_step_if_done(bot, rnd["id"])
    return True


async def close_writing_step(bot, game, rnd, cause: str) -> bool:
    """Close headline writing and read the headlines out. False when it had already closed."""
    repo = context(bot).repo
    count = await repo.close_writing(rnd["id"], RNG)
    if count is None:
        return False
    log.info(
        "headlines game %s: headlines closed for round %s (%s): %d to read",
        game["id"],
        rnd["id"],
        cause,
        count,
    )
    rnd = await repo.get_round(rnd["id"])
    if count == 0:
        log.info("headlines game %s: round %s closed (no_headlines)", game["id"], rnd["id"])
        await edit_message(
            bot, game, rnd["write_message_id"], write_closed_text(game, rnd, "done", 0)
        )
        await post(bot, game, empty_round_line(game, rnd, "no_headlines"))
        await advance(bot, game)
        return True
    await reveal_round(bot, game, rnd)
    return True


async def reveal_post(bot, game, text: str):
    """Post one reveal message with no link previews. None when it failed or took too long."""
    try:
        return await asyncio.wait_for(
            post_with_view(bot, game, text, None, suppress_embeds=True), REVEAL_POST_TIMEOUT
        )
    except TimeoutError:
        log.warning("headlines game %s: a reveal post timed out", game["id"])
        return None


async def reveal_edit(bot, game, message_id: int | None, text: str) -> None:
    """Close a message during a reveal, giving up when Discord takes too long."""
    try:
        await asyncio.wait_for(edit_message(bot, game, message_id, text), REVEAL_POST_TIMEOUT)
    except TimeoutError:
        log.warning("headlines game %s: closing a message during a reveal timed out", game["id"])


async def pairing_text_for(bot, game, row, total: int) -> str:
    """One pairing's message. A name lookup that takes too long falls back to a plain mention."""
    user_id = row["quoted_user_id"]
    try:
        name = await asyncio.wait_for(
            display_name(bot, game["guild_id"], user_id), REVEAL_POST_TIMEOUT
        )
    except TimeoutError:
        log.warning("headlines game %s: a name lookup during a reveal timed out", game["id"])
        name = f"<@{user_id}>"
    return pairing_text(row["position"], total, row["headline"], row["answer"], name)


async def reveal_round(bot, game, rnd) -> None:
    """Read out every headline not shown yet, one message at a time, then open the vote.

    It runs in whichever call closed headline writing, and again from the sweep after a restart
    or a failure. A pairing is marked shown only once its message is up, and every pause and
    post is checked against the stored round first, so an ``end`` stops it and a second run
    posts only what is left. An error stops it without opening the vote.

    Each check also stamps the round. Between two stamps lie at most one name lookup, one post
    and one pause, each bounded, which keeps a live reveal inside the stall grace period however
    slowly Discord answers.
    """
    repo = context(bot).repo
    try:
        options = await repo.vote_options(rnd["id"])
        queue = await repo.reveal_queue(rnd["id"])
        log.info(
            "headlines game %s: reveal of round %s, %d of %d pairings left",
            game["id"],
            rnd["id"],
            len(queue),
            len(options),
        )
        if len(queue) == len(options):
            await reveal_post(bot, game, reveal_intro(game, rnd, len(options)))
            if not await repo.touch_reveal(rnd["id"]):
                log.info("headlines game %s: reveal of round %s stopped", game["id"], rnd["id"])
                return
            await reveal_edit(
                bot,
                game,
                rnd["write_message_id"],
                write_closed_text(game, rnd, "done", len(options)),
            )
        pause = reveal_pause(len(options))
        for row in queue:
            await asyncio.sleep(pause)
            if not await repo.touch_reveal(rnd["id"]):
                log.info("headlines game %s: reveal of round %s stopped", game["id"], rnd["id"])
                return
            text = await pairing_text_for(bot, game, row, len(options))
            message = await reveal_post(bot, game, text)
            if message is not None:
                await repo.mark_shown(row["id"])
    except ChannelGone:
        log.warning("headlines game %s: game channel is gone, ending the game", game["id"])
        await finish(bot, game, note=CHANNEL_NOTE)
        return
    except Exception:
        log.exception("headlines game %s: reveal of round %s failed", game["id"], rnd["id"])
        return
    if len(options) == 1:
        await close_one_headline(bot, game, rnd)
    else:
        await open_voting(bot, game, rnd)


async def close_one_headline(bot, game, rnd) -> None:
    """Close a round that has a single headline: it was shown, and there is nothing to vote on."""
    if not await context(bot).repo.close_unvoted(rnd["id"]):
        return
    log.info("headlines game %s: round %s closed (one_headline)", game["id"], rnd["id"])
    await post(bot, game, empty_round_line(game, rnd, "one_headline"))
    await advance(bot, game)


async def open_voting(bot, game, rnd) -> bool:
    """Open the vote and post its menu. False when it did not open or the menu did not go up."""
    from ..vote import vote_view

    repo = context(bot).repo
    if not await repo.open_vote(rnd["id"], deadline_in(vote_seconds(bot))):
        return False
    rnd = await repo.get_round(rnd["id"])
    voted, eligible = await repo.vote_progress(rnd["id"])
    log.info(
        "headlines game %s: vote opened for round %s, %d may vote",
        game["id"],
        rnd["id"],
        eligible,
    )
    view = vote_view(rnd["id"], await repo.vote_options(rnd["id"]))
    try:
        message = await post_with_view(bot, game, vote_text(game, rnd, voted, eligible), view)
    except ChannelGone:
        log.warning("headlines game %s: game channel is gone, ending the game", game["id"])
        await finish(bot, game, note=CHANNEL_NOTE)
        return False
    if message is None:
        await abandon_unposted(bot, game, rnd, "vote")
        return False
    await repo.set_vote_message(rnd["id"], message.id)
    if not await close_if_round_closed(
        bot, game, rnd["id"], message.id, vote_closed_text(game, rnd)
    ):
        await close_step_if_done(bot, rnd["id"])
    return True


async def close_voting(bot, game, rnd, cause: str, then_advance: bool = True) -> bool:
    """Close the vote and post the result, then the next step unless told not to.

    The result goes up before the vote message changes. False when the game is not running or
    the vote was already closed.
    """
    repo = context(bot).repo
    game = await repo.get_game(game["id"])
    if game is None or game["status"] != "active":
        return False
    if not await repo.close_vote(rnd["id"]):
        return False
    log.info("headlines game %s: vote closed for round %s (%s)", game["id"], rnd["id"], cause)
    try:
        rnd = await repo.get_round(rnd["id"])
        rows = await repo.scored_pairings(game["id"], rnd["id"])
        totals = None
        more = then_advance and await repo.rounds_played(game["id"]) < game["max_rounds"]
        if more and not await idle_note(bot, game):
            totals = game_points(
                await repo.scored_pairings(game["id"]), await repo.game_user_ids(game["id"])
            )
        users = {uid for row in rows for uid in (row.writer_id, row.quoted_id)}
        users.update(uid for uid, _ in totals or [])
        names = await names_by_user(bot, game, sorted(users))
        for text in result_messages(game, rnd, rows, names, totals):
            await post(bot, game, text, suppress_embeds=True)
        await edit_message(bot, game, rnd["vote_message_id"], vote_closed_text(game, rnd))
    except Exception:
        log.exception("headlines game %s: result of round %s failed", game["id"], rnd["id"])
    if then_advance:
        await advance(bot, game)
    return True


async def finish(bot, game, note: str = "") -> bool:
    """End the game and post the recap. False, posting no recap, when it had already ended.

    An open vote is closed first and its result posted, so the votes cast so far score. A round
    still being answered, written or read out is abandoned and scores nothing.
    """
    repo = context(bot).repo
    rnd = await repo.get_current_round(game["id"])
    step = step_of(rnd)
    if step == "voting":
        await close_voting(bot, game, rnd, "game ended", then_advance=False)
    if not await repo.finish_game(game["id"]):
        return False
    log.info("headlines game %s: finished (%s)", game["id"], note or "all rounds played")
    if step in ("answering", "writing", "reveal"):
        log.info("headlines game %s: round %s closed (abandoned)", game["id"], rnd["id"])
        if rnd["write_message_id"]:
            await edit_message(
                bot, game, rnd["write_message_id"], write_closed_text(game, rnd, "ended")
            )
        else:
            await edit_message(
                bot, game, rnd["answer_message_id"], answer_closed_text(game, rnd, "ended")
            )
    pairings = await repo.scored_pairings(game["id"])
    points = game_points(pairings, await repo.game_user_ids(game["id"]))
    names = await names_by_user(bot, game, [uid for uid, _ in points])
    text = recap_text(
        game,
        points,
        names,
        await repo.rounds_played(game["id"]),
        top_headline(pairings),
        note,
        rnd["number"] if step == "reveal" else None,
    )
    await post(bot, game, text, suppress_embeds=True)
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
        rows = standings(await repo.finished_pairings(guild_id), await repo.games_played(guild_id))
        if not rows:
            return
        shown = leaderboard_rows(rows, 0)
        names = await names_by_user(bot, game, [s.user_id for _, s in shown])
        await post(bot, game, leaderboard_text(shown, names))
    except Exception:
        log.exception("headlines game %s: standings after the game failed", game["id"])


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
            log.info("headlines game %s: lobby card is gone, not updating it", game_id)
            return
        embed = lobby_embed(
            game,
            names,
            host_name,
            required_players(bot),
            answer_seconds(bot),
            write_seconds(bot),
            vote_seconds(bot),
        )
        try:
            await message.edit(embed=embed, view=lobby_view(game), allowed_mentions=NO_MENTIONS)
        except NETWORK_ERRORS as e:
            log.info("headlines game %s: could not update the lobby card: %s", game_id, e)


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
    log.info("headlines game %s: started with %d players", game["id"], count)
    await start_round(bot, game)
    await refresh_lobby(bot, game["id"])
    return f"{title(game)} has started with {count} players and {plural(rounds, 'round')}."


async def join_game(bot, game, user_id: int) -> str:
    repo = context(bot).repo
    if game["status"] == "finished":
        raise GameError(GAME_OVER)
    try:
        await repo.join(game["id"], user_id)
    except AlreadyJoined:
        raise GameError("You're already in.") from None
    game = await repo.get_game(game["id"])
    count = len(await repo.list_players(game["id"], active_only=True))
    name = await display_name(bot, game["guild_id"], user_id)
    rnd = await repo.get_current_round(game["id"]) if game["status"] == "active" else None
    open_round = step_of(rnd) is not None
    ctx = await repo.round_context(rnd["id"], user_id) if open_round else None
    on_roster = ctx is not None and ctx["answer_id"] is not None
    mid_round = open_round and not on_roster
    await post(bot, game, join_line(name, game, count, required_players(bot), mid_round))
    await refresh_lobby(bot, game["id"])
    if mid_round:
        return NEXT_ROUND_REPLY
    if on_roster:
        await refresh_open_step(bot, game, rnd["id"])
        return BACK_IN
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
    return "You've left. Your answers, headlines and votes so far still count."


async def after_leave(bot, game, player) -> None:
    """A player left: pass the host role on, then end the game or keep the open step moving.

    A reveal is left alone. When the game ends here, the reveal stops by itself at its next
    check of the stored round.
    """
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
            log.info("headlines game %s: host passed to %s", game["id"], heir["user_id"])
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
    if step_of(rnd) in (None, "reveal"):
        return
    if not await close_step_if_done(bot, rnd["id"], cause="a player left"):
        await refresh_open_step(bot, game, rnd["id"])


async def status_reply(bot, game, user_id: int) -> str:
    """The private status for one caller: the game, its players, the step, and what they owe.

    It never shows a question, an answer, a quote, a headline or a vote.
    """
    repo = context(bot).repo
    players = await repo.list_players(game["id"], active_only=True)
    names = await names_by_user(bot, game, [p["user_id"] for p in players])
    host = safe(await display_name(bot, game["guild_id"], game["host_id"]))
    roster = ", ".join(safe(names[p["user_id"]]) for p in players) or "nobody"
    lines = [f"{title(game)}, hosted by **{host}**.", f"**Players ({len(players)}):** {roster}"]
    if game["status"] == "open":
        lines.append(f"Sign-ups are open. It needs at least {required_players(bot)} to start.")
        return "\n".join(lines)
    rnd = await repo.get_current_round(game["id"])
    step = step_of(rnd)
    progress, deadline = (0, 0), None
    if step == "answering":
        progress, deadline = await repo.answer_progress(rnd["id"]), rnd["answer_deadline_at"]
    elif step == "writing":
        progress, deadline = await repo.write_progress(rnd["id"]), rnd["write_deadline_at"]
    elif step == "voting":
        progress, deadline = await repo.vote_progress(rnd["id"]), rnd["vote_deadline_at"]
    owed = owed_line(await repo.round_context(rnd["id"], user_id)) if step else None
    return "\n".join(lines + status_lines(game, rnd, step, progress, deadline, owed))


async def close_overdue(bot) -> int:
    """Close every answer, headline and vote step whose deadline has passed. Returns how many.

    A step whose message never went up (a crash between the database write and the post) is
    abandoned, so nobody is blamed for it and it is never read as idle.
    """
    repo = context(bot).repo
    steps = (
        (repo.list_overdue_answering, "answer_message_id", close_answering),
        (repo.list_overdue_writing, "write_message_id", close_writing_step),
        (repo.list_overdue_voting, "vote_message_id", close_voting),
    )
    closed = 0
    for list_overdue, message_column, close in steps:
        for rnd in await list_overdue(now_iso()):
            game = await repo.get_game(rnd["game_id"])
            if game is None:
                continue
            if rnd[message_column] is None:
                if await repo.abandon_round(rnd["id"]):
                    log.info(
                        "headlines game %s: round %s closed (abandoned)", game["id"], rnd["id"]
                    )
                    await advance(bot, game)
                    closed += 1
            elif await close(bot, game, rnd, "deadline"):
                closed += 1
    return closed


async def resume_stalled(bot, grace: int = STALL_GRACE_SECONDS) -> int:
    """Move on every running game that has had nothing to wait for during ``grace`` seconds."""
    repo = context(bot).repo
    before = (now() - timedelta(seconds=grace)).isoformat(timespec="seconds")
    resumed = 0
    for game in await repo.list_stalled_games(before):
        log.info("headlines game %s: nothing open, resuming", game["id"])
        await advance(bot, game)
        resumed += 1
    return resumed


async def leaderboard_reply(bot, guild_id: int, caller_id: int, member_id: int | None = None):
    """The all-time standings for a server, or one member's numbers."""
    repo = context(bot).repo
    rows = standings(await repo.finished_pairings(guild_id), await repo.games_played(guild_id))
    if member_id is not None:
        name = await display_name(bot, guild_id, member_id)
        return member_text(name, next((s for s in rows if s.user_id == member_id), None))
    shown = leaderboard_rows(rows, caller_id)
    names = {s.user_id: await display_name(bot, guild_id, s.user_id) for _, s in shown}
    return leaderboard_text(shown, names)

"""Fifty-Fifty game flow, driven through a fake bot and channel."""

import asyncio
from itertools import cycle
from types import SimpleNamespace

import aiohttp
import discord
import pytest
from fiftyfifty_fakes import (
    CHANNEL_ID,
    DECK,
    FakeChannel,
    add_card,
    card_names,
    interaction_for,
    make_bot,
    reply_of,
)

from gaminator.core.util import GameError, NotGameAdmin
from gaminator.games.fiftyfifty.commands.group import require_host_or_admin
from gaminator.games.fiftyfifty.services import game as flow
from gaminator.games.fiftyfifty.services.rules import (
    CLOSED,
    NOT_IN_ROUND,
    VOTE_FIRST,
    WRITING_CLOSED,
)
from gaminator.games.fiftyfifty.vote import GuessSelect, guess_view, handle_guess, handle_vote

FOUR = (11, 12, 13, 14)
HINT = "All-time standings: `/gaminator fiftyfifty leaderboard`"
PAST = "2000-01-01T00:00:00+00:00"
DRUM = "🥁 The votes are in..."


@pytest.fixture
async def bot(db):
    return make_bot(db)


async def lobby(bot, users=(11, 12, 13)):
    game = await bot.repo.create_game(1, "Friday", users[0], CHANNEL_ID)
    for uid in users:
        await bot.repo.join(game["id"], uid)
    return game


async def running(bot, users=(11, 12, 13), rounds=1):
    game = await lobby(bot, users)
    await flow.begin_game(bot, game, rounds)
    return await bot.repo.get_game(game["id"])


async def write(bot, game, uid, text=None):
    rnd = await bot.repo.get_current_round(game["id"])
    return await flow.submit_question(bot, rnd["id"], uid, text or f"soup {uid}", f"salad {uid}")


async def voting(bot, users=(11, 12, 13), rounds=1):
    """A running game whose first round has every question written, so question 1 is open."""
    game = await running(bot, users, rounds)
    for uid in users:
        await write(bot, game, uid)
    return game


async def question(bot, game):
    rnd = await bot.repo.get_current_round(game["id"])
    return await bot.repo.current_question(rnd["id"])


async def writer_of(bot, q):
    return (await bot.repo.get_player_by_id(q["writer_id"]))["user_id"]


async def vote(bot, game, uid, choice, guess=None):
    """Cast a vote straight through the repo, with a guess at the A votes when one is given."""
    q = await question(bot, game)
    voter = await bot.repo.get_player(game["id"], uid)
    assert await bot.repo.cast_vote(q["id"], voter["id"], choice)
    if guess is not None:
        assert await bot.repo.set_guess(q["id"], voter["id"], guess)


async def vote_all(bot, game, users, choices="ab", guesses=None):
    """Every user but the writer votes on the open question, cycling ``choices``. Returns the writer.

    ``guesses`` maps a user id to that voter's guess at the A votes.
    """
    writer = await writer_of(bot, await question(bot, game))
    for uid, choice in zip([u for u in users if u != writer], cycle(choices), strict=False):
        await vote(bot, game, uid, choice, (guesses or {}).get(uid))
    return writer


async def close_current(bot, game):
    return await flow.close_question(bot, game, await question(bot, game))


async def play_question(bot, game, users, choices="ab", guesses=None):
    writer = await vote_all(bot, game, users, choices, guesses)
    assert await close_current(bot, game)
    return writer


def reveals(bot):
    return [t for t in bot.channel.texts if t.startswith("📊")]


async def status_of(bot, game):
    return (await bot.repo.get_game(game["id"]))["status"]


async def expire_writing(bot, game):
    rnd = await bot.repo.get_current_round(game["id"])
    async with bot.repo.db.transaction() as conn:
        await conn.execute(
            "UPDATE fiftyfifty_rounds SET write_deadline_at = ? WHERE id = ?", (PAST, rnd["id"])
        )
    return rnd


async def expire_question(bot, game):
    q = await question(bot, game)
    async with bot.repo.db.transaction() as conn:
        await conn.execute(
            "UPDATE fiftyfifty_questions SET deadline_at = ? WHERE id = ?", (PAST, q["id"])
        )
    return q


def labels(message):
    return [c.item.label for c in message.view.children]


def forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "missing access")


def blips(count):
    return [aiohttp.ClientError("blip") for _ in range(count)]


async def test_begin_game_posts_round(bot):
    game = await lobby(bot)
    reply = await flow.begin_game(bot, game, 1)
    assert reply == "Fifty-Fifty game **Friday** has started with 3 players and 1 round."
    game = await bot.repo.get_game(game["id"])
    assert game["status"] == "active" and game["max_rounds"] == 1
    (message,) = bot.channel.messages
    assert message.content.startswith("_ _\n✍️ **Round 1 of 1**: press **Write**")
    assert "0/3 written." in message.content
    rnd = await bot.repo.get_current_round(game["id"])
    assert labels(message) == ["Write", "Answer by DM"]
    assert message.view.children[0].item.custom_id == f"fiftyfifty:write:{rnd['id']}"
    assert rnd["write_message_id"] == message.id and rnd["write_channel_id"] == CHANNEL_ID
    topics = set()
    for uid in (11, 12, 13):
        ctx = await bot.repo.write_context(rnd["id"], uid)
        assert (ctx["option_a"], ctx["option_b"], ctx["answered_at"]) == ("", "", None)
        topics.add(ctx["topic"])
    assert len(topics) == 3 and topics <= set(DECK)


async def test_begin_game_refusals(bot):
    game = await lobby(bot, users=(11, 12))
    with pytest.raises(GameError, match="Need at least 3 players to start; 2 joined"):
        await flow.begin_game(bot, game, 1)
    await bot.repo.join(game["id"], 13)
    await flow.begin_game(bot, game, 1)
    with pytest.raises(GameError, match="already started"):
        await flow.begin_game(bot, await bot.repo.get_game(game["id"]), 1)


async def test_double_start_one_round(bot):
    game = await lobby(bot)
    results = await asyncio.gather(
        flow.begin_game(bot, game, 2), flow.begin_game(bot, game, 2), return_exceptions=True
    )
    assert sorted(isinstance(r, GameError) for r in results) == [False, True]
    assert len(bot.channel.messages) == 1
    assert (await bot.repo.get_current_round(game["id"]))["number"] == 1
    assert not await flow.start_round(bot, await bot.repo.get_game(game["id"]))


async def test_everyone_wrote_posts_first_question(bot):
    game = await running(bot)
    rnd = await bot.repo.get_current_round(game["id"])
    reply = await write(bot, game, 11, "cold   soup")
    assert reply.split("\n") == [
        "Saved. Your question now reads:",
        "Would you rather cold soup or salad 11?",
        "You can change it until writing closes.",
    ]
    await write(bot, game, 12)
    round_message = bot.channel.messages[0]
    assert "2/3 written." in round_message.content and round_message.view is not None
    assert not await flow.everyone_wrote(bot, rnd)
    assert len(bot.channel.messages) == 1
    await write(bot, game, 13)
    assert round_message.content.endswith("Writing is closed: 3 questions to vote on.")
    assert round_message.view is None
    first = bot.channel.messages[1]
    assert first.content.startswith(
        "_ _\n🗳️ **Question 1 of 3** (round 1)\n## Would you rather\n> 🅰️ "
    )
    assert "0/2 voted, 0/2 guessed." in first.content
    assert labels(first) == ["A", "B"]
    q = await question(bot, game)
    assert q["position"] == 1 and q["vote_message_id"] == first.id
    assert [c.item.custom_id for c in first.view.children] == [
        f"fiftyfifty:vote:{q['id']}:a",
        f"fiftyfifty:vote:{q['id']}:b",
    ]
    assert f"> 🅰️ {q['option_a']}\n> 🅱️ {q['option_b']}\n" in first.content
    assert q["option_a"] in ("cold soup", "soup 12", "soup 13") and q["answer"] is None
    assert "U11" not in first.content and "U12" not in first.content
    assert len(bot.channel.messages) == 2


async def test_write_edit_does_not_change_count(bot):
    game = await running(bot)
    await write(bot, game, 11, "first")
    round_message = bot.channel.messages[0]
    edits = len(round_message.edits)
    assert "Would you rather second or" in await write(bot, game, 11, "second")
    assert len(round_message.edits) == edits and "1/3 written." in round_message.content
    rnd = await bot.repo.get_current_round(game["id"])
    assert (await bot.repo.write_context(rnd["id"], 11))["option_a"] == "second"


async def test_write_deadline_closes_with_partial_answers(db):
    bot = make_bot(db, write_seconds=0)
    game = await running(bot)
    await write(bot, game, 11)
    await write(bot, game, 12)
    assert await flow.close_overdue(bot) == 1
    assert bot.channel.messages[0].content.endswith("Writing is closed: 2 questions to vote on.")
    assert bot.channel.messages[0].view is None
    assert bot.channel.messages[1].content.startswith("_ _\n🗳️ **Question 1 of 2** (round 1)")
    assert await write(bot, game, 13) == WRITING_CLOSED
    assert await flow.close_overdue(bot) == 0


async def test_unwritten_question_skipped(bot):
    game = await running(bot, rounds=2)
    rnd = await bot.repo.get_current_round(game["id"])
    await write(bot, game, 11)
    await write(bot, game, 12)
    await expire_writing(bot, game)
    assert await flow.close_overdue(bot) == 1
    q = await question(bot, game)
    assert await bot.repo.vote_progress(q["id"]) == (0, 2)
    writers = {await play_question(bot, game, (11, 12, 13)) for _ in range(2)}
    assert writers == {11, 12}
    skipped = await bot.repo.write_context(rnd["id"], 13)
    assert (await bot.repo.get_question(skipped["question_id"]))["started_at"] is None
    results = next(t for t in bot.channel.texts if "is done" in t)
    assert "🏁 **Round 1 of 2** is done." in results
    assert "This round: U11 100, U12 100, U13 0 (no question)" in results
    assert "Totals: U11 100, U12 100, U13 0" in results
    assert results.endswith("Round 2 starts now.")
    assert bot.channel.texts[-1].startswith("_ _\n✍️ **Round 2 of 2**")


async def test_everyone_done_reveals_and_moves_on(bot):
    game = await voting(bot)
    q = await question(bot, game)
    writer = await writer_of(bot, q)
    first, second = (u for u in (11, 12, 13) if u != writer)
    await vote(bot, game, first, "a", guess=1)
    assert not await flow.everyone_done(bot, q)
    await vote(bot, game, second, "b")
    assert not await flow.everyone_done(bot, q)
    await vote(bot, game, second, "b", guess=2)
    assert await flow.everyone_done(bot, q)
    assert await flow.close_question(bot, game, q)
    _, asked, drum, reveal, following = bot.channel.messages
    assert asked.content.endswith("Voting is closed.") and asked.view is None
    assert drum.content == DRUM
    assert reveal.content.split("\n") == [
        f"📊 **Question 1**: Would you rather {q['option_a']} or {q['option_b']}?",
        "**A 1, B 1**: a 50/50 split.",
        f"🅰️ **U{first}**",
        f"🅱️ **U{second}**",
        f"✍️ Written by **U{writer}**: **100** points.",
        f"🎯 Guessed the split: **U{first}** +50",
    ]
    assert following.content.startswith("_ _\n🗳️ **Question 2 of 3** (round 1)")
    assert following.view is not None
    assert not await flow.close_question(bot, game, q)
    assert len(bot.channel.messages) == 5


async def test_reveal_names_who_picked_what(bot):
    game = await voting(bot, users=FOUR)
    q = await question(bot, game)
    writer = await writer_of(bot, q)
    one, two, three = (u for u in FOUR if u != writer)
    await vote(bot, game, two, "b", guess=3)
    await vote(bot, game, one, "a", guess=0)
    await vote(bot, game, three, "a", guess=2)
    assert await close_current(bot, game)
    assert bot.channel.texts[2] == DRUM
    lines = bot.channel.texts[3].split("\n")
    assert lines[1] == "**A 2, B 1**: as even as 3 votes can be."
    assert lines[2] == f"🅰️ **U{one}** and **U{three}**"
    assert lines[3] == f"🅱️ **U{two}**"
    assert lines[4] == f"✍️ Written by **U{writer}**: **100** points."
    assert lines[5] == f"🎯 Guessed the split: **U{three}** +50"
    assert len(lines) == 6


async def test_unanimous_reveal_and_nobody_guessed(bot):
    game = await voting(bot)
    writer = await play_question(bot, game, (11, 12, 13), "aa", {11: 1, 12: 1, 13: 1})
    reveal = bot.channel.texts[3]
    assert f"U{writer}" in reveal
    assert "**A 2, B 0**: a 100/0 split." in reveal and "**0** points." in reveal
    assert "\n🅱️ nobody\n" in reveal
    assert reveal.endswith("🎯 Nobody guessed the split.")


async def test_reveal_waits_for_the_pause(bot, monkeypatch):
    game = await voting(bot)
    q = await question(bot, game)
    await vote_all(bot, game, (11, 12, 13), "ab")
    seen = []

    async def pause(seconds):
        closed = (await bot.repo.get_question(q["id"]))["closed_at"]
        seen.append((seconds, bool(closed), bot.channel.texts[-1], len(reveals(bot))))

    monkeypatch.setattr(flow, "REVEAL_PAUSE", 3)
    monkeypatch.setattr(flow.asyncio, "sleep", pause)
    assert await flow.close_question(bot, game, q)
    assert seen == [(3, True, DRUM, 0)]
    assert len(reveals(bot)) == 1
    assert bot.channel.texts.index(DRUM) + 1 == bot.channel.texts.index(reveals(bot)[0])


async def test_crash_during_the_pause_is_resumed(bot, monkeypatch):
    game = await voting(bot)
    q = await question(bot, game)
    await vote_all(bot, game, (11, 12, 13), "ab")

    async def crash(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(flow.asyncio, "sleep", crash)
    with pytest.raises(asyncio.CancelledError):
        await flow.close_question(bot, game, q)
    monkeypatch.undo()
    assert (await bot.repo.get_question(q["id"]))["closed_at"]
    assert bot.channel.texts[-1] == DRUM and await question(bot, game) is None
    assert await flow.resume_stalled(bot) == 0
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert "**Question 2 of 3**" in bot.channel.texts[-1]
    assert len(await bot.repo.closed_questions(game["id"])) == 1


async def test_last_question_posts_recap(bot):
    game = await voting(bot)
    top = await play_question(bot, game, (11, 12, 13), "ab")
    zero = await play_question(bot, game, (11, 12, 13), "aa")
    assert await status_of(bot, game) == "active"
    also = await play_question(bot, game, (11, 12, 13), "ba")
    assert await status_of(bot, game) == "finished"
    assert len(bot.channel.messages) == 12
    recap = bot.channel.recap.split("\n")
    low, high = sorted((top, also))
    assert recap[0] == "🏁 Fifty-Fifty game **Friday** is over after 1 round."
    assert recap[1] == f"🏆 **U{low}** and **U{high}** tie with 100 points each!"
    assert recap[2] == f"Also: U{zero} 0"
    assert recap[3].startswith("⚖️ Best split: Would you rather ")
    assert recap[3].endswith(f"(1 to 1, by U{top})")
    assert recap[4] == HINT and len(recap) == 5
    assert not any("is done" in t for t in bot.channel.texts)
    assert (await bot.repo.get_current_round(game["id"]))["closed_at"]


async def test_multi_round_game(bot):
    game = await voting(bot, rounds=2)
    for _ in range(3):
        await play_question(bot, game, (11, 12, 13), "ab")
    assert await status_of(bot, game) == "active"
    results, second_round = bot.channel.messages[-2:]
    assert results.content.split("\n") == [
        "🏁 **Round 1 of 2** is done.",
        "This round: U11 100, U12 100, U13 100",
        "Totals: U11 100, U12 100, U13 100",
        "Round 2 starts now.",
    ]
    assert second_round.content.startswith("_ _\n✍️ **Round 2 of 2**")
    assert "0/3 written." in second_round.content and labels(second_round) == [
        "Write",
        "Answer by DM",
    ]
    assert len(await bot.repo.used_topics(game["id"])) == 6
    for uid in (11, 12, 13):
        await write(bot, game, uid)
    assert "**Question 1 of 3** (round 2)" in bot.channel.texts[-1]
    winner = await play_question(bot, game, (11, 12, 13), "ab")
    for _ in range(2):
        await play_question(bot, game, (11, 12, 13), "aa")
    assert await status_of(bot, game) == "finished"
    recap = bot.channel.recap.split("\n")
    assert recap[0] == "🏁 Fifty-Fifty game **Friday** is over after 2 rounds."
    assert recap[1] == f"🏆 **U{winner}** wins with 200 points!"
    assert recap[2] == "Also: " + ", ".join(f"U{u} 100" for u in (11, 12, 13) if u != winner)
    assert sum("is done" in t for t in bot.channel.texts) == 1


async def test_question_with_no_voters_closes_at_once(bot):
    game = await voting(bot, rounds=2)
    for uid in (14, 15, 16):
        await flow.join_game(bot, game, uid)
    for uid in (11, 12, 13):
        await flow.leave_game(bot, game, uid)
    game = await bot.repo.get_game(game["id"])
    assert game["status"] == "active" and game["host_id"] == 14
    reveals = [t for t in bot.channel.texts if t.startswith("📊")]
    assert len(reveals) == 3 and all("Nobody voted, so it scores 0." in t for t in reveals)
    asked = [m for m in bot.channel.messages if m.content.startswith("_ _\n🗳️")]
    assert len(asked) == 3 and all(m.view is None for m in asked)
    assert await bot.repo.asked_vote_totals(game["id"]) == []
    assert not any("nobody voted on the last 2 questions" in t for t in bot.channel.texts)
    assert bot.channel.texts[-1].startswith("_ _\n✍️ **Round 2 of 2**")
    assert "0/3 written." in bot.channel.texts[-1]


async def test_nobody_wrote_ends_game(bot):
    game = await running(bot, rounds=3)
    await expire_writing(bot, game)
    assert await flow.close_overdue(bot) == 1
    assert await status_of(bot, game) == "finished"
    round_message = bot.channel.messages[0]
    assert round_message.content.endswith("Writing is closed: nobody wrote a question.")
    assert round_message.view is None
    recap = bot.channel.texts[-1]
    assert "is over after 1 round (nobody wrote a question this round)." in recap
    assert recap.endswith(f"Nobody scored.\n{HINT}")
    assert len(bot.channel.messages) == 2


async def test_two_unvoted_questions_end_game(bot):
    game = await voting(bot, rounds=3)
    assert await close_current(bot, game)
    assert await status_of(bot, game) == "active"
    assert bot.channel.texts[2] == DRUM
    assert "Nobody voted, so it scores 0." in bot.channel.texts[3]
    assert "**Question 2 of 3**" in bot.channel.texts[4]
    assert await close_current(bot, game)
    assert await status_of(bot, game) == "finished"
    assert "(nobody voted on the last 2 questions)." in bot.channel.recap
    assert not any("**Question 3 of 3**" in t for t in bot.channel.texts)
    assert sum("is over" in t for t in bot.channel.texts) == 1


async def test_a_vote_resets_the_unvoted_count(bot):
    game = await voting(bot, rounds=3)
    await close_current(bot, game)
    await vote_all(bot, game, (11, 12, 13), "a")
    await close_current(bot, game)
    await close_current(bot, game)
    assert await status_of(bot, game) == "active"
    assert bot.channel.texts[-1].startswith("_ _\n✍️ **Round 2 of 3**")
    for uid in (11, 12, 13):
        await write(bot, game, uid)
    await close_current(bot, game)
    assert await status_of(bot, game) == "finished"
    assert "(nobody voted on the last 2 questions)." in bot.channel.recap


async def test_double_finish_one_recap(bot):
    game = await voting(bot)
    results = await asyncio.gather(flow.finish(bot, game), flow.finish(bot, game))
    assert sorted(results) == [False, True]
    assert sum("is over" in t for t in bot.channel.texts) == 1


async def test_finish_mid_question_leaves_it_out(bot):
    game = await voting(bot)
    scorer = await play_question(bot, game, (11, 12, 13), "ab")
    await vote_all(bot, game, (11, 12, 13), "ab")
    q = await question(bot, game)
    assert await flow.finish(bot, game, note="ended by U11")
    asked = bot.channel.messages[4]
    assert asked.content.endswith("Voting is closed.") and asked.view is None
    assert (await bot.repo.get_question(q["id"]))["abandoned"] == 1
    recap = bot.channel.recap
    assert "is over after 1 round (ended by U11)." in recap
    assert f"🏆 **U{scorer}** wins with 100 points!" in recap
    assert not await flow.close_question(bot, game, q)
    assert not await flow.start_round(bot, game)
    assert await write(bot, game, 11) == "That game is over."


async def test_finish_during_writing(bot):
    game = await running(bot)
    await write(bot, game, 11)
    assert await flow.finish(bot, game, note="ended by U11")
    round_message = bot.channel.messages[0]
    assert round_message.content.endswith("\nWriting is closed.") and round_message.view is None
    assert "is over after 0 rounds (ended by U11)." in bot.channel.texts[-1]
    assert bot.channel.texts[-1].endswith(f"Nobody scored.\n{HINT}")


async def test_finish_announces_elsewhere(bot):
    other = FakeChannel(777)
    channels = {CHANNEL_ID: bot.channel, 777: other}
    bot.get_channel = channels.get
    await bot.guild_settings.set_announce_channel(1, 777)
    game = await voting(bot)
    await flow.finish(bot, game, note="ended by U11")
    assert other.texts == [bot.channel.texts[-1]]
    assert "is over after 1 round (ended by U11)." in other.texts[0]
    await bot.guild_settings.set_announce_channel(2, CHANNEL_ID)
    here = await bot.repo.create_game(2, "Same", 11, CHANNEL_ID)
    for uid in (11, 12, 13):
        await bot.repo.join(here["id"], uid)
    await flow.begin_game(bot, here, 1)
    await flow.finish(bot, here)
    assert sum("**Same** is over" in t for t in bot.channel.texts) == 1
    assert len(other.texts) == 1


async def test_failed_reveal_still_moves_on(bot):
    game = await voting(bot)
    await vote_all(bot, game, (11, 12, 13), "ab")
    bot.channel.fail_with = [RuntimeError("boom")]
    assert await close_current(bot, game)
    assert await status_of(bot, game) == "active"
    assert not any(t.startswith("📊") for t in bot.channel.texts)
    assert "**Question 2 of 3**" in bot.channel.texts[-1]
    assert (await question(bot, game))["position"] == 2


async def test_post_retries_once(bot):
    game = await lobby(bot)
    bot.channel.fail_with = blips(1)
    await flow.begin_game(bot, game, 1)
    assert len(bot.channel.messages) == 1
    assert (await bot.repo.get_current_round(game["id"]))["write_message_id"]
    await write(bot, game, 11)
    await write(bot, game, 12)
    bot.channel.fail_with = blips(1)
    await write(bot, game, 13)
    assert "**Question 1 of 3**" in bot.channel.texts[-1]
    assert (await question(bot, game))["vote_message_id"] == bot.channel.messages[-1].id


async def test_post_fails_twice_abandons_round(bot):
    game = await lobby(bot)
    bot.channel.fail_with = blips(2)
    await flow.begin_game(bot, game, 3)
    rnd = await bot.repo.get_current_round(game["id"])
    assert await status_of(bot, game) == "active" and rnd["abandoned"] == 1
    assert bot.channel.messages == []
    assert await bot.repo.rounds_played(game["id"]) == 0


async def test_post_fails_twice_abandons_question(bot):
    game = await running(bot)
    await write(bot, game, 11)
    await write(bot, game, 12)
    bot.channel.fail_with = blips(2)
    await write(bot, game, 13)
    rnd = await bot.repo.get_current_round(game["id"])
    assert len(bot.channel.messages) == 1
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert len(bot.channel.messages) == 2
    assert "**Question 2 of 3**" in bot.channel.texts[-1]
    current = await question(bot, game)
    assert current["position"] == 2
    scored = await play_question(bot, game, (11, 12, 13), "ab")
    await play_question(bot, game, (11, 12, 13), "aa")
    assert await status_of(bot, game) == "finished"
    lost = [
        await bot.repo.get_question((await bot.repo.write_context(rnd["id"], uid))["question_id"])
        for uid in (11, 12, 13)
    ]
    assert sorted(q["abandoned"] for q in lost) == [0, 0, 1]
    assert len(await bot.repo.closed_questions(game["id"])) == 2
    assert f"🏆 **U{scored}** wins with 100 points!" in bot.channel.recap


async def test_channel_gone_ends_game(bot):
    game = await lobby(bot)
    bot.channel.fail_with = [forbidden()]
    await flow.begin_game(bot, game, 3)
    assert await status_of(bot, game) == "finished"
    assert bot.channel.texts[-1].startswith(
        "🏁 Fifty-Fifty game **Friday** is over after 0 rounds (I couldn't post in the game channel)."
    )


async def test_channel_gone_at_question_ends_game(bot):
    game = await running(bot)
    await write(bot, game, 11)
    await write(bot, game, 12)
    bot.channel.fail_with = [forbidden()]
    await write(bot, game, 13)
    assert await status_of(bot, game) == "finished"
    assert "(I couldn't post in the game channel)." in bot.channel.texts[-1]


async def test_resume_after_abandoned_round_starts_next(bot):
    game = await lobby(bot)
    bot.channel.fail_with = blips(2)
    await flow.begin_game(bot, game, 3)
    assert bot.channel.messages == []
    assert await flow.idle_note(bot, game) is None
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert await status_of(bot, game) == "active"
    (message,) = bot.channel.messages
    assert message.content.startswith("_ _\n✍️ **Round 1 of 3**")
    rnd = await bot.repo.get_current_round(game["id"])
    assert rnd["number"] == 1 and rnd["abandoned"] == 0
    assert await flow.resume_stalled(bot, grace=0) == 0


async def test_three_abandoned_rounds_end_game(bot):
    game = await lobby(bot)
    bot.channel.fail_with = blips(6)
    await flow.begin_game(bot, game, 3)
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert await bot.repo.abandoned_streak(game["id"]) == 2
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert await bot.repo.abandoned_streak(game["id"]) == 3
    assert await status_of(bot, game) == "active" and bot.channel.messages == []
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert await status_of(bot, game) == "finished"
    (recap,) = bot.channel.texts
    assert "is over after 0 rounds (I couldn't post in the game channel)." in recap
    assert await flow.resume_stalled(bot, grace=0) == 0


async def test_join_game_lobby_and_refusals(bot):
    game = await lobby(bot, users=(11,))
    assert await flow.join_game(bot, game, 12) == "You're in."
    assert "🙋 **U12** joined Fifty-Fifty game **Friday**." in bot.channel.texts[-1]
    assert "1 more needed to start" in bot.channel.texts[-1]
    with pytest.raises(GameError, match="You're already in."):
        await flow.join_game(bot, game, 12)
    for uid in range(100, 110):
        await bot.repo.join(game["id"], uid)
    with pytest.raises(GameError, match=r"The game is full \(12 players\)."):
        await flow.join_game(bot, game, 999)
    await bot.repo.finish_game(game["id"])
    with pytest.raises(GameError, match="That game is over."):
        await flow.join_game(bot, await bot.repo.get_game(game["id"]), 999)


async def test_join_mid_round_flow(bot):
    game = await running(bot, rounds=2)
    assert await flow.join_game(bot, game, 14) == "You're in from the next round."
    assert bot.channel.texts[-1] == "🙋 **U14** joined and is in from the next round."
    round_message = bot.channel.messages[0]
    assert "0/3 written." in round_message.content and round_message.edits == []
    assert await write(bot, game, 14) == NOT_IN_ROUND
    for uid in (11, 12, 13):
        await write(bot, game, uid)
    q = await question(bot, game)
    assert await bot.repo.vote_progress(q["id"]) == (0, 2)
    for _ in range(3):
        await play_question(bot, game, (11, 12, 13), "ab")
    assert "U14" not in bot.channel.texts[-2] and "is done" in bot.channel.texts[-2]
    assert bot.channel.texts[-1].startswith("_ _\n✍️ **Round 2 of 2**")
    assert "0/4 written." in bot.channel.texts[-1]
    assert await flow.join_game(bot, game, 15) == "You're in from the next round."


async def test_rejoin_back_in(bot):
    game = await running(bot, users=FOUR)
    await write(bot, game, 13)
    round_message = bot.channel.messages[0]
    assert "1/4 written." in round_message.content
    await flow.leave_game(bot, game, 13)
    assert "0/3 written." in round_message.content
    assert await flow.join_game(bot, game, 13) == "You're back in."
    assert bot.channel.texts[-1] == "🙋 **U13** joined."
    assert "1/4 written." in round_message.content
    for uid in (11, 12, 14):
        await write(bot, game, uid)
    writer = await writer_of(bot, await question(bot, game))
    voter = next(u for u in FOUR if u != writer)
    await vote(bot, game, voter, "a")
    asked = bot.channel.messages[-1]
    await flow.leave_game(bot, game, voter)
    assert "0/2 voted, 0/2 guessed." in asked.content
    assert await flow.join_game(bot, game, voter) == "You're back in."
    assert "1/3 voted, 0/3 guessed." in asked.content


async def test_leave_game_refusals(bot):
    game = await lobby(bot)
    with pytest.raises(GameError, match="You're not in this game."):
        await flow.leave_game(bot, game, 99)
    assert await flow.leave_game(bot, game, 13) == "You've left the game."
    with pytest.raises(GameError, match="You're not in this game."):
        await flow.leave_game(bot, game, 13)


async def test_leave_during_writing_closes_writing(bot):
    game = await running(bot, users=FOUR)
    for uid in (11, 12, 13):
        await write(bot, game, uid)
    assert len(bot.channel.messages) == 1
    reply = await flow.leave_game(bot, game, 14)
    assert reply == "You've left. Your question, votes and guesses so far still count."
    texts = bot.channel.texts
    assert texts[1] == "🚪 **U14** left. 3 players remain."
    assert texts[0].endswith("Writing is closed: 3 questions to vote on.")
    assert texts[2].startswith("_ _\n🗳️ **Question 1 of 3** (round 1)")
    assert "0/2 voted, 0/2 guessed." in texts[2]


async def test_leave_during_writing_updates_count(bot):
    game = await running(bot, users=FOUR)
    await write(bot, game, 11)
    await flow.leave_game(bot, game, 14)
    assert "1/3 written." in bot.channel.messages[0].content
    assert bot.channel.messages[0].view is not None


async def test_leave_during_vote_closes_question(bot):
    game = await voting(bot, users=FOUR)
    q = await question(bot, game)
    writer = await writer_of(bot, q)
    one, two, three = (u for u in FOUR if u != writer)
    await vote(bot, game, one, "a", guess=1)
    await flow.leave_game(bot, game, three)
    asked = bot.channel.messages[1]
    assert "1/2 voted, 1/2 guessed." in asked.content and asked.view is not None
    assert await flow.join_game(bot, game, three) == "You're back in."
    await vote(bot, game, two, "b", guess=1)
    await flow.leave_game(bot, game, three)
    assert asked.content.endswith("Voting is closed.")
    assert (await bot.repo.get_question(q["id"]))["closed_at"]
    assert any("**A 1, B 1**: a 50/50 split." in t for t in bot.channel.texts)
    assert "**Question 2 of 4**" in bot.channel.texts[-1]


async def test_leaver_who_voted_without_a_guess_stops_blocking(bot):
    game = await voting(bot, users=FOUR)
    q = await question(bot, game)
    writer = await writer_of(bot, q)
    one, two, three = (u for u in FOUR if u != writer)
    await vote(bot, game, one, "a", guess=2)
    await vote(bot, game, two, "b", guess=0)
    await vote(bot, game, three, "a")
    assert not await flow.everyone_done(bot, q)
    await flow.leave_game(bot, game, three)
    assert (await bot.repo.get_question(q["id"]))["closed_at"]
    (reveal,) = reveals(bot)
    assert "**A 2, B 1**" in reveal and f"🅰️ **U{one}** and **U{three}**" in reveal
    assert reveal.endswith(f"🎯 Guessed the split: **U{one}** +50")


async def test_leavers_guess_still_counts(bot):
    game = await voting(bot, users=FOUR)
    q = await question(bot, game)
    writer = await writer_of(bot, q)
    one, two, three = (u for u in FOUR if u != writer)
    await vote(bot, game, three, "b", guess=1)
    await flow.leave_game(bot, game, three)
    assert await bot.repo.most_votes(q["id"]) == 3
    await vote(bot, game, one, "a", guess=0)
    await vote(bot, game, two, "b", guess=3)
    assert await flow.everyone_done(bot, q)
    assert await close_current(bot, game)
    assert reveals(bot)[0].endswith(f"🎯 Guessed the split: **U{three}** +50")
    totals = dict(game_totals(await bot.repo.closed_questions(game["id"])))
    assert totals[three] == 50 and totals[writer] == 100


async def test_leavers_question_still_scores(bot):
    game = await running(bot, users=FOUR)
    for uid in (11, 12, 13):
        await write(bot, game, uid)
    await write(bot, game, 14, "left behind")
    await flow.leave_game(bot, game, 14)
    scores = {}
    for _ in range(4):
        q = await question(bot, game)
        assert await bot.repo.vote_progress(q["id"]) == (
            0,
            2 if await writer_of(bot, q) != 14 else 3,
        )
        writer = await play_question(bot, game, (11, 12, 13), "ab")
        scores[writer] = (await bot.repo.closed_questions(game["id"]))[-1].points
    assert await status_of(bot, game) == "finished"
    assert scores == {11: 100, 12: 100, 13: 100, 14: 100}
    recap = bot.channel.recap
    assert "🏆 **U11**, **U12**, **U13** and **U14** tie with 100 points each!" in recap
    assert any("left behind or salad 14?" in t and "Written by **U14**" in t for t in reveals(bot))


async def test_leave_ends_game(bot):
    game = await voting(bot)
    await vote_all(bot, game, (11, 12, 13), "ab")
    asked = bot.channel.messages[1]
    await flow.leave_game(bot, game, 12)
    assert await status_of(bot, game) == "finished"
    assert "is over after 1 round (too few players left)." in bot.channel.texts[-1]
    assert asked.view is None
    assert len(await bot.repo.closed_questions(game["id"])) == 0


async def test_leave_with_lowered_minimum_keeps_going(db):
    bot = make_bot(db, min_players=2)
    game = await voting(bot)
    await flow.leave_game(bot, game, 13)
    assert await status_of(bot, game) == "active"
    await flow.leave_game(bot, game, 12)
    assert await status_of(bot, game) == "finished"


async def test_host_handover(bot):
    game = await lobby(bot)
    await flow.leave_game(bot, game, 11)
    assert (await bot.repo.get_game(game["id"]))["host_id"] == 12
    assert bot.channel.texts[-1] == "👑 **U12** is now the host."
    await flow.leave_game(bot, game, 13)
    assert (await bot.repo.get_game(game["id"]))["host_id"] == 12
    await flow.leave_game(bot, game, 12)
    assert await status_of(bot, game) == "finished"
    assert "was cancelled (everyone left)" in bot.channel.texts[-1]
    live = await running(bot, users=FOUR)
    await flow.leave_game(bot, live, 11)
    assert (await bot.repo.get_game(live["id"]))["host_id"] == 12


async def test_require_host_or_admin(bot):
    game = await lobby(bot)
    await require_host_or_admin(interaction_for(bot, 11), game)
    with pytest.raises(NotGameAdmin):
        await require_host_or_admin(interaction_for(bot, 12), game)
    await require_host_or_admin(interaction_for(bot, 12, manage_guild=True), game)
    await flow.leave_game(bot, game, 11)
    game = await bot.repo.get_game(game["id"])
    with pytest.raises(NotGameAdmin):
        await require_host_or_admin(interaction_for(bot, 11), game)
    await require_host_or_admin(interaction_for(bot, 12), game)


async def test_close_overdue_writing(bot):
    game = await running(bot, rounds=2)
    assert await flow.close_overdue(bot) == 0
    await write(bot, game, 11)
    await write(bot, game, 12)
    await expire_writing(bot, game)
    assert await flow.close_overdue(bot) == 1
    assert "**Question 1 of 2**" in bot.channel.texts[-1]
    assert await flow.close_overdue(bot) == 0


async def test_close_overdue_skips_ended_game(bot):
    game = await running(bot)
    await expire_writing(bot, game)
    await flow.finish(bot, game, note="ended by U11")
    count = len(bot.channel.messages)
    assert await flow.close_overdue(bot) == 0
    assert len(bot.channel.messages) == count


async def test_close_overdue_questions(bot):
    game = await voting(bot)
    assert await flow.close_overdue(bot) == 0
    await vote_all(bot, game, (11, 12, 13), "a")
    first = await expire_question(bot, game)
    assert await flow.close_overdue(bot) == 1
    assert (await bot.repo.get_question(first["id"]))["closed_at"]
    assert any("**A 2, B 0**: a 100/0 split." in t for t in bot.channel.texts)
    assert "**Question 2 of 3**" in bot.channel.texts[-1]
    assert await flow.close_overdue(bot) == 0
    writer = await writer_of(bot, await question(bot, game))
    await vote(bot, game, next(u for u in (11, 12, 13) if u != writer), "b")
    await expire_question(bot, game)
    assert await flow.close_overdue(bot) == 1
    assert any("Only 1 vote, so it scores 0." in t for t in bot.channel.texts)


async def test_resume_stalled_after_crash_mid_round(bot):
    game = await running(bot)
    rnd = await bot.repo.get_current_round(game["id"])
    assert await flow.resume_stalled(bot, grace=0) == 0
    for uid in (11, 12, 13):
        ctx = await bot.repo.write_context(rnd["id"], uid)
        await bot.repo.write_options(ctx["question_id"], f"soup {uid}", f"salad {uid}")
    await bot.repo.close_writing(rnd["id"], flow.RNG)
    assert await flow.resume_stalled(bot) == 0
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert "**Question 1 of 3**" in bot.channel.texts[-1]
    assert await flow.resume_stalled(bot, grace=0) == 0
    for number in (2, 3):
        await vote_all(bot, game, (11, 12, 13), "ab")
        await bot.repo.close_question((await question(bot, game))["id"])
        assert await flow.resume_stalled(bot, grace=0) == 1
        assert f"**Question {number} of 3**" in bot.channel.texts[-1]
    await vote_all(bot, game, (11, 12, 13), "ab")
    await bot.repo.close_question((await question(bot, game))["id"])
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert await status_of(bot, game) == "finished"
    assert sum("is over" in t for t in bot.channel.texts) == 1
    assert "tie with 100 points each!" in bot.channel.recap
    assert await flow.resume_stalled(bot, grace=0) == 0


async def test_resume_stalled_between_rounds(bot):
    game = await voting(bot, rounds=2)
    for _ in range(2):
        await play_question(bot, game, (11, 12, 13), "ab")
    await vote_all(bot, game, (11, 12, 13), "ab")
    q = await question(bot, game)
    await bot.repo.close_question(q["id"])
    await bot.repo.close_round(q["round_id"])
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert bot.channel.texts[-1].startswith("_ _\n✍️ **Round 2 of 2**")


async def test_resume_stalled_after_failed_round_post(bot):
    game = await lobby(bot)
    bot.channel.fail_with = blips(2)
    await flow.begin_game(bot, game, 2)
    assert bot.channel.messages == []
    assert await flow.resume_stalled(bot) == 0
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert bot.channel.texts[-1].startswith("_ _\n✍️ **Round 1 of 2**")
    for uid in (11, 12, 13):
        await write(bot, game, uid)
    for _ in range(3):
        await play_question(bot, game, (11, 12, 13), "ab")
    assert "🏁 **Round 1 of 2** is done." in bot.channel.texts[-2]
    assert bot.channel.texts[-1].startswith("_ _\n✍️ **Round 2 of 2**")


async def test_handle_vote(bot):
    game = await voting(bot)
    q = await question(bot, game)
    asked = bot.channel.messages[1]
    writer = await writer_of(bot, q)
    first, second = (u for u in (11, 12, 13) if u != writer)

    outsider = interaction_for(bot, 99, message=asked)
    await handle_vote(outsider, q["id"], "a")
    assert "Only players in this game can vote" in reply_of(outsider)

    own = interaction_for(bot, writer, message=asked)
    await handle_vote(own, q["id"], "a")
    assert reply_of(own) == "You wrote this one, so you sit this vote out."

    await flow.join_game(bot, game, 14)
    late = interaction_for(bot, 14, message=asked)
    await handle_vote(late, q["id"], "a")
    assert reply_of(late) == NOT_IN_ROUND
    assert await bot.repo.vote_counts(q["id"]) == (0, 0)

    click = interaction_for(bot, first, message=asked)
    await handle_vote(click, q["id"], "a")
    assert reply_of(click) == (
        "You picked **A**. Now guess how many players will pick A, counting yourself."
    )
    sent = click.response.send_message.await_args.kwargs
    assert sent["ephemeral"] is True
    (menu,) = sent["view"].children
    assert isinstance(menu, GuessSelect) and menu.item.custom_id == f"fiftyfifty:guess:{q['id']}"
    assert [o.value for o in menu.item.options] == ["0", "1", "2"]
    assert not any(o.default for o in menu.item.options)
    assert "1/2 voted, 0/2 guessed." in asked.content
    change = interaction_for(bot, first, message=asked)
    await handle_vote(change, q["id"], "b")
    assert reply_of(change).startswith("You picked **B**.")
    assert await bot.repo.vote_counts(q["id"]) == (0, 1)
    assert "1/2 voted, 0/2 guessed." in asked.content

    last = interaction_for(bot, second, message=asked)
    await handle_vote(last, q["id"], "a")
    assert "2/2 voted, 0/2 guessed." in asked.content
    assert (await bot.repo.get_question(q["id"]))["closed_at"] is None
    assert asked.view is not None and not reveals(bot)

    missing = interaction_for(bot, first)
    await handle_vote(missing, 9999, "a")
    assert reply_of(missing) == CLOSED


async def test_question_closes_only_after_every_guess(bot):
    game = await voting(bot)
    q = await question(bot, game)
    asked = bot.channel.messages[1]
    writer = await writer_of(bot, q)
    first, second = (u for u in (11, 12, 13) if u != writer)

    early = interaction_for(bot, first)
    await handle_guess(early, q["id"], "1")
    assert reply_of(early) == VOTE_FIRST
    own = interaction_for(bot, writer)
    await handle_guess(own, q["id"], "1")
    assert reply_of(own) == "You wrote this one, so you sit this vote out."
    outsider = interaction_for(bot, 99)
    await handle_guess(outsider, q["id"], "1")
    assert "Only players in this game can vote" in reply_of(outsider)
    assert await bot.repo.guesses(q["id"]) == ()

    await handle_vote(interaction_for(bot, first, message=asked), q["id"], "a")
    await handle_vote(interaction_for(bot, second, message=asked), q["id"], "b")
    for bad in ("", "x", "-1", "12", "1.5"):
        refused = interaction_for(bot, first)
        await handle_guess(refused, q["id"], bad)
        assert reply_of(refused) == "That guess is out of range.", bad
    guess = interaction_for(bot, first)
    await handle_guess(guess, q["id"], "2")
    assert reply_of(guess) == (
        "You guessed **2** for A. You can change it until the question closes."
    )
    assert guess.response.send_message.await_args.kwargs["ephemeral"] is True
    assert "2/2 voted, 1/2 guessed." in asked.content
    assert (await bot.repo.get_question(q["id"]))["closed_at"] is None

    changed = interaction_for(bot, first)
    await handle_guess(changed, q["id"], "1")
    assert reply_of(changed).startswith("You guessed **1** for A.")
    assert await bot.repo.guesses(q["id"]) == ((first, 1),)
    assert "2/2 voted, 1/2 guessed." in asked.content

    revote = interaction_for(bot, first, message=asked)
    await handle_vote(revote, q["id"], "a")
    assert reply_of(revote) == (
        "You picked **A**. Your guess stands at **1** for A. "
        "You can change both until the question closes."
    )
    (menu,) = revote.response.send_message.await_args.kwargs["view"].children
    assert [o.value for o in menu.item.options if o.default] == ["1"]
    assert (await bot.repo.get_question(q["id"]))["closed_at"] is None

    final = interaction_for(bot, second)
    await handle_guess(final, q["id"], "0")
    assert (await bot.repo.get_question(q["id"]))["closed_at"]
    assert asked.content.endswith("Voting is closed.") and asked.view is None
    (reveal,) = reveals(bot)
    assert "**A 1, B 1**: a 50/50 split." in reveal
    assert reveal.endswith(f"🎯 Guessed the split: **U{first}** +50")
    assert (await question(bot, game))["position"] == 2

    stale = interaction_for(bot, first, message=asked)
    await handle_vote(stale, q["id"], "a")
    assert reply_of(stale) == CLOSED
    stale_guess = interaction_for(bot, first)
    await handle_guess(stale_guess, q["id"], "1")
    assert reply_of(stale_guess) == CLOSED
    gone = interaction_for(bot, first)
    await handle_guess(gone, 9999, "1")
    assert reply_of(gone) == CLOSED


async def test_last_guess_on_a_changed_vote_closes(bot):
    game = await voting(bot)
    q = await question(bot, game)
    asked = bot.channel.messages[1]
    writer = await writer_of(bot, q)
    first, second = (u for u in (11, 12, 13) if u != writer)
    await vote(bot, game, first, "a", guess=1)
    await vote(bot, game, second, "a", guess=1)
    await handle_vote(interaction_for(bot, second, message=asked), q["id"], "b")
    assert (await bot.repo.get_question(q["id"]))["closed_at"]
    assert "**A 1, B 1**" in reveals(bot)[0]


async def test_guess_menu_survives_a_restart(bot):
    game = await voting(bot, users=FOUR)
    q = await question(bot, game)
    menu = guess_view(q["id"], await bot.repo.most_votes(q["id"]), 2).children[0]
    assert menu.item.placeholder == "How many will pick A?"
    assert [o.label for o in menu.item.options] == [
        "Nobody picks A",
        "1 picks A",
        "2 pick A",
        "3 pick A",
    ]
    assert (menu.item.min_values, menu.item.max_values) == (1, 1)
    match = GuessSelect.__discord_ui_compiled_template__.fullmatch(menu.item.custom_id)
    rebuilt = await GuessSelect.from_custom_id(interaction_for(bot, 11), menu.item, match)
    assert rebuilt.question_id == q["id"]
    assert rebuilt.item.custom_id == menu.item.custom_id
    assert [o.value for o in rebuilt.item.options] == ["0", "1", "2", "3"]
    restarted = make_bot(bot.repo.db)
    restarted.channel = bot.channel
    restarted.get_channel = bot.get_channel
    writer = await writer_of(bot, q)
    voter = next(u for u in FOUR if u != writer)
    await vote(bot, game, voter, "a")
    click = interaction_for(restarted, voter)
    rebuilt.item._values = ["3"]
    await rebuilt.callback(click)
    assert reply_of(click).startswith("You guessed **3** for A.")
    assert await bot.repo.guesses(q["id"]) == ((voter, 3),)
    assert len(guess_view(1, 99).children[0].item.options) == 12
    assert len(guess_view(1, 0).children[0].item.options) == 1


def game_totals(questions):
    from gaminator.games.fiftyfifty.services.rules import game_points

    return game_points(questions, ())


async def test_guess_points_count_in_totals_recap_and_leaderboard(bot):
    users = (11, 12, 13, 14, 15)
    sharp = 11
    game = await voting(bot, users, rounds=2)
    for _ in users:
        guesses = {uid: 2 if uid == sharp else 0 for uid in users}
        await play_question(bot, game, users, "abab", guesses)
    questions = await bot.repo.closed_questions(game["id"])
    assert all((q.a, q.b, q.points) == (2, 2, 100) for q in questions)
    assert all(len(q.guesses) == 4 for q in questions)
    totals = dict(game_totals(questions))
    assert totals == {11: 300, 12: 100, 13: 100, 14: 100, 15: 100}
    results = next(t for t in bot.channel.texts if "is done" in t)
    assert f"This round: U{sharp} 300, " in results and f"Totals: U{sharp} 300, " in results
    await flow.finish(bot, game, note="ended by U11")
    recap = bot.channel.recap
    assert f"🏆 **U{sharp}** wins with 300 points!" in recap
    board = bot.channel.texts[-1]
    assert f"`1.` **U{sharp}**: 1 game won, 300 points, 1 played" in board
    assert f"`1.` **U{sharp}**: 1 game won, 300 points, 1 played" in (
        await flow.leaderboard_reply(bot, 1, 11)
    )
    member = (await flow.leaderboard_reply(bot, 1, 11, member_id=sharp)).split("\n")
    assert member[1] == "1 question written, 100 points on average"
    assert member[2] == "200 points from guessing splits"


async def test_no_guess_points_under_two_votes(bot):
    game = await voting(bot)
    q = await question(bot, game)
    writer = await writer_of(bot, q)
    first = next(u for u in (11, 12, 13) if u != writer)
    await vote(bot, game, first, "a", guess=1)
    assert await close_current(bot, game)
    (reveal,) = reveals(bot)
    assert "Only 1 vote, so it scores 0." in reveal and f"🅰️ **U{first}**" in reveal
    assert "🎯" not in reveal
    assert dict(game_totals(await bot.repo.closed_questions(game["id"]))) == {writer: 0}


async def test_card_follows_join_leave_start_and_end(bot):
    game = await lobby(bot, users=(11, 12))
    card = await add_card(bot, game)
    await flow.join_game(bot, game, 13)
    assert card_names(card) == ["U11", "U12", "U13"]
    assert card.embed.title == "⚖️ Friday: sign-ups open"
    assert len(card.view.children) == 4
    await flow.leave_game(bot, game, 12)
    assert card_names(card) == ["U11", "U13"]
    await flow.join_game(bot, game, 12)
    await flow.begin_game(bot, game, 1)
    assert card.embed.title == "⚖️ Friday: in progress"
    assert [c.item.label for c in card.view.children] == ["Join", "Leave", "Rules"]
    await flow.join_game(bot, game, 14)
    assert sorted(card_names(card)) == ["U11", "U12", "U13", "U14"]
    assert card_names(card)[-1] == "U14"
    await flow.finish(bot, game, note="ended by U11")
    assert card.embed.title == "🏁 Friday: finished"
    assert card.view is None and card.edits[-1]["view"] is None


async def test_cancelled_lobby_card(bot):
    game = await lobby(bot, users=(11,))
    card = await add_card(bot, game)
    await flow.leave_game(bot, game, 11)
    assert card.embed.title == "🏁 Friday: cancelled"
    assert card.view is None


async def test_concurrent_joins_final_card(bot):
    game = await lobby(bot, users=(11,))
    card = await add_card(bot, game)
    await asyncio.gather(*(flow.join_game(bot, game, uid) for uid in (12, 13, 14, 15)))
    assert sorted(card_names(card)) == ["U11", "U12", "U13", "U14", "U15"]


async def test_status_reply(bot):
    game = await lobby(bot)
    waiting = await flow.status_reply(bot, game, 11)
    assert waiting.split("\n") == [
        "Fifty-Fifty game **Friday**, hosted by **U11**.",
        "**Players (3):** U11, U12, U13",
        "Sign-ups are open. It needs at least 3 to start.",
    ]
    await flow.begin_game(bot, game, 2)
    game = await bot.repo.get_game(game["id"])
    writing = (await flow.status_reply(bot, game, 11)).split("\n")
    assert writing[2].startswith("✍️ **Round 1 of 2**: writing. 0/3 written. Closes <t:")
    assert writing[3].startswith("You haven't written your question yet.")
    assert writing[4].startswith("Topic idea: **topic ") and len(writing) == 5
    await write(bot, game, 11, "soup")
    written = (await flow.status_reply(bot, game, 11)).split("\n")
    assert "1/3 written." in written[2] and len(written) == 4
    assert written[3] == "Your question: Would you rather soup or salad 11?"
    assert (await flow.status_reply(bot, game, 99)).endswith(
        "You're not in this game. Join with `/gaminator fiftyfifty join`."
    )
    await flow.join_game(bot, game, 14)
    assert (await flow.status_reply(bot, game, 14)).endswith("You're in from the next round.")
    await write(bot, game, 12)
    await write(bot, game, 13)
    q = await question(bot, game)
    writer = await writer_of(bot, q)
    voter = next(u for u in (11, 12, 13) if u != writer)
    asking = (await flow.status_reply(bot, game, voter)).split("\n")
    assert asking[2].startswith("🗳️ **Question 1 of 3** (round 1): Would you rather ")
    assert asking[3].startswith("0/2 voted, 0/2 guessed. Closes <t:")
    assert asking[4] == "🗳️ You haven't voted on this question yet."
    await vote(bot, game, voter, "b")
    assert (await flow.status_reply(bot, game, voter)).endswith(
        "🗳️ You picked **B**, and you haven't guessed the split yet."
    )
    assert "1/2 voted, 0/2 guessed." in await flow.status_reply(bot, game, voter)
    await vote(bot, game, voter, "b", guess=0)
    assert (await flow.status_reply(bot, game, voter)).endswith(
        "🗳️ You picked **B** and guessed **0** for A."
    )
    assert "1/2 voted, 1/2 guessed." in await flow.status_reply(bot, game, writer)
    assert (await flow.status_reply(bot, game, writer)).endswith(
        "You wrote this one, so you sit this vote out."
    )
    assert (await flow.status_reply(bot, game, 14)).endswith("You're in from the next round.")
    assert (await flow.status_reply(bot, game, 99)).endswith("`/gaminator fiftyfifty join`.")
    await bot.repo.close_question(q["id"])
    assert (await flow.status_reply(bot, game, voter)).endswith(
        "✍️ **Round 1 of 2**: the next question is on its way."
    )
    await bot.repo.close_round(q["round_id"])
    assert (await flow.status_reply(bot, game, voter)).endswith("The next round is on its way.")


async def play(bot, users, choices):
    """Run a whole one-round game. ``choices`` maps each writer to the votes their question gets."""
    game = await voting(bot, users)
    for _ in users:
        writer = await writer_of(bot, await question(bot, game))
        await play_question(bot, game, users, choices[writer])
    assert await status_of(bot, game) == "finished"
    return game


async def test_leaderboard_reply(bot):
    empty = await flow.leaderboard_reply(bot, 1, 11)
    assert empty.startswith("No finished Fifty-Fifty games yet.")
    await play(bot, (11, 12, 13), {11: "ab", 12: "aa", 13: "aa"})
    await play(bot, (11, 12, 14), {11: "aa", 12: "ab", 14: "ab"})
    lines = (await flow.leaderboard_reply(bot, 1, 11)).split("\n")
    assert lines == [
        "🏆 **Fifty-Fifty: all-time standings**",
        "`1.` **U11**: 1 game won, 100 points, 2 played",
        "`2.` **U12**: 1 game won, 100 points, 2 played",
        "`3.` **U14**: 1 game won, 100 points, 1 played",
        "`4.` **U13**: 0 games won, 0 points, 1 played",
    ]
    member = (await flow.leaderboard_reply(bot, 1, 11, member_id=12)).split("\n")
    assert member[:3] == [
        "**U12**: 1 game won, 100 points, 2 played",
        "2 questions written, 50 points on average",
        "Best splits:",
    ]
    assert member[3].startswith("• 100: Would you rather ") and "(1 to 1, <t:" in member[3]
    assert member[4].startswith("• 0: Would you rather ") and "(2 to 0, <t:" in member[4]
    assert len(member) == 5
    stranger = await flow.leaderboard_reply(bot, 1, 11, member_id=99)
    assert stranger == "**U99** hasn't played a finished game yet."
    assert (await flow.leaderboard_reply(bot, 2, 11)).startswith("No finished")


async def test_leaderboard_ignores_running_game(bot):
    game = await voting(bot)
    writer = await play_question(bot, game, (11, 12, 13), "ab")
    await vote_all(bot, game, (11, 12, 13), "ab")
    assert (await flow.leaderboard_reply(bot, 1, 11)).startswith("No finished")
    await flow.finish(bot, game, note="ended by U11")
    lines = (await flow.leaderboard_reply(bot, 1, 11)).split("\n")
    rest = [u for u in (11, 12, 13) if u != writer]
    assert lines[1:] == [
        f"`1.` **U{writer}**: 1 game won, 100 points, 1 played",
        f"`2.` **U{rest[0]}**: 0 games won, 0 points, 1 played",
        f"`3.` **U{rest[1]}**: 0 games won, 0 points, 1 played",
    ]


async def test_leaderboard_departed_member_falls_back_to_mention(bot):
    await play(bot, (11, 12, 13), {11: "aa", 12: "ab", 13: "aa"})

    async def fetch_user(user_id):
        raise aiohttp.ClientError("gone")

    bot.get_user = lambda user_id: None
    bot.fetch_user = fetch_user
    assert "**<@12>**: 1 game won" in await flow.leaderboard_reply(bot, 1, 11)


async def test_leaderboard_shows_caller_outside_top_ten(bot):
    users = tuple(range(101, 113))
    await play(bot, users, dict.fromkeys(users, "a"))
    lines = (await flow.leaderboard_reply(bot, 1, 112)).split("\n")
    assert len(lines) == 13 and lines[11] == ""
    assert lines[12] == "`12.` **U112**: 0 games won, 0 points, 1 played"
    assert len((await flow.leaderboard_reply(bot, 1, 101)).split("\n")) == 11

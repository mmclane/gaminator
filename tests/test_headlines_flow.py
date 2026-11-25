"""Headlines game flow, driven through a fake bot and channel."""

import asyncio
import logging
import re
from types import SimpleNamespace

import discord
import pytest
from headlines_fakes import (
    CHANNEL_ID,
    add_card,
    card_names,
    interaction_for,
    make_bot,
    pairings_of,
    play_to_voting,
    play_to_writing,
)

from gaminator.core.messaging import NO_MENTIONS
from gaminator.core.util import GameError, NotGameAdmin
from gaminator.games.headlines.commands.group import require_host_or_admin
from gaminator.games.headlines.forms import AnswerButton, WriteButton
from gaminator.games.headlines.services import game as flow
from gaminator.games.headlines.services.rules import (
    NEXT_ROUND,
    NOT_PLAYING,
    OWES_ANSWER,
    OWES_HEADLINE,
    OWES_VOTE,
)
from gaminator.games.headlines.vote import VoteSelect

FOUR = (11, 12, 13, 14)
EARLIER = "2000-01-01T00:00:00+00:00"
STAMPS = re.compile(r"<t:[0-9]+:R>")
DEALT = re.compile(r"question [0-9]+\?")
PAIRING = re.compile(r"📰 \*\*([0-9]+) of ([0-9]+)\*\*")
SKIPPED = "This round was skipped: I couldn't post the next step."
UNVOTED = "Round 1's headlines were not voted on, so they score nothing."


@pytest.fixture
async def bot(db):
    return make_bot(db)


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="x"), "x")


def unavailable(times=1):
    return [http_error(discord.HTTPException, 503) for _ in range(times)]


async def lobby(bot, users=(11, 12, 13)):
    game = await bot.repo.create_game(1, "Friday", users[0], CHANNEL_ID)
    for uid in users:
        await bot.repo.join(game["id"], uid)
    return game


async def running(bot, users=(11, 12, 13), rounds=2):
    """A started game. Round 1 is open for answers."""
    game = await lobby(bot, users)
    await flow.begin_game(bot, game, rounds)
    return await bot.repo.get_game(game["id"])


async def current(bot, game):
    return await bot.repo.get_current_round(game["id"])


async def rounds_of(bot, game):
    return await bot.repo._all(
        "SELECT * FROM headlines_rounds WHERE game_id = ? ORDER BY id", (game["id"],)
    )


async def status(bot, game):
    return (await bot.repo.get_game(game["id"]))["status"]


async def expire(bot, column):
    """Move the open round's deadline for a step into the past."""
    async with bot.repo.db.transaction() as conn:
        await conn.execute(
            f"UPDATE headlines_rounds SET {column} = ? WHERE closed_at IS NULL", (EARLIER,)
        )


def message_of(bot, message_id):
    return next(m for m in bot.channel.messages if m.id == message_id)


def with_text(bot, text):
    return [m for m in bot.channel.messages if text in (m.sent or "")]


def recaps(bot):
    return [t for t in bot.channel.texts if "is over after" in t]


def pairing_posts(bot):
    return [m.sent for m in bot.channel.messages if PAIRING.search(m.sent or "")]


def results(bot):
    return [m.sent for m in bot.channel.messages if "the results" in (m.sent or "")]


def vote_posts(bot):
    return with_text(bot, "vote for the best headline")


def everything_public(bot):
    """Every text the channel has ever shown: sent messages, edits, and menu entries."""
    seen = []
    for message in bot.channel.messages:
        seen += [message.sent or "", message.content or ""]
        seen += [edit.get("content") or "" for edit in message.edits]
        view = message.kwargs.get("view") if hasattr(message, "kwargs") else None
        for child in view.children if view is not None else []:
            for option in getattr(child.item, "options", []):
                seen += [option.label, option.description or ""]
    return STAMPS.sub("", "\n".join(seen))


async def vote(bot, rnd, voter, writer):
    """Vote for the headline a user wrote, as the menu does: save, then close when all are in."""
    target = (await pairings_of(bot, rnd))[writer]
    reply = await flow.submit_vote(bot, rnd["id"], voter, str(target["id"]))
    await flow.close_step_if_done(bot, rnd["id"])
    return reply


async def vote_all(bot, game, rnd, voters=None):
    """Every voter picks the headline of the lowest-numbered writer other than themselves."""
    pairs = await pairings_of(bot, rnd)
    written = sorted(uid for uid, row in pairs.items() if row["position"] is not None)
    if voters is None:
        players = await bot.repo.list_players(game["id"], active_only=True)
        voters = [p["user_id"] for p in players]
    for voter in voters:
        choice = next(uid for uid in written if uid != voter)
        reply = await vote(bot, rnd, voter, choice)
        assert reply.startswith("Your vote is on headline"), reply


async def play_round(bot, game):
    """Play the open round from its answers to its result. Returns the closed round."""
    rnd = await play_to_voting(bot, game)
    await vote_all(bot, game, rnd)
    return await bot.repo.get_round(rnd["id"])


def stop_reveal_at(monkeypatch, call, error=None):
    """Make the reveal fail when it builds its ``call``-th pairing message."""
    real = flow.pairing_text_for
    seen = []

    async def failing(*args):
        seen.append(1)
        if len(seen) == call:
            raise error or RuntimeError("boom")
        return await real(*args)

    monkeypatch.setattr(flow, "pairing_text_for", failing)


async def test_begin_game_posts_answer_step(bot):
    game = await lobby(bot)
    reply = await flow.begin_game(bot, game, 2)
    assert reply == "Headlines game **Friday** has started with 3 players and 2 rounds."
    rnd = await current(bot, game)
    (message,) = bot.channel.messages
    assert rnd["number"] == 1 and rnd["answer_message_id"] == message.id
    assert message.sent.startswith("_ _\n## 📰 Round 1 of 2: answer your question\n")
    assert "0/3 answered." in message.sent
    (button,) = message.view.children
    assert isinstance(button, AnswerButton) and button.round_id == rnd["id"]
    assert button.item.label == "Answer"
    assert message.kwargs["allowed_mentions"] is NO_MENTIONS
    assert (await bot.repo.get_game(game["id"]))["max_rounds"] == 2


async def test_begin_game_refusals(bot):
    game = await lobby(bot, users=(11, 12))
    with pytest.raises(GameError, match="Need at least 3 players to start; 2 joined so far."):
        await flow.begin_game(bot, game, 2)
    await bot.repo.join(game["id"], 13)
    await flow.begin_game(bot, game, 2)
    with pytest.raises(GameError, match="already started"):
        await flow.begin_game(bot, game, 2)
    with pytest.raises(GameError, match="already started"):
        await flow.begin_game(bot, await bot.repo.get_game(game["id"]), 2)
    assert len(bot.channel.messages) == 1


async def test_double_start_one_round(bot):
    game = await lobby(bot)
    done = await asyncio.gather(
        flow.begin_game(bot, game, 2), flow.begin_game(bot, game, 2), return_exceptions=True
    )
    assert sum(isinstance(d, GameError) for d in done) == 1
    assert len(await rounds_of(bot, game)) == 1
    assert len(with_text(bot, "answer your question")) == 1


async def test_progress_edits_keep_view(bot):
    game = await running(bot)
    rnd = await current(bot, game)
    answer_message = message_of(bot, rnd["answer_message_id"])
    await flow.submit_answer(bot, rnd["id"], 11, "first answer")
    assert "1/3 answered." in answer_message.content
    assert isinstance(answer_message.view.children[0], AnswerButton)
    assert all("view" not in edit for edit in answer_message.edits)
    await flow.submit_answer(bot, rnd["id"], 11, "changed answer")
    assert len(answer_message.edits) == 1

    rnd = await play_to_writing(bot, game)
    write_message = message_of(bot, rnd["write_message_id"])
    writer = next(iter(await pairings_of(bot, rnd)))
    await flow.submit_headline(bot, rnd["id"], writer, "first headline")
    assert "1/3 headlines in." in write_message.content
    assert isinstance(write_message.view.children[0], WriteButton)
    assert all("view" not in edit for edit in write_message.edits)

    rnd = await play_to_voting(bot, game)
    vote_message = message_of(bot, rnd["vote_message_id"])
    assert await vote(bot, rnd, 11, 12) == "Your vote is on headline **{}**. You can {}".format(
        (await pairings_of(bot, rnd))[12]["position"], "change it until voting closes."
    )
    assert "1/3 voted." in vote_message.content
    assert isinstance(vote_message.view.children[0], VoteSelect)
    assert all("view" not in edit for edit in vote_message.edits)
    assert len(vote_message.view.children[0].item.options) == 3


async def test_all_answered_opens_writing(bot):
    game = await running(bot)
    rnd = await current(bot, game)
    answer_message = message_of(bot, rnd["answer_message_id"])
    seen = {}

    async def on_send(message):
        if "write the headlines" in message.sent:
            seen["answer_view"] = answer_message.view
            seen["answer_text"] = answer_message.content

    bot.channel.on_send = on_send
    rnd = await play_to_writing(bot, game)
    assert seen["answer_view"] is not None and "answered." in seen["answer_text"]
    write_message = message_of(bot, rnd["write_message_id"])
    assert write_message is bot.channel.messages[-1]
    assert write_message.sent.startswith("_ _\n## 📰 Round 1 of 2: write the headlines\n")
    assert "0/3 headlines in." in write_message.sent
    (button,) = write_message.view.children
    assert isinstance(button, WriteButton) and button.round_id == rnd["id"]
    assert answer_message.view is None
    assert answer_message.content.endswith("\nAnswering is closed: 3 answers in.")
    pairs = await pairings_of(bot, rnd)
    assert sorted(pairs) == [11, 12, 13]
    assert all(row["quoted_user_id"] != writer for writer, row in pairs.items())


async def test_writing_post_fails_abandons_round(bot):
    game = await running(bot)
    rnd = await current(bot, game)
    for uid in (11, 12):
        await flow.submit_answer(bot, rnd["id"], uid, "an answer")
    bot.channel.fail_with = unavailable(2)
    await flow.submit_answer(bot, rnd["id"], 13, "an answer")
    await flow.close_step_if_done(bot, rnd["id"])
    rnd = await bot.repo.get_round(rnd["id"])
    assert rnd["outcome"] == "abandoned" and rnd["write_message_id"] is None
    (answer_message,) = bot.channel.messages
    assert answer_message.content.endswith(f"\n{SKIPPED}") and answer_message.view is None
    assert await status(bot, game) == "active"
    assert await flow.resume_stalled(bot, grace=30) == 0
    assert await flow.resume_stalled(bot, grace=0) == 1
    again = await current(bot, game)
    assert again["id"] != rnd["id"] and again["number"] == 1
    assert bot.channel.messages[-1].view is not None


async def test_no_answers_ends_game_idle(bot):
    game = await running(bot)
    rnd = await current(bot, game)
    await expire(bot, "answer_deadline_at")
    assert await flow.close_overdue(bot) == 1
    assert (await bot.repo.get_round(rnd["id"]))["outcome"] == "no_answers"
    assert await status(bot, game) == "finished"
    answer_message = message_of(bot, rnd["answer_message_id"])
    assert answer_message.view is None
    assert answer_message.content.endswith("\nAnswering is closed: 0 answers in.")
    assert "_ _\n⏭️ Not enough answers came in, so round 1 is over." in bot.channel.texts
    (recap,) = recaps(bot)
    assert "is over after 1 round (nobody answered this round)." in recap
    assert "Nobody scored." in recap


async def test_questions_never_in_channel(bot, caplog):
    game = await running(bot, rounds=2)
    with caplog.at_level(logging.DEBUG, logger="gaminator"):
        await play_round(bot, game)
        await play_round(bot, game)
        await flow.status_reply(bot, game, 11)
    assert await status(bot, game) == "finished"
    dealt = await bot.repo.used_questions(game["id"])
    assert len(dealt) == 6 and all(DEALT.fullmatch(q) for q in dealt)
    assert not DEALT.search(everything_public(bot))
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert not DEALT.search(logged)
    assert "answer of a" not in logged and "headline by w" not in logged


async def test_unwritten_quote_dropped(bot):
    game = await running(bot)
    rnd = await play_to_writing(bot, game)
    rnd = await play_to_voting(bot, game, {11: "first headline", 12: "second headline"})
    pairs = await pairings_of(bot, rnd)
    assert sorted(pairs[uid]["position"] for uid in (11, 12)) == [1, 2]
    assert pairs[13]["position"] is None and pairs[13]["shown_at"] is None
    assert len(pairing_posts(bot)) == 2
    assert all("of 2**" in post for post in pairing_posts(bot))
    (menu,) = vote_posts(bot)[0].view.children
    assert len(menu.item.options) == 2
    assert f"({'U' + str(pairs[13]['quoted_user_id'])})" not in "\n".join(pairing_posts(bot))
    reply = await flow.submit_vote(bot, rnd["id"], 11, str(pairs[13]["id"]))
    assert reply == "Pick a headline from the menu."


async def test_writer_leaves_before_writing(bot):
    game = await running(bot, FOUR)
    rnd = await play_to_writing(bot, game)
    for uid in (11, 12, 13):
        await flow.submit_headline(bot, rnd["id"], uid, f"headline by w{uid}")
        await flow.close_step_if_done(bot, rnd["id"])
    assert (await current(bot, game))["write_closed_at"] is None
    await flow.leave_game(bot, game, 14)
    rnd = await current(bot, game)
    assert rnd["write_closed_at"] and rnd["vote_deadline_at"]
    pairs = await pairings_of(bot, rnd)
    assert pairs[14]["position"] is None and pairs[14]["headline"] is None
    assert len(pairing_posts(bot)) == 3 and len(vote_posts(bot)) == 1


async def test_writer_rejoins_and_writes(bot):
    game = await running(bot, FOUR)
    rnd = await play_to_writing(bot, game)
    write_message = message_of(bot, rnd["write_message_id"])
    await flow.leave_game(bot, game, 14)
    assert "0/3 headlines in." in write_message.content
    assert await flow.submit_headline(bot, rnd["id"], 14, "gone") == "You've left the game."
    assert await flow.join_game(bot, game, 14) == "You're back in."
    assert "0/4 headlines in." in write_message.content and write_message.view is not None
    reply = await flow.submit_headline(bot, rnd["id"], 14, "back with a headline")
    assert reply.startswith("Saved. Your headline: **back with a headline**.")
    assert (await pairings_of(bot, rnd))[14]["headline"] == "back with a headline"


async def test_writer_leaves_after_writing_still_scores(bot):
    game = await running(bot, FOUR, rounds=1)
    rnd = await play_to_writing(bot, game)
    await flow.submit_headline(bot, rnd["id"], 14, "headline by w14")
    await flow.leave_game(bot, game, 14)
    rnd = await play_to_voting(bot, game, {uid: f"headline by w{uid}" for uid in (11, 12, 13)})
    assert len(pairing_posts(bot)) == 4
    for voter in (11, 12, 13):
        await vote(bot, rnd, voter, 14)
    assert await status(bot, game) == "finished"
    (result,) = results(bot)
    assert "(3 votes): headline by **U14** +300" in result
    assert "🏆 **U14** wins with 300 points!" in recaps(bot)[0]


async def test_all_written_reveals(bot):
    game = await running(bot)
    rnd = await play_to_writing(bot, game)
    write_message = message_of(bot, rnd["write_message_id"])
    seen = {}

    async def on_send(message):
        if "the headlines\n" in message.sent:
            seen["at_intro"] = write_message.view
        elif "**1 of 3**" in message.sent:
            seen["at_first"] = write_message.view

    bot.channel.on_send = on_send
    before = len(bot.channel.messages)
    rnd = await play_to_voting(bot, game)
    assert seen["at_intro"] is not None and seen["at_first"] is None
    assert write_message.content.endswith("\nHeadlines are closed: 3 to read.")
    intro, *posts, menu = bot.channel.messages[before:]
    assert intro.sent == (
        "_ _\n## 📰 Round 1 of 2: the headlines\n"
        "3 stories tonight. The vote opens after the last one."
    )
    assert [PAIRING.search(p.sent).groups() for p in posts] == [("1", "3"), ("2", "3"), ("3", "3")]
    by_position = {row["position"]: row for row in (await pairings_of(bot, rnd)).values()}
    for position, post in enumerate(posts, start=1):
        row = by_position[position]
        assert post.sent == flow.pairing_text(
            position, 3, row["headline"], row["answer"], f"U{row['quoted_user_id']}"
        )
        assert post.view is None and row["shown_at"]
    assert menu.id == rnd["vote_message_id"] and "0/3 voted." in menu.sent
    assert rnd["reveal_active_at"] and rnd["eligible_voters"] == 3
    (select,) = menu.view.children
    assert isinstance(select, VoteSelect) and select.round_id == rnd["id"]
    assert [o.value for o in select.item.options] == [str(by_position[n]["id"]) for n in (1, 2, 3)]
    assert [o.label for o in select.item.options] == [
        f"{n}. {by_position[n]['headline']}" for n in (1, 2, 3)
    ]
    assert [o.description for o in select.item.options] == [
        by_position[n]["answer"] for n in (1, 2, 3)
    ]


async def test_reveal_hides_writer(bot):
    game = await running(bot, FOUR)
    rnd = await play_to_voting(bot, game)
    by_position = {row["position"]: row for row in (await pairings_of(bot, rnd)).values()}
    posts = pairing_posts(bot)
    assert len(posts) == 4
    for position, text in enumerate(posts, start=1):
        row = by_position[position]
        assert f"(U{row['quoted_user_id']})" in text
        assert f"U{row['writer_user_id']}" not in text
    public = everything_public(bot)
    assert "headline by **" not in public and "quote from" not in public


async def test_reveal_pause(bot, monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append((seconds, len(pairing_posts(bot))))

    monkeypatch.setattr(flow, "REVEAL_PAUSE", 4)
    monkeypatch.setattr(flow.asyncio, "sleep", fake_sleep)
    game = await running(bot)
    await play_to_voting(bot, game)
    assert slept == [(4, 0), (4, 1), (4, 2)]
    await flow.finish(bot, game)

    slept.clear()
    ten = tuple(range(21, 31))
    other = await bot.repo.create_game(2, "Big", ten[0], CHANNEL_ID)
    for uid in ten:
        await bot.repo.join(other["id"], uid)
    bot.channel.messages.clear()
    await flow.begin_game(bot, other, 1)
    await play_to_voting(bot, other)
    assert slept == [(2, n) for n in range(10)]


async def test_reveal_failed_post_moves_on(bot):
    game = await running(bot)

    async def on_send(message):
        if "**1 of 3**" in message.sent:
            bot.channel.fail_with = unavailable(2)

    bot.channel.on_send = on_send
    rnd = await play_to_voting(bot, game)
    assert [PAIRING.search(p).group(1) for p in pairing_posts(bot)] == ["1", "3"]
    by_position = {row["position"]: row for row in (await pairings_of(bot, rnd)).values()}
    assert by_position[1]["shown_at"] and by_position[3]["shown_at"]
    assert by_position[2]["shown_at"] is None
    (menu,) = vote_posts(bot)
    assert len(menu.view.children[0].item.options) == 3 and rnd["vote_deadline_at"]


async def test_reveal_post_timeout_moves_on(bot, monkeypatch, caplog):
    game = await running(bot)
    real = flow.post_with_view

    async def slow(bot_, game_, text, view, **kwargs):
        if "**2 of 3**" in text:
            await asyncio.sleep(5)
        return await real(bot_, game_, text, view, **kwargs)

    monkeypatch.setattr(flow, "REVEAL_POST_TIMEOUT", 0.05)
    monkeypatch.setattr(flow, "post_with_view", slow)
    with caplog.at_level(logging.WARNING):
        rnd = await play_to_voting(bot, game)
    assert [PAIRING.search(p).group(1) for p in pairing_posts(bot)] == ["1", "3"]
    by_position = {row["position"]: row for row in (await pairings_of(bot, rnd)).values()}
    assert by_position[2]["shown_at"] is None
    assert any("a reveal post timed out" in r.getMessage() for r in caplog.records)
    assert len(vote_posts(bot)) == 1 and rnd["vote_deadline_at"]


async def test_reveal_slow_name_lookup_falls_back(bot, monkeypatch, caplog):
    game = await running(bot)
    known = bot.get_user
    bot.get_user = lambda user_id: None

    async def slow_fetch(user_id):
        if user_id == 12:
            await asyncio.sleep(5)
        return known(user_id)

    bot.fetch_user = slow_fetch
    monkeypatch.setattr(flow, "REVEAL_POST_TIMEOUT", 0.05)
    with caplog.at_level(logging.WARNING):
        rnd = await play_to_voting(bot, game)
    posts = "\n".join(pairing_posts(bot))
    assert len(pairing_posts(bot)) == 3 and rnd["vote_deadline_at"]
    assert "(<@12>)" in posts and "(U11)" in posts and "(U13)" in posts
    assert any("name lookup during a reveal timed out" in r.getMessage() for r in caplog.records)
    assert all(m.kwargs["allowed_mentions"] is NO_MENTIONS for m in bot.channel.messages)


def test_reveal_stamps_stay_inside_the_stall_grace():
    """A name lookup, a post and the longest pause, plus a second for rounding, beat the grace."""
    longest_gap = 2 * 10 + 4 + 1
    assert flow.REVEAL_POST_TIMEOUT == 10 and longest_gap < flow.STALL_GRACE_SECONDS == 30


async def test_reveal_exception_stops_without_vote(bot, monkeypatch, caplog):
    game = await running(bot)
    with monkeypatch.context() as patch:
        stop_reveal_at(patch, 2)
        with caplog.at_level(logging.ERROR):
            rnd = await play_to_voting(bot, game)
    assert any("reveal of round" in r.getMessage() and r.exc_info for r in caplog.records)
    assert len(pairing_posts(bot)) == 1 and vote_posts(bot) == []
    assert rnd["write_closed_at"] and rnd["vote_deadline_at"] is None and not rnd["closed_at"]
    assert await flow.resume_stalled(bot, grace=30) == 0
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert [PAIRING.search(p).group(1) for p in pairing_posts(bot)] == ["1", "2", "3"]
    assert len(with_text(bot, "stories tonight")) == 1
    assert len(vote_posts(bot)) == 1
    assert (await current(bot, game))["vote_deadline_at"]


async def test_reveal_channel_gone_ends_game(bot):
    game = await running(bot)

    async def on_send(message):
        if "**1 of 3**" in message.sent:
            bot.channel.fail_with = [http_error(discord.Forbidden, 403)]

    bot.channel.on_send = on_send
    rnd = await play_to_voting(bot, game)
    assert await status(bot, game) == "finished"
    assert rnd["outcome"] == "abandoned" and vote_posts(bot) == []
    assert len(pairing_posts(bot)) == 1
    assert "(I couldn't post in the game channel)" in recaps(bot)[0]


async def test_resume_posts_only_unshown(bot, db, monkeypatch):
    game = await running(bot, FOUR)
    with monkeypatch.context() as patch:
        stop_reveal_at(patch, 3)
        rnd = await play_to_voting(bot, game)
    assert len(pairing_posts(bot)) == 2 and vote_posts(bot) == []
    restarted = make_bot(db)
    assert await flow.resume_stalled(restarted, grace=0) == 1
    first, second, menu = restarted.channel.messages
    assert [PAIRING.search(m.sent).groups() for m in (first, second)] == [("3", "4"), ("4", "4")]
    assert "vote for the best headline" in menu.sent
    assert len(menu.view.children[0].item.options) == 4
    rnd = await restarted.repo.get_round(rnd["id"])
    assert rnd["vote_message_id"] == menu.id
    assert all(row["shown_at"] for row in (await pairings_of(restarted, rnd)).values())
    assert await flow.resume_stalled(restarted, grace=0) == 0


async def test_one_headline_skips_vote(bot):
    game = await running(bot)
    await play_to_writing(bot, game)
    before = len(bot.channel.messages)
    rnd = await play_to_voting(bot, game, {12: "the only headline"})
    assert rnd["outcome"] == "one_headline" and rnd["vote_deadline_at"] is None
    intro, post, line, next_round = bot.channel.messages[before:]
    assert intro.sent.endswith("\n1 story tonight.")
    assert "**1 of 1**" in post.sent and "## the only headline" in post.sent
    assert line.sent == "_ _\n⏭️ A vote needs at least two headlines, so this one is just for fun."
    assert "Round 2 of 2: answer your question" in next_round.sent
    assert vote_posts(bot) == [] and results(bot) == []
    assert await bot.repo.scored_pairings(game["id"]) == []
    assert await bot.repo.game_user_ids(game["id"]) == []
    assert await status(bot, game) == "active"
    assert await bot.repo.rounds_played(game["id"]) == 1


async def test_no_headlines_ends_game_idle(bot):
    game = await running(bot)
    rnd = await play_to_writing(bot, game)
    await expire(bot, "write_deadline_at")
    assert await flow.close_overdue(bot) == 1
    assert (await bot.repo.get_round(rnd["id"]))["outcome"] == "no_headlines"
    write_message = message_of(bot, rnd["write_message_id"])
    assert write_message.view is None
    assert write_message.content.endswith("\nHeadlines are closed: 0 to read.")
    assert "_ _\n⏭️ No headlines came in, so round 1 is over." in bot.channel.texts
    assert await status(bot, game) == "finished"
    assert "(no headlines came in this round)" in recaps(bot)[0]
    assert pairing_posts(bot) == []


async def test_previews_suppressed(bot):
    game = await running(bot, rounds=1)
    await play_round(bot, game)
    checked = 0
    for message in bot.channel.messages:
        text = message.sent or ""
        if PAIRING.search(text) or "the results" in text or "is over after" in text:
            assert message.kwargs["suppress_embeds"] is True, text
            checked += 1
    assert checked == 3 + 1 + 1


async def test_votes_hidden_until_result(bot):
    game = await running(bot, FOUR)
    rnd = await play_to_voting(bot, game)
    await vote(bot, rnd, 11, 12)
    await vote(bot, rnd, 12, 11)
    await vote(bot, rnd, 13, 11)
    public = everything_public(bot)
    assert "3/4 voted." in public
    for hidden in ("headline by **", "quote from", " vote)", " votes)", "+100", "Totals"):
        assert hidden not in public, hidden
    await vote(bot, rnd, 14, 11)
    (result,) = results(bot)
    assert "(3 votes): headline by **U11** +300" in result
    assert "(1 vote): headline by **U12** +100" in result


async def test_everyone_voted_posts_result(bot):
    game = await running(bot)
    rnd = await play_to_voting(bot, game)
    vote_message = message_of(bot, rnd["vote_message_id"])
    seen = {}

    async def on_send(message):
        if "the results" in message.sent:
            seen["menu"] = vote_message.view

    bot.channel.on_send = on_send
    await vote_all(bot, game, rnd)
    assert seen["menu"] is not None
    assert vote_message.view is None
    assert vote_message.content == (
        "_ _\n## 📰 Round 1 of 2: vote for the best headline\nVoting is closed."
    )
    rnd = await bot.repo.get_round(rnd["id"])
    assert rnd["outcome"] == "voted" and len(results(bot)) == 1
    assert (await current(bot, game))["number"] == 2


async def test_result_points_match_rules(bot):
    game = await running(bot)
    rnd = await play_to_voting(bot, game)
    await vote_all(bot, game, rnd)
    pairs = await pairings_of(bot, rnd)
    (result,) = results(bot)
    lines = result.split("\n")
    assert lines[:2] == ["_ _", "## 🏆 Round 1 of 2: the results"]
    assert lines[2] == (
        f"`{pairs[11]['position']}.` headline by w11 (2 votes): headline by **U11** +200, "
        f"quote from **U{pairs[11]['quoted_user_id']}** +50"
    )
    assert lines[3] == (
        f"`{pairs[12]['position']}.` headline by w12 (1 vote): headline by **U12** +100, "
        f"quote from **U{pairs[12]['quoted_user_id']}** +25"
    )
    assert lines[4] == (
        f"`{pairs[13]['position']}.` headline by w13 (0 votes): headline by **U13** +0, "
        f"quote from **U{pairs[13]['quoted_user_id']}** +0"
    )
    points = dict.fromkeys((11, 12, 13), 0)
    points[11] += 200
    points[pairs[11]["quoted_user_id"]] += 50
    points[12] += 100
    points[pairs[12]["quoted_user_id"]] += 25
    ordered = sorted(points.items(), key=lambda kv: (-kv[1], kv[0]))
    assert lines[5] == "Totals: " + ", ".join(f"U{uid} {total}" for uid, total in ordered)
    assert lines[6] == "Round 2 starts now."


async def test_vote_deadline_closes(bot):
    game = await running(bot)
    rnd = await play_to_voting(bot, game)
    await vote(bot, rnd, 11, 12)
    assert await flow.close_overdue(bot) == 0
    await expire(bot, "vote_deadline_at")
    assert await flow.close_overdue(bot) == 1
    assert (await bot.repo.get_round(rnd["id"]))["outcome"] == "voted"
    assert "(1 vote): headline by **U12** +100" in results(bot)[0]
    assert await status(bot, game) == "active" and (await current(bot, game))["number"] == 2


async def test_no_votes_ends_game_idle(bot):
    game = await running(bot)
    rnd = await play_to_voting(bot, game)
    await expire(bot, "vote_deadline_at")
    assert await flow.close_overdue(bot) == 1
    (result,) = results(bot)
    assert result.count("(0 votes)") == 3 and "Totals" not in result
    assert "starts now" not in result
    assert message_of(bot, rnd["vote_message_id"]).view is None
    assert await status(bot, game) == "finished"
    assert "is over after 1 round (nobody voted this round)." in recaps(bot)[0]
    assert "Nobody scored." in recaps(bot)[0]


async def test_one_away_player_cannot_end_game(bot):
    game = await running(bot)
    for number in (1, 2):
        rnd = await current(bot, game)
        assert rnd["number"] == number
        for uid in (11, 12):
            await flow.submit_answer(bot, rnd["id"], uid, f"answer of a{uid}")
            await flow.close_step_if_done(bot, rnd["id"])
        await expire(bot, "answer_deadline_at")
        assert await flow.close_overdue(bot) == 1
        pairs = await pairings_of(bot, rnd)
        assert {uid: row["quoted_user_id"] for uid, row in pairs.items()} == {11: 12, 12: 11}
        assert await flow.submit_headline(bot, rnd["id"], 13, "none") == (
            "You don't have a quote to write for this round. Sit back, the headlines are coming."
        )
        for uid in (11, 12):
            await flow.submit_headline(bot, rnd["id"], uid, f"headline by w{uid}")
            await flow.close_step_if_done(bot, rnd["id"])
        rnd = await bot.repo.get_round(rnd["id"])
        assert rnd["vote_deadline_at"] and rnd["eligible_voters"] == 3
        await vote(bot, rnd, 11, 12)
        await vote(bot, rnd, 12, 11)
        assert (await bot.repo.get_round(rnd["id"]))["closed_at"] is None
        await expire(bot, "vote_deadline_at")
        assert await flow.close_overdue(bot) == 1
    assert await status(bot, game) == "finished"
    (recap,) = recaps(bot)
    assert "is over after 2 rounds.\n" in recap
    assert "🏆 **U11** and **U12** tie with 250 points each!" in recap
    assert "Also: U13 0" in recap


async def test_nobody_eligible_closes_at_once(bot):
    game = await running(bot)
    for uid in (14, 15, 16):
        await flow.join_game(bot, game, uid)
    rnd = await play_to_writing(bot, game, {uid: f"answer of a{uid}" for uid in (11, 12, 13)})
    for uid in (11, 12):
        await flow.submit_headline(bot, rnd["id"], uid, f"headline by w{uid}")
        await flow.leave_game(bot, game, uid)
    assert (await current(bot, game))["write_closed_at"] is None
    await flow.leave_game(bot, game, 13)
    rnd = await bot.repo.get_round(rnd["id"])
    assert rnd["outcome"] == "voted" and rnd["eligible_voters"] == 0
    assert len(pairing_posts(bot)) == 2
    (menu,) = vote_posts(bot)
    assert menu.view is None and menu.content.endswith("\nVoting is closed.")
    assert results(bot)[0].count("(0 votes)") == 2
    assert await status(bot, game) == "active"
    assert (await current(bot, game))["number"] == 2
    assert (await bot.repo.get_game(game["id"]))["host_id"] == 14


async def test_multi_round_game(bot):
    game = await running(bot, rounds=3)
    for number in (1, 2, 3):
        assert (await current(bot, game))["number"] == number
        await play_round(bot, game)
    first, second, third = results(bot)
    assert "## 🏆 Round 1 of 3: the results" in first and "Round 2 starts now." in first
    assert "Totals: " in second and "Round 3 starts now." in second
    assert "Totals" not in third and "starts now" not in third
    assert await status(bot, game) == "finished"
    assert len(await bot.repo.used_questions(game["id"])) == 9
    (recap,) = recaps(bot)
    assert "is over after 3 rounds." in recap and "🏆 **U11** wins with" in recap
    points = flow.game_points(
        await bot.repo.scored_pairings(game["id"]), await bot.repo.game_user_ids(game["id"])
    )
    assert dict(points)[11] >= 600
    assert f"🏆 **U11** wins with {points[0][1]} points!" in recap


async def test_last_round_then_recap(bot):
    game = await lobby(bot)
    card = await add_card(bot, game)
    await flow.begin_game(bot, game, 1)
    assert card.embed.title == "📰 Friday: in progress"
    await play_round(bot, game)
    assert await status(bot, game) == "finished"
    result, recap, standings = bot.channel.texts[-3:]
    assert "the results" in result and "is over after 1 round." in recap
    assert '📰 Top headline: "headline by w11" by **U11**, quoting **U' in recap
    assert "(2 votes)" in recap
    assert recap.endswith("All-time standings: `/gaminator headlines leaderboard`")
    assert "## 🏆 Headlines: all-time standings" in standings
    assert card.embed.title == "🏁 Friday: finished" and card.view is None
    assert card_names(card) == ["U11", "U12", "U13"]
    assert all(m.view is None for m in bot.channel.messages)


async def test_end_during_answering_abandons(bot):
    game = await running(bot)
    rnd = await current(bot, game)
    await flow.submit_answer(bot, rnd["id"], 11, "an answer")
    assert await flow.finish(bot, game, note="ended by U11")
    rnd = await bot.repo.get_round(rnd["id"])
    assert rnd["outcome"] == "abandoned"
    answer_message = message_of(bot, rnd["answer_message_id"])
    assert answer_message.view is None and answer_message.content.endswith("\nThe game ended.")
    (recap,) = recaps(bot)
    assert "is over after 0 rounds (ended by U11)." in recap and "Nobody scored." in recap
    assert "not voted on" not in recap
    assert await flow.submit_answer(bot, rnd["id"], 12, "late") == "That game is over."


async def test_end_during_writing_abandons(bot):
    game = await running(bot)
    rnd = await play_to_writing(bot, game)
    await flow.submit_headline(bot, rnd["id"], 11, "a headline")
    assert await flow.finish(bot, game, note="ended by U11")
    rnd = await bot.repo.get_round(rnd["id"])
    assert rnd["outcome"] == "abandoned"
    write_message = message_of(bot, rnd["write_message_id"])
    assert write_message.view is None and write_message.content.endswith("\nThe game ended.")
    assert message_of(bot, rnd["answer_message_id"]).view is None
    assert "is over after 0 rounds (ended by U11)." in recaps(bot)[0]
    assert "not voted on" not in recaps(bot)[0] and pairing_posts(bot) == []
    assert await flow.submit_headline(bot, rnd["id"], 12, "late") == "That game is over."


def interrupt_reveal(monkeypatch, action):
    """Run ``action`` during the pause before the second headline of a reveal."""
    pauses = []

    async def fake_sleep(seconds):
        pauses.append(seconds)
        if len(pauses) == 2:
            await action()

    monkeypatch.setattr(flow.asyncio, "sleep", fake_sleep)


async def test_end_during_reveal_stops_reveal(bot, monkeypatch):
    game = await running(bot)

    async def end():
        assert await flow.finish(bot, game, note="ended by U11")

    interrupt_reveal(monkeypatch, end)
    rnd = await play_to_voting(bot, game)
    assert rnd["outcome"] == "abandoned" and rnd["vote_deadline_at"] is None
    assert len(pairing_posts(bot)) == 1 and vote_posts(bot) == [] and results(bot) == []
    (recap,) = recaps(bot)
    assert "is over after 0 rounds (ended by U11)." in recap
    assert UNVOTED in recap and "Nobody scored." in recap
    assert bot.channel.texts[-1] == recap
    assert await bot.repo.scored_pairings(game["id"]) == []
    assert all(m.view is None for m in bot.channel.messages)
    assert message_of(bot, rnd["write_message_id"]).content.endswith("\nThe game ended.")


async def test_leave_below_minimum_during_reveal(bot, monkeypatch):
    game = await running(bot)

    async def leave():
        await flow.leave_game(bot, game, 13)

    interrupt_reveal(monkeypatch, leave)
    rnd = await play_to_voting(bot, game)
    assert rnd["outcome"] == "abandoned" and await status(bot, game) == "finished"
    assert len(pairing_posts(bot)) == 1 and vote_posts(bot) == []
    (recap,) = recaps(bot)
    assert "is over after 0 rounds (too few players left)." in recap and UNVOTED in recap
    assert all(m.view is None for m in bot.channel.messages)


async def test_end_during_voting_posts_result_before_recap(bot):
    game = await running(bot)
    rnd = await play_to_voting(bot, game)
    await vote(bot, rnd, 12, 11)
    assert await flow.finish(bot, game, note="ended by U11")
    rnd = await bot.repo.get_round(rnd["id"])
    assert rnd["outcome"] == "voted"
    result, recap = bot.channel.texts[-3:-1]
    assert "the results" in result and "(1 vote): headline by **U11** +100" in result
    assert "Totals" not in result and "starts now" not in result
    assert "is over after 1 round (ended by U11)." in recap
    assert "🏆 **U11** wins with" in recap and "not voted on" not in recap
    assert message_of(bot, rnd["vote_message_id"]).view is None
    assert "all-time standings" in bot.channel.texts[-1]


@pytest.mark.parametrize(
    "marker, column",
    [
        ("answer your question", "answer_message_id"),
        ("write the headlines", "write_message_id"),
        ("vote for the best headline", "vote_message_id"),
    ],
)
async def test_end_racing_step_post_closes_message(bot, marker, column):
    game = await lobby(bot)

    async def on_send(message):
        if marker in message.sent:
            bot.channel.on_send = None
            assert message.view is not None
            assert await flow.finish(bot, game, note="ended by U11")

    bot.channel.on_send = on_send
    await flow.begin_game(bot, game, 2)
    rnd = await current(bot, game)
    if await status(bot, game) == "active":
        rnd = await play_to_writing(bot, game)
    if await status(bot, game) == "active":
        rnd = await play_to_voting(bot, game)
    assert await status(bot, game) == "finished" and bot.channel.on_send is None
    rnd = await bot.repo.get_round(rnd["id"])
    (message,) = with_text(bot, marker)
    assert rnd[column] == message.id
    assert message.view is None and message.edits[-1]["view"] is None
    assert all(m.view is None for m in bot.channel.messages)
    assert len(recaps(bot)) == 1


async def test_double_finish_one_recap(bot):
    game = await running(bot)
    rnd = await play_to_voting(bot, game)
    await vote(bot, rnd, 12, 11)
    done = await asyncio.gather(flow.finish(bot, game), flow.finish(bot, game))
    assert sorted(done) == [False, True]
    assert len(recaps(bot)) == 1 and len(results(bot)) == 1
    assert not await flow.finish(bot, game)
    assert len(recaps(bot)) == 1


async def test_post_retries_once(bot):
    game = await lobby(bot)
    bot.channel.fail_with = unavailable(1)
    await flow.begin_game(bot, game, 2)
    rnd = await current(bot, game)
    assert rnd["outcome"] is None and rnd["answer_message_id"] == bot.channel.messages[-1].id
    assert len(with_text(bot, "answer your question")) == 1


async def test_answer_post_fails_twice_abandons_round(bot):
    game = await lobby(bot)
    bot.channel.fail_with = unavailable(2)
    await flow.begin_game(bot, game, 2)
    (first,) = await rounds_of(bot, game)
    assert first["outcome"] == "abandoned" and first["answer_message_id"] is None
    assert bot.channel.messages == [] and await status(bot, game) == "active"
    assert await flow.resume_stalled(bot, grace=30) == 0
    assert await flow.resume_stalled(bot, grace=0) == 1
    rnd = await current(bot, game)
    assert rnd["number"] == 1 and rnd["id"] != first["id"]
    assert rnd["answer_message_id"] == bot.channel.messages[-1].id


async def test_three_abandoned_rounds_end_game(bot):
    game = await lobby(bot)
    bot.channel.fail_with = unavailable(6)
    await flow.begin_game(bot, game, 2)
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert await status(bot, game) == "active" and bot.channel.fail_with == []
    assert await bot.repo.abandoned_streak(game["id"]) == 3
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert await status(bot, game) == "finished"
    assert [r["outcome"] for r in await rounds_of(bot, game)] == ["abandoned"] * 3
    (recap,) = recaps(bot)
    assert "is over after 0 rounds (I couldn't post in the game channel)." in recap
    assert with_text(bot, "answer your question") == []


async def test_abandoned_round_not_idle(bot):
    game = await lobby(bot)
    bot.channel.fail_with = unavailable(2)
    await flow.begin_game(bot, game, 2)
    assert await flow.idle_note(bot, game) is None
    assert await bot.repo.rounds_played(game["id"]) == 0
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert await status(bot, game) == "active"
    await play_round(bot, game)
    await play_round(bot, game)
    assert "is over after 2 rounds." in recaps(bot)[0]


async def test_channel_gone_ends_game(bot):
    game = await lobby(bot)
    bot.channel.fail_with = [http_error(discord.Forbidden, 403)]
    await flow.begin_game(bot, game, 2)
    assert await status(bot, game) == "finished"
    assert "(I couldn't post in the game channel)" in recaps(bot)[0]
    assert [r["outcome"] for r in await rounds_of(bot, game)] == ["abandoned"]


async def test_channel_gone_during_later_steps_ends_game(bot):
    game = await running(bot)
    rnd = await current(bot, game)
    for uid in (11, 12):
        await flow.submit_answer(bot, rnd["id"], uid, "an answer")
    bot.channel.fail_with = [http_error(discord.NotFound, 404)]
    await flow.submit_answer(bot, rnd["id"], 13, "an answer")
    await flow.close_step_if_done(bot, rnd["id"])
    assert await status(bot, game) == "finished"
    assert "(I couldn't post in the game channel)" in recaps(bot)[0]
    assert all(m.view is None for m in bot.channel.messages)


async def test_vote_post_fails_abandons_round(bot):
    game = await running(bot)

    async def on_send(message):
        if "**3 of 3**" in message.sent:
            bot.channel.fail_with = unavailable(2)

    bot.channel.on_send = on_send
    rnd = await play_to_voting(bot, game)
    assert rnd["outcome"] == "abandoned" and rnd["vote_message_id"] is None
    assert vote_posts(bot) == [] and len(pairing_posts(bot)) == 3
    assert await status(bot, game) == "active"
    assert all(m.view is None for m in bot.channel.messages)
    assert await flow.resume_stalled(bot, grace=0) == 1
    again = await current(bot, game)
    assert again["number"] == 1 and again["id"] != rnd["id"]


async def test_vote_post_channel_gone_ends_game(bot):
    game = await running(bot)

    async def on_send(message):
        if "**3 of 3**" in message.sent:
            bot.channel.fail_with = [http_error(discord.Forbidden, 403)]

    bot.channel.on_send = on_send
    rnd = await play_to_voting(bot, game)
    assert await status(bot, game) == "finished" and vote_posts(bot) == []
    assert rnd["outcome"] == "voted" and rnd["vote_message_id"] is None
    (recap,) = recaps(bot)
    assert "(I couldn't post in the game channel)" in recap and "Nobody scored." in recap


async def test_close_overdue_each_step(bot):
    game = await running(bot, FOUR)
    rnd = await current(bot, game)
    assert await flow.close_overdue(bot) == 0
    for uid in (11, 12, 13):
        await flow.submit_answer(bot, rnd["id"], uid, f"answer of a{uid}")
    await expire(bot, "answer_deadline_at")
    assert await flow.close_overdue(bot) == 1
    rnd = await bot.repo.get_round(rnd["id"])
    assert rnd["answer_closed_at"] and rnd["write_message_id"]
    assert await flow.close_overdue(bot) == 0
    assert sorted(await pairings_of(bot, rnd)) == [11, 12, 13]
    for uid in (11, 12):
        await flow.submit_headline(bot, rnd["id"], uid, f"headline by w{uid}")
    await expire(bot, "write_deadline_at")
    assert await flow.close_overdue(bot) == 1
    rnd = await bot.repo.get_round(rnd["id"])
    assert rnd["write_closed_at"] and rnd["vote_message_id"] and rnd["eligible_voters"] == 4
    assert await flow.close_overdue(bot) == 0
    await vote(bot, rnd, 14, 11)
    await expire(bot, "vote_deadline_at")
    assert await flow.close_overdue(bot) == 1
    assert (await bot.repo.get_round(rnd["id"]))["outcome"] == "voted"
    assert "(1 vote): headline by **U11** +100" in results(bot)[0]
    assert await flow.close_overdue(bot) == 0


@pytest.mark.parametrize("step", ["answer", "write", "vote"])
async def test_overdue_without_message_is_abandoned(bot, step):
    game = await running(bot)
    if step != "answer":
        await play_to_writing(bot, game)
    if step == "vote":
        await play_to_voting(bot, game)
    rnd = await current(bot, game)
    async with bot.repo.db.transaction() as conn:
        await conn.execute(f"UPDATE headlines_rounds SET {step}_message_id = NULL")
    await expire(bot, f"{step}_deadline_at")
    assert await flow.close_overdue(bot) == 1
    assert (await bot.repo.get_round(rnd["id"]))["outcome"] == "abandoned"
    assert await flow.idle_note(bot, game) is None
    assert await status(bot, game) == "active" and recaps(bot) == []
    again = await current(bot, game)
    assert again["id"] != rnd["id"] and again["number"] == 1 and again["answer_message_id"]
    assert results(bot) == []


async def test_resume_stalled_after_crash_between_rounds(bot, db, monkeypatch):
    game = await running(bot)
    rnd = await play_to_voting(bot, game)

    async def crash(*args):
        return None

    with monkeypatch.context() as patch:
        patch.setattr(flow, "advance", crash)
        await vote_all(bot, game, rnd)
    assert (await current(bot, game))["id"] == rnd["id"]
    assert (await current(bot, game))["outcome"] == "voted"
    restarted = make_bot(db)
    assert await flow.resume_stalled(restarted, grace=30) == 0
    assert await flow.resume_stalled(restarted, grace=0) == 1
    assert (await current(restarted, game))["number"] == 2
    (message,) = restarted.channel.messages
    assert "Round 2 of 2: answer your question" in message.sent
    assert await flow.resume_stalled(restarted, grace=0) == 0


async def test_late_joiner_in_from_next_round(bot):
    game = await running(bot)
    rnd = await current(bot, game)
    answer_message = message_of(bot, rnd["answer_message_id"])
    assert await flow.join_game(bot, game, 14) == "You're in from the next round."
    assert bot.channel.texts[-1] == "_ _\n🙋 **U14** joined and is in from the next round."
    assert "0/3 answered." in answer_message.content
    assert await flow.submit_answer(bot, rnd["id"], 14, "me too") == NEXT_ROUND
    rnd = await play_to_writing(bot, game, {uid: f"answer of a{uid}" for uid in (11, 12, 13)})
    assert rnd["write_message_id"]
    assert await flow.submit_headline(bot, rnd["id"], 14, "me too") == NEXT_ROUND
    rnd = await play_to_voting(bot, game)
    assert rnd["eligible_voters"] == 3
    target = (await pairings_of(bot, rnd))[11]
    assert await flow.submit_vote(bot, rnd["id"], 14, str(target["id"])) == NEXT_ROUND
    await vote_all(bot, game, rnd, voters=(11, 12, 13))
    second = await current(bot, game)
    assert second["number"] == 2
    assert (await bot.repo.round_context(second["id"], 14))["answer_id"] is not None
    assert "0/4 answered." in bot.channel.texts[-1]
    reply = await flow.submit_answer(bot, second["id"], 14, "now I can")
    assert reply.startswith("Saved.")


async def test_rejoin_same_round_continues(bot):
    game = await running(bot, FOUR)
    rnd = await current(bot, game)
    answer_message = message_of(bot, rnd["answer_message_id"])
    await flow.submit_answer(bot, rnd["id"], 14, "before leaving")
    reply = await flow.leave_game(bot, game, 14)
    assert reply == "You've left. Your answers, headlines and votes so far still count."
    assert "0/3 answered." in answer_message.content
    assert await flow.join_game(bot, game, 14) == "You're back in."
    assert bot.channel.texts[-1] == "_ _\n🙋 **U14** joined."
    assert "1/4 answered." in answer_message.content and answer_message.view is not None
    reply = await flow.submit_answer(bot, rnd["id"], 14, "after coming back")
    assert reply.startswith('Saved. Your answer: "after coming back".')
    with pytest.raises(GameError, match="already in"):
        await flow.join_game(bot, game, 14)


async def test_join_between_rounds(bot, monkeypatch):
    game = await running(bot)
    rnd = await play_to_voting(bot, game)

    async def crash(*args):
        return None

    with monkeypatch.context() as patch:
        patch.setattr(flow, "advance", crash)
        await vote_all(bot, game, rnd)
    assert await flow.join_game(bot, game, 14) == "You're in."
    assert bot.channel.texts[-1] == "_ _\n🙋 **U14** joined."
    assert await flow.resume_stalled(bot, grace=0) == 1
    second = await current(bot, game)
    assert (await bot.repo.round_context(second["id"], 14))["answer_id"] is not None


async def test_join_refusals(bot):
    game = await lobby(bot)
    assert await flow.join_game(bot, game, 14) == "You're in."
    assert "joined Headlines game **Friday**. 4 players signed up." in bot.channel.texts[-1]
    for uid in range(100, 108):
        await bot.repo.join(game["id"], uid)
    with pytest.raises(GameError, match="The game is full"):
        await flow.join_game(bot, game, 200)
    await bot.repo.finish_game(game["id"])
    with pytest.raises(GameError, match="That game is over."):
        await flow.join_game(bot, await bot.repo.get_game(game["id"]), 201)
    with pytest.raises(GameError, match="That game is over."):
        await flow.leave_game(bot, await bot.repo.get_game(game["id"]), 11)


async def test_leave_during_answering_closes_when_rest_answered(bot):
    game = await running(bot, FOUR)
    rnd = await current(bot, game)
    for uid in (11, 12, 13):
        await flow.submit_answer(bot, rnd["id"], uid, f"answer of a{uid}")
        await flow.close_step_if_done(bot, rnd["id"])
    assert (await current(bot, game))["answer_closed_at"] is None
    await flow.leave_game(bot, game, 14)
    rnd = await current(bot, game)
    assert rnd["answer_closed_at"] and rnd["write_message_id"]
    assert sorted(await pairings_of(bot, rnd)) == [11, 12, 13]
    assert "_ _\n🚪 **U14** left. 3 players remain." in bot.channel.texts
    with pytest.raises(GameError, match="You're not in this game."):
        await flow.leave_game(bot, game, 14)
    with pytest.raises(GameError, match="You're not in this game."):
        await flow.leave_game(bot, game, 99)


async def test_leavers_answer_still_handed_out(bot):
    game = await running(bot, FOUR, rounds=1)
    rnd = await current(bot, game)
    await flow.submit_answer(bot, rnd["id"], 14, "the leaver's words")
    await flow.leave_game(bot, game, 14)
    for uid in (11, 12):
        await flow.submit_answer(bot, rnd["id"], uid, f"answer of a{uid}")
        await flow.close_step_if_done(bot, rnd["id"])
    await expire(bot, "answer_deadline_at")
    assert await flow.close_overdue(bot) == 1
    pairs = await pairings_of(bot, rnd)
    assert {uid: row["quoted_user_id"] for uid, row in pairs.items()} == {11: 12, 12: 11, 13: 14}
    rnd = await play_to_voting(bot, game)
    assert any('> "the leaver\'s words" (U14)' in post for post in pairing_posts(bot))
    await vote(bot, rnd, 11, 13)
    await vote(bot, rnd, 12, 13)
    await vote(bot, rnd, 13, 11)
    assert "(2 votes): headline by **U13** +200, quote from **U14** +50" in results(bot)[0]
    (recap,) = recaps(bot)
    assert "🏆 **U13** wins with 200 points!" in recap and "U14 50" in recap


async def test_leave_during_voting_closes(bot):
    game = await running(bot, (11, 12, 13, 14, 15))
    rnd = await play_to_voting(bot, game)
    vote_message = message_of(bot, rnd["vote_message_id"])
    await vote_all(bot, game, rnd, voters=(11, 12, 13))
    await flow.leave_game(bot, game, 14)
    assert "3/4 voted." in vote_message.content and vote_message.view is not None
    assert (await bot.repo.get_round(rnd["id"]))["closed_at"] is None
    await flow.leave_game(bot, game, 15)
    assert (await bot.repo.get_round(rnd["id"]))["outcome"] == "voted"
    assert vote_message.view is None and len(results(bot)) == 1
    assert "(2 votes): headline by **U11** +200" in results(bot)[0]
    assert await status(bot, game) == "active" and (await current(bot, game))["number"] == 2


async def test_leavers_vote_still_counts(bot):
    game = await running(bot, FOUR, rounds=1)
    rnd = await play_to_voting(bot, game)
    await vote(bot, rnd, 14, 12)
    await flow.leave_game(bot, game, 14)
    await vote(bot, rnd, 11, 12)
    await vote(bot, rnd, 13, 12)
    await vote(bot, rnd, 12, 11)
    (result,) = results(bot)
    assert "(3 votes): headline by **U12** +300" in result
    assert "🏆 **U12** wins with" in recaps(bot)[0]
    assert "U14" in recaps(bot)[0]


async def test_leave_ends_game(bot):
    game = await running(bot)
    rnd = await current(bot, game)
    await flow.leave_game(bot, game, 13)
    assert await status(bot, game) == "finished"
    assert "is over after 0 rounds (too few players left)." in recaps(bot)[0]
    assert message_of(bot, rnd["answer_message_id"]).view is None


async def test_host_handover(bot):
    game = await running(bot, FOUR)
    await flow.leave_game(bot, game, 11)
    assert (await bot.repo.get_game(game["id"]))["host_id"] == 12
    assert "_ _\n👑 **U12** is now the host." in bot.channel.texts
    await flow.leave_game(bot, game, 13)
    assert (await bot.repo.get_game(game["id"]))["host_id"] == 12
    assert await status(bot, game) == "finished"

    open_game = await bot.repo.create_game(2, "Later", 21, CHANNEL_ID)
    for uid in (21, 22):
        await bot.repo.join(open_game["id"], uid)
    assert await flow.leave_game(bot, open_game, 21) == "You've left the game."
    assert (await bot.repo.get_game(open_game["id"]))["host_id"] == 22
    await flow.leave_game(bot, open_game, 22)
    assert await status(bot, open_game) == "finished"
    assert bot.channel.texts[-1] == (
        "_ _\n🚫 Headlines game **Later** was cancelled (everyone left)."
    )


async def test_require_host_or_admin(bot):
    game = await lobby(bot)
    await require_host_or_admin(interaction_for(bot, 11), game)
    with pytest.raises(NotGameAdmin):
        await require_host_or_admin(interaction_for(bot, 12), game)
    await require_host_or_admin(interaction_for(bot, 12, manage_guild=True), game)


async def test_no_pings(bot):
    nasty = "@everyone <@1> hello"
    game = await lobby(bot)
    card = await add_card(bot, game)
    await flow.begin_game(bot, game, 1)
    await flow.join_game(bot, game, 14)
    await flow.leave_game(bot, game, 14)
    rnd = await play_to_writing(bot, game, dict.fromkeys((11, 12, 13), nasty))
    rnd = await play_to_voting(bot, game, dict.fromkeys((11, 12, 13), nasty))
    await vote_all(bot, game, rnd)
    assert await status(bot, game) == "finished"
    assert nasty in "\n".join(pairing_posts(bot)) and nasty in results(bot)[0]
    edits = 0
    for message in bot.channel.messages:
        if message is not card:
            assert message.kwargs["allowed_mentions"] is NO_MENTIONS, message.sent
        for edit in message.edits:
            assert edit["allowed_mentions"] is NO_MENTIONS, edit
            edits += 1
    assert edits >= 8 and len(card.edits) >= 2
    assert NO_MENTIONS.everyone is False and NO_MENTIONS.users is False
    assert NO_MENTIONS.roles is False


def secrets_of(pairs):
    """Every answer and headline of a round, which a status reply must never show."""
    found = []
    for row in pairs.values():
        found += [row["answer"], row["headline"]]
    return [text for text in found if text]


async def status_of(bot, game, user_id):
    text = await flow.status_reply(bot, game, user_id)
    assert not DEALT.search(text)
    return STAMPS.sub("T", text).split("\n")


async def test_status_open_lobby(bot):
    game = await lobby(bot)
    assert await status_of(bot, game, 11) == [
        "Headlines game **Friday**, hosted by **U11**.",
        "**Players (3):** U11, U12, U13",
        "Sign-ups are open. It needs at least 3 to start.",
    ]


async def test_status_answering(bot):
    game = await running(bot)
    rnd = await current(bot, game)
    await flow.join_game(bot, game, 14)
    await flow.submit_answer(bot, rnd["id"], 11, "my secret answer")
    head = [
        "Headlines game **Friday**, hosted by **U11**.",
        "**Players (4):** U11, U12, U13, U14",
        "Round 1 of 2: answering. 1/3 answered. Closes T.",
    ]
    assert await status_of(bot, game, 11) == head
    assert await status_of(bot, game, 12) == [*head, OWES_ANSWER]
    assert await status_of(bot, game, 14) == head
    assert await status_of(bot, game, 99) == head


async def test_status_writing(bot):
    game = await running(bot)
    await flow.join_game(bot, game, 14)
    rnd = await play_to_writing(bot, game, {11: "answer of a11", 12: "answer of a12"})
    await flow.submit_headline(bot, rnd["id"], 11, "my secret headline")
    step = "Round 1 of 2: writing headlines. 1/2 headlines in. Closes T."
    assert (await status_of(bot, game, 11))[2:] == [step]
    assert (await status_of(bot, game, 12))[2:] == [step, OWES_HEADLINE]
    assert (await status_of(bot, game, 13))[2:] == [step]
    assert (await status_of(bot, game, 14))[2:] == [step]
    assert (await status_of(bot, game, 99))[2:] == [step]
    for uid in (11, 12, 13, 14, 99):
        text = await flow.status_reply(bot, game, uid)
        assert "secret" not in text and "answer of" not in text


async def test_status_voting(bot):
    game = await running(bot)
    await flow.join_game(bot, game, 14)
    rnd = await play_to_voting(bot, game)
    await vote(bot, rnd, 11, 12)
    step = "Round 1 of 2: voting. 1/3 voted. Closes T."
    assert (await status_of(bot, game, 11))[2:] == [step]
    assert (await status_of(bot, game, 12))[2:] == [step, OWES_VOTE]
    assert (await status_of(bot, game, 14))[2:] == [step]
    assert (await status_of(bot, game, 99))[2:] == [step]
    secrets = secrets_of(await pairings_of(bot, rnd))
    assert len(secrets) == 6
    for uid in (11, 12, 14, 99):
        text = await flow.status_reply(bot, game, uid)
        assert not any(secret in text for secret in secrets)
        assert "headline **" not in text
    await flow.leave_game(bot, game, 12)
    assert (await status_of(bot, game, 12))[2:] == ["Round 1 of 2: voting. 1/2 voted. Closes T."]


async def test_status_reveal_and_between_rounds(bot, monkeypatch):
    game = await running(bot)
    with monkeypatch.context() as patch:
        stop_reveal_at(patch, 2)
        rnd = await play_to_voting(bot, game)
    assert (await status_of(bot, game, 11))[2:] == [
        "Round 1 of 2: the headlines are being read out."
    ]
    assert await flow.resume_stalled(bot, grace=0) == 1

    async def crash(*args):
        return None

    with monkeypatch.context() as patch:
        patch.setattr(flow, "advance", crash)
        await vote_all(bot, game, rnd)
    assert (await status_of(bot, game, 11))[2:] == ["The next round is on its way."]
    assert NOT_PLAYING not in await flow.status_reply(bot, game, 99)


async def play_finished_game(bot, guild_id=1, users=(11, 12, 13)):
    game = await bot.repo.create_game(guild_id, "Friday", users[0], CHANNEL_ID)
    for uid in users:
        await bot.repo.join(game["id"], uid)
    await flow.begin_game(bot, game, 1)
    await play_round(bot, game)
    assert await status(bot, game) == "finished"
    return game


async def test_leaderboard_reply(bot):
    assert await flow.leaderboard_reply(bot, 1, 11) == (
        "No finished Headlines games yet. Start one with `/gaminator headlines create`."
    )
    assert await flow.leaderboard_reply(bot, 1, 11, 12) == (
        "**U12** hasn't played a finished game yet."
    )
    game = await play_finished_game(bot)
    points = dict(
        flow.game_points(
            await bot.repo.scored_pairings(game["id"]), await bot.repo.game_user_ids(game["id"])
        )
    )
    text = await flow.leaderboard_reply(bot, 1, 13)
    lines = text.split("\n")
    assert lines[:2] == ["_ _", "## 🏆 Headlines: all-time standings"]
    assert lines[2] == f"`1.` **U11**: 1 game won, {points[11]} points, 1 played"
    assert len(lines) == 5 and all(f"**U{uid}**" in text for uid in (11, 12, 13))
    assert await flow.leaderboard_reply(bot, 1, 13, 11) == (
        f"**U11**: 1 game won, {points[11]} points, 1 played"
    )
    assert await flow.leaderboard_reply(bot, 1, 13, 99) == (
        "**U99** hasn't played a finished game yet."
    )
    assert "No finished Headlines games yet." in await flow.leaderboard_reply(bot, 2, 13)


async def test_leaderboard_ignores_running_game(bot):
    game = await running(bot)
    await play_round(bot, game)
    assert await status(bot, game) == "active"
    assert "No finished Headlines games yet." in await flow.leaderboard_reply(bot, 1, 11)
    await play_round(bot, game)
    assert "**U11**: 1 game won" in await flow.leaderboard_reply(bot, 1, 11)


async def test_recap_then_standings(bot, monkeypatch, caplog):
    game = await play_finished_game(bot)
    recap, standings = bot.channel.texts[-2:]
    assert "is over after 1 round." in recap
    assert standings.startswith(
        "_ _\n## 🏆 Headlines: all-time standings\n`1.` **U11**: 1 game won"
    )
    assert bot.channel.messages[-1].kwargs["allowed_mentions"] is NO_MENTIONS
    points = dict(
        flow.game_points(
            await bot.repo.scored_pairings(game["id"]), await bot.repo.game_user_ids(game["id"])
        )
    )
    assert f"wins with {points[11]} points!" in recap
    assert f"{points[11]} points, 1 played" in standings

    async def broken(guild_id):
        raise RuntimeError("no standings today")

    monkeypatch.setattr(bot.repo, "finished_pairings", broken)
    second = await bot.repo.create_game(1, "Saturday", 11, CHANNEL_ID)
    for uid in (11, 12, 13):
        await bot.repo.join(second["id"], uid)
    card = await add_card(bot, second)
    await flow.begin_game(bot, second, 1)
    with caplog.at_level(logging.ERROR):
        await play_round(bot, second)
    assert await status(bot, second) == "finished"
    assert "is over after 1 round." in bot.channel.texts[-1]
    assert any("standings after the game failed" in r.getMessage() for r in caplog.records)
    assert card.embed.title == "🏁 Saturday: finished" and card.view is None


async def test_announce_copy_when_channel_differs(bot):
    other = SimpleNamespace(id=777, sent=[])

    async def send(text, **kwargs):
        other.sent.append(text)

    other.send = send
    known = bot.get_channel
    bot.get_channel = lambda channel_id: other if channel_id == 777 else known(channel_id)
    await bot.guild_settings.set_announce_channel(1, 777)
    await play_finished_game(bot)
    assert len(other.sent) == 1 and "is over after 1 round." in other.sent[0]
    assert other.sent[0] == recaps(bot)[0]

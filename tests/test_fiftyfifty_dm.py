"""Answering by DM: the Answer by DM button, and DM replies read as a player's two options."""

import asyncio
import logging
from types import SimpleNamespace

import pytest
from fiftyfifty_fakes import (
    CHANNEL_ID,
    dm_message,
    interaction_for,
    make_bot,
    reply_of,
    submit_for,
)

from gaminator.games.fiftyfifty.dm import handle_direct_message, handle_dm_click
from gaminator.games.fiftyfifty.events import FiftyFiftyEvents
from gaminator.games.fiftyfifty.services import game as flow
from gaminator.games.fiftyfifty.services.game import DM_FORMAT, dm_question_text, written_so_far
from gaminator.games.fiftyfifty.services.rules import NOT_IN_ROUND, WRITING_CLOSED
from gaminator.games.fiftyfifty.write import blank_custom_id, handle_question_submit

SENT = "Check your DMs."
FAILED = "I couldn't DM you (your DMs may be closed). Use **Write** instead."
CLOSED = "Writing has closed for that round."


@pytest.fixture
async def bot(db):
    return make_bot(db)


async def running(bot, users=(11, 12, 13), rounds=1, guild_id=1, name="Friday"):
    game = await bot.repo.create_game(guild_id, name, users[0], CHANNEL_ID)
    for uid in users:
        await bot.repo.join(game["id"], uid)
    await flow.begin_game(bot, game, rounds)
    return await bot.repo.get_game(game["id"]), await bot.repo.get_current_round(game["id"])


async def mine(bot, rnd, uid):
    return await bot.repo.write_context(rnd["id"], uid)


async def question_dm(bot, rnd, uid, place=None):
    ctx = await mine(bot, rnd, uid)
    return dm_question_text(ctx["topic"], written_so_far(ctx), place)


async def options(bot, rnd, uid):
    """The user's two options, or None while they haven't written."""
    row = await mine(bot, rnd, uid)
    return (row["option_a"], row["option_b"]) if row["answered_at"] else None


async def send(bot, uid, content):
    return await handle_direct_message(bot, dm_message(bot, uid, content))


async def click(bot, uid, round_id):
    interaction = interaction_for(bot, uid)
    await handle_dm_click(interaction, round_id)
    return interaction


async def stamp(bot, rnd, uid, value):
    ctx = await mine(bot, rnd, uid)
    async with bot.repo.db.transaction() as conn:
        await conn.execute(
            "UPDATE fiftyfifty_questions SET dm_prompted_at = ? WHERE id = ?",
            (value, ctx["question_id"]),
        )


async def test_dm_button_sends_question(bot):
    _, rnd = await running(bot)
    expected = await question_dm(bot, rnd, 11)
    pressed = await click(bot, 11, rnd["id"])
    pressed.response.defer.assert_awaited_once_with(ephemeral=True)
    assert reply_of(pressed) == SENT
    assert pressed.followup.send.await_args.kwargs["ephemeral"] is True
    assert bot.dms[11] == [expected]
    assert "Reply here with both options" in expected and "Topic idea: **topic " in expected
    assert " for " not in expected.split("\n")[0]
    first = (await mine(bot, rnd, 11))["dm_prompted_at"]
    assert first
    await stamp(bot, rnd, 11, "2026-01-01T00:00:00+00:00")
    assert reply_of(await click(bot, 11, rnd["id"])) == SENT
    assert bot.dms[11] == [expected, expected]
    assert (await mine(bot, rnd, 11))["dm_prompted_at"] == "2026-01-01T00:00:00+00:00"
    await send(bot, 11, "cold soup | warm salad")
    await click(bot, 11, rnd["id"])
    assert "Your question so far: Would you rather cold soup or warm salad?" in bot.dms[11][-1]
    assert bot.dms[12] == []


async def test_dm_button_dms_closed(bot):
    _, rnd = await running(bot)
    bot.closed_dms.add(11)
    pressed = await click(bot, 11, rnd["id"])
    assert reply_of(pressed) == FAILED
    assert bot.dms[11] == []
    assert (await mine(bot, rnd, 11))["dm_prompted_at"] is None
    await stamp(bot, rnd, 11, "earlier")
    assert reply_of(await click(bot, 11, rnd["id"])) == FAILED
    assert (await mine(bot, rnd, 11))["dm_prompted_at"] == "earlier"


async def test_dm_button_refusals(bot):
    game, rnd = await running(bot, users=(11, 12, 13, 14))

    async def refused(user_id, round_id=rnd["id"]):
        pressed = await click(bot, user_id, round_id)
        pressed.response.defer.assert_not_awaited()
        assert pressed.response.send_message.await_args.kwargs["ephemeral"] is True
        assert bot.dms[user_id] == []
        return reply_of(pressed)

    assert "Only players in this game can write" in await refused(99)
    await flow.join_game(bot, game, 15)
    assert await refused(15) == NOT_IN_ROUND
    await flow.leave_game(bot, game, 14)
    assert await refused(14) == "You've left the game."
    assert await refused(11, round_id=9999) == "That game is over."
    for uid in (11, 12, 13):
        await flow.submit_question(bot, rnd["id"], uid, "x", "y")
    assert await refused(11) == "Writing for that round has closed."
    await flow.finish(bot, game)
    assert await refused(11) == "That game is over."


async def test_round_view_has_both_buttons(bot):
    _, rnd = await running(bot)
    round_message = bot.channel.messages[0]

    def buttons():
        return [(c.item.label, c.item.custom_id) for c in round_message.view.children]

    both = [
        ("Write", f"fiftyfifty:write:{rnd['id']}"),
        ("Answer by DM", f"fiftyfifty:dm:{rnd['id']}"),
    ]
    assert buttons() == both
    await flow.submit_question(bot, rnd["id"], 11, "soup", "salad")
    assert "1/3 written." in round_message.content and buttons() == both
    assert round_message.view.children[1].item.style.name == "secondary"


async def test_first_dm_gets_question(bot):
    _, rnd = await running(bot)
    expected = await question_dm(bot, rnd, 11)
    assert await send(bot, 11, "hello?")
    assert bot.dms[11] == [expected]
    after = await mine(bot, rnd, 11)
    assert after["answered_at"] is None and after["dm_prompted_at"]
    assert "0/3 written." in bot.channel.messages[0].content


async def test_first_dm_reply_fails_clears_flag(bot):
    _, rnd = await running(bot)
    bot.closed_dms.add(11)
    assert await send(bot, 11, "hello?")
    assert (await mine(bot, rnd, 11))["dm_prompted_at"] is None
    bot.closed_dms.clear()
    await send(bot, 11, "hello again")
    assert bot.dms[11] == [await question_dm(bot, rnd, 11)]
    assert await options(bot, rnd, 11) is None


async def test_second_dm_sets_question(bot):
    _, rnd = await running(bot)
    await send(bot, 11, "hello?")
    assert await send(bot, 11, "  cold   soup | warm salad ")
    assert bot.dms[11][-1].split("\n")[:2] == [
        "Saved. Your question now reads:",
        "Would you rather cold soup or warm salad?",
    ]
    assert await options(bot, rnd, 11) == ("cold soup", "warm salad")
    assert "1/3 written." in bot.channel.messages[0].content
    await send(bot, 11, "Would you rather eat *bugs*\nor eat dirt?")
    assert await options(bot, rnd, 11) == ("eat *bugs*", "eat dirt")
    assert "Would you rather eat \\*bugs\\* or eat dirt?" in bot.dms[11][-1]
    assert len(bot.dms[11]) == 3 and "1/3 written." in bot.channel.messages[0].content


async def test_dm_without_two_options_explains_the_format(bot):
    _, rnd = await running(bot)
    await click(bot, 11, rnd["id"])
    for text in ("cold soup", "soup or salad", "a | b | c", "a\nb\nc", "soup |"):
        assert await send(bot, 11, text)
        assert bot.dms[11][-1] == DM_FORMAT, text
    assert "two lines" in DM_FORMAT and "`|`" in DM_FORMAT
    assert await options(bot, rnd, 11) is None
    assert (await mine(bot, rnd, 11))["dm_prompted_at"]
    assert "0/3 written." in bot.channel.messages[0].content
    await send(bot, 11, "soup | ?")
    assert bot.dms[11][-1] == "Both options are needed, and neither can be empty."
    await send(bot, 11, "soup | salad")
    assert await options(bot, rnd, 11) == ("soup", "salad")
    await send(bot, 11, "not two options")
    assert bot.dms[11][-1] == DM_FORMAT
    assert await options(bot, rnd, 11) == ("soup", "salad")


async def test_dm_after_button_sets_question(bot):
    _, rnd = await running(bot)
    await click(bot, 11, rnd["id"])
    await send(bot, 11, "cold soup\nwarm salad")
    assert await options(bot, rnd, 11) == ("cold soup", "warm salad")
    assert len(bot.dms[11]) == 2 and bot.dms[11][-1].startswith("Saved.")


async def test_dm_after_popup_resends_question(bot):
    _, rnd = await running(bot)
    await send(bot, 11, "hello?")
    await send(bot, 11, "by dm | or not")
    form = submit_for(bot, 11, blank_custom_id(rnd["id"]), "by form", "not that")
    await handle_question_submit(form)
    assert (await mine(bot, rnd, 11))["dm_prompted_at"] is None
    await send(bot, 11, "not | a question")
    assert bot.dms[11][-1] == await question_dm(bot, rnd, 11)
    assert "Your question so far: Would you rather by form or not that?" in bot.dms[11][-1]
    assert await options(bot, rnd, 11) == ("by form", "not that")
    await send(bot, 11, "final | answer")
    assert await options(bot, rnd, 11) == ("final", "answer")


async def test_popup_overwrites_dm_question(bot):
    _, rnd = await running(bot)
    await click(bot, 11, rnd["id"])
    await send(bot, 11, "by dm | or not")
    submit = submit_for(bot, 11, blank_custom_id(rnd["id"]), "by form", "or else")
    await handle_question_submit(submit)
    assert "Would you rather by form or else?" in reply_of(submit)
    assert await options(bot, rnd, 11) == ("by form", "else")
    assert "1/3 written." in bot.channel.messages[0].content


async def test_two_quick_dms_one_prompt(bot):
    _, rnd = await running(bot)
    expected = await question_dm(bot, rnd, 11)
    results = await asyncio.gather(send(bot, 11, "one | 1"), send(bot, 11, "two | 2"))
    assert results == [True, True]
    assert sorted(m == expected for m in bot.dms[11]) == [False, True]
    assert sum(m.startswith("Saved.") for m in bot.dms[11]) == 1
    assert await options(bot, rnd, 11) in (("one", "1"), ("two", "2"))


async def test_dm_too_long_is_cut(bot):
    _, rnd = await running(bot)
    await click(bot, 11, rnd["id"])
    await send(bot, 11, "x" * 90 + " | short")
    assert await options(bot, rnd, 11) == ("x" * 80, "short")
    assert "\n(Each option is cut to 80 characters.)\n" in bot.dms[11][-1]


async def test_dm_after_close(bot):
    _, rnd = await running(bot)
    await click(bot, 11, rnd["id"])
    await send(bot, 11, "in time | or not")
    await bot.repo.close_writing(rnd["id"], flow.RNG)
    assert await send(bot, 11, "too late | or not")
    assert await send(bot, 11, "still too late")
    assert bot.dms[11][-2:] == [CLOSED, CLOSED]
    assert await options(bot, rnd, 11) == ("in time", "not")
    assert not await send(bot, 12, "never asked by DM")
    assert bot.dms[12] == []
    async with bot.repo.db.transaction() as conn:
        await conn.execute(
            "UPDATE fiftyfifty_rounds SET write_closed_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", rnd["id"]),
        )
    count = len(bot.dms[11])
    assert not await send(bot, 11, "minutes later")
    assert len(bot.dms[11]) == count


async def test_dm_races_the_close(bot, monkeypatch):
    _, rnd = await running(bot)
    await click(bot, 11, rnd["id"])
    real = bot.repo.dm_candidates

    async def then_close(user_id):
        rows = await real(user_id)
        await bot.repo.close_writing(rnd["id"], flow.RNG)
        return rows

    monkeypatch.setattr(bot.repo, "dm_candidates", then_close)
    assert await send(bot, 11, "just | too late")
    assert bot.dms[11][-1] == WRITING_CLOSED
    assert await options(bot, rnd, 11) is None


async def test_dm_from_non_player_is_silent(bot):
    assert not await send(bot, 99, "hello bot")
    await running(bot)
    assert not await send(bot, 99, "hello bot")
    assert bot.dms[99] == []
    assert bot.channel.messages[0].edits == []


async def test_dm_ignores_bots_guild_messages_and_empty(bot):
    _, rnd = await running(bot)
    in_server = dm_message(bot, 11, "soup", guild=SimpleNamespace(id=1))
    assert not await handle_direct_message(bot, in_server)
    assert not await handle_direct_message(bot, dm_message(bot, 11, "soup", is_bot=True))
    assert not await send(bot, 11, "")
    assert not await send(bot, 11, "  \n ")
    after = await mine(bot, rnd, 11)
    assert after["dm_prompted_at"] is None and after["answered_at"] is None
    assert bot.dms[11] == []
    await FiftyFiftyEvents.on_message(SimpleNamespace(bot=bot), in_server)
    assert bot.dms[11] == []
    await FiftyFiftyEvents.on_message(SimpleNamespace(bot=bot), dm_message(bot, 11, "hello?"))
    assert len(bot.dms[11]) == 1


async def test_dm_two_games_two_guilds(bot):
    _, first = await running(bot)
    _, second = await running(bot, users=(11, 21, 22), guild_id=2, name="Other_game")
    there = "**Other\\_game** in **Server 2**"
    here = "**Friday** in **Server 1**"
    await send(bot, 11, "hello?")
    assert bot.dms[11] == [await question_dm(bot, second, 11, there)]
    assert bot.dms[11][0].startswith(f"✍️ Write your Fifty-Fifty would-you-rather for {there}\n")
    await send(bot, 11, "for the | other")
    assert bot.dms[11][-1].startswith(f"For {there}: Saved. Your question now reads:")
    assert await options(bot, second, 11) == ("for the", "other")
    assert await options(bot, first, 11) is None
    await stamp(bot, second, 11, "2026-01-01T00:00:00+00:00")
    await click(bot, 11, first["id"])
    assert " for " not in bot.dms[11][-1].split("\n")[0]
    await stamp(bot, first, 11, "2026-01-01T00:00:05+00:00")
    await send(bot, 11, "for | friday")
    assert bot.dms[11][-1].startswith(f"For {here}: Saved.")
    assert await options(bot, first, 11) == ("for", "friday")
    assert await options(bot, second, 11) == ("for the", "other")
    await send(bot, 11, "one line")
    assert bot.dms[11][-1] == f"For {here}: {DM_FORMAT}"
    bot.get_guild = lambda guild_id: None
    await send(bot, 11, "again | now")
    assert bot.dms[11][-1].startswith("For **Friday** in another server: Saved.")
    await bot.repo.close_writing(first["id"], flow.RNG)
    await send(bot, 11, "now the | other one")
    assert bot.dms[11][-1].startswith("For **Other\\_game** in another server: Saved.")
    assert await options(bot, second, 11) == ("now the", "other one")
    await send(bot, 21, "hello?")
    assert bot.dms[21] == [await question_dm(bot, second, 21)]


async def test_dm_flag_survives_restart(bot):
    _, rnd = await running(bot)
    await click(bot, 11, rnd["id"])
    restarted = make_bot(bot.repo.db)
    restarted.channel = bot.channel
    restarted.get_channel = bot.get_channel
    assert await send(restarted, 11, "after | the restart")
    assert restarted.dms[11][-1].startswith("Saved.")
    assert await options(bot, rnd, 11) == ("after", "the restart")
    assert "1/3 written." in bot.channel.messages[0].content


async def test_last_dm_question_starts_voting(bot):
    await running(bot)
    for uid in (11, 12, 13):
        await send(bot, uid, "hello?")
        await send(bot, uid, f"soup {uid} | salad {uid}")
    round_message, first = bot.channel.messages
    assert round_message.content.endswith("Writing is closed: 3 questions to vote on.")
    assert round_message.view is None
    assert first.content.startswith("_ _\n🗳️ **Question 1 of 3** (round 1)")


async def test_dm_unexpected_error_replies(bot, monkeypatch, caplog):
    _, rnd = await running(bot)
    await click(bot, 11, rnd["id"])
    ctx = await mine(bot, rnd, 11)

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(flow, "submit_question", boom)
    with caplog.at_level(logging.DEBUG):
        assert await send(bot, 11, "my secret | question")
    assert bot.dms[11][-1] == "Something went wrong; the logs have details."
    failures = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert failures == [f"fiftyfifty: DM question failed for question {ctx['question_id']}"]


async def test_dm_text_is_never_logged(bot, caplog):
    _, rnd = await running(bot)
    with caplog.at_level(logging.DEBUG, logger="gaminator"):
        await send(bot, 11, "zebra first message")
        await send(bot, 11, "zebra soup | zebra salad")
        await send(bot, 11, "zebra with no second option")
        await click(bot, 12, rnd["id"])
        await send(bot, 12, "zebra " * 20 + "\nzebra b")
        await send(bot, 13, "zebra one")
        await send(bot, 13, "zebra two | zebra three")
        await send(bot, 11, "zebra after close")
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("gaminator")]
    assert any("by DM" in line for line in ours)
    assert not any("zebra" in line for line in ours)

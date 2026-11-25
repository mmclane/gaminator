"""The Write button, the two-option form, and the listener that saves a submitted question."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest
from discord.ui.view import ViewStore
from fiftyfifty_fakes import CHANNEL_ID, click_for, interaction_for, make_bot, reply_of, submit_for

from gaminator.core.util import GameError
from gaminator.games.fiftyfifty import dm, lobby, vote, write
from gaminator.games.fiftyfifty.events import FiftyFiftyEvents
from gaminator.games.fiftyfifty.services import game as flow
from gaminator.games.fiftyfifty.services.prompts import topic_text
from gaminator.games.fiftyfifty.services.rules import NOT_IN_ROUND, WRITING_CLOSED
from gaminator.games.fiftyfifty.write import (
    MODAL_TITLE,
    OPTION_HELP,
    blank_custom_id,
    handle_question_submit,
    handle_write_click,
    options_from_submit,
    question_modal,
)
from gaminator.games.mostlikely import lobby as mostlikely_lobby
from gaminator.games.mostlikely import vote as mostlikely_vote

SAVED = "Saved. Your question now reads:"


@pytest.fixture
async def bot(db):
    return make_bot(db)


async def running(bot, users=(11, 12, 13), rounds=1):
    game = await bot.repo.create_game(1, "Friday", users[0], CHANNEL_ID)
    for uid in users:
        await bot.repo.join(game["id"], uid)
    await flow.begin_game(bot, game, rounds)
    return await bot.repo.get_game(game["id"]), await bot.repo.get_current_round(game["id"])


async def answer_of(bot, rnd, uid):
    """The user's two options, or None while they haven't written."""
    row = await bot.repo.write_context(rnd["id"], uid)
    return (row["option_a"], row["option_b"]) if row["answered_at"] else None


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="x"), "x")


def warnings_in(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_custom_ids_do_not_collide():
    ids = {
        "lobby": lobby.lobby_custom_id("join", 5),
        "write": write.write_custom_id(5),
        "dm": dm.dm_custom_id(5),
        "vote": vote.vote_custom_id(5, "a"),
        "guess": vote.guess_custom_id(5),
    }
    templates = {
        "lobby": lobby.TEMPLATE,
        "write": write.TEMPLATE,
        "dm": dm.TEMPLATE,
        "vote": vote.TEMPLATE,
        "guess": vote.GUESS_TEMPLATE,
    }
    for kind, custom_id in ids.items():
        for name, template in templates.items():
            assert (template.fullmatch(custom_id) is not None) == (kind == name), (kind, name)
        assert mostlikely_lobby.TEMPLATE.fullmatch(custom_id) is None
        assert mostlikely_vote.TEMPLATE.fullmatch(custom_id) is None
        assert write.SUBMIT_TEMPLATE.fullmatch(custom_id) is None
    for template in templates.values():
        assert template.fullmatch("mostlikely:join:5") is None
        assert template.fullmatch("mostlikely:vote:5") is None
        assert template.fullmatch(blank_custom_id(5)) is None
    assert write.TEMPLATE.fullmatch("fiftyfifty:write:5")["round"] == "5"
    assert dm.TEMPLATE.fullmatch("fiftyfifty:dm:5")["round"] == "5"
    match = vote.TEMPLATE.fullmatch("fiftyfifty:vote:12:b")
    assert match["question"] == "12" and match["choice"] == "b"
    assert vote.TEMPLATE.fullmatch("fiftyfifty:vote:12:c") is None
    assert write.SUBMIT_TEMPLATE.fullmatch("fiftyfifty:blank:7")["round"] == "7"
    assert vote.GUESS_TEMPLATE.fullmatch("fiftyfifty:guess:12")["question"] == "12"
    assert vote.GUESS_TEMPLATE.fullmatch("fiftyfifty:guess:12:a") is None


async def test_write_click_opens_modal(bot):
    _, rnd = await running(bot)
    ctx = await bot.repo.write_context(rnd["id"], 11)
    click = click_for(bot, 11)
    await handle_write_click(click, rnd["id"])
    click.response.send_modal.assert_awaited_once()
    click.response.defer.assert_not_awaited()
    click.response.send_message.assert_not_awaited()
    (modal,) = click.response.send_modal.await_args.args
    assert modal.title == MODAL_TITLE and len(modal.title) <= 45
    assert modal.custom_id == f"fiftyfifty:blank:{rnd['id']}"
    assert modal.is_finished() and modal.timeout is None
    display, label_a, label_b = modal.children
    assert isinstance(display, discord.ui.TextDisplay)
    assert topic_text(ctx["topic"]) in display.content
    assert label_a.description == f"Topic idea: {ctx['topic']}"
    assert (label_a.text, label_b.text) == ("Option A", "Option B")
    for label, input_id in ((label_a, "a"), (label_b, "b")):
        text_input = label.component
        assert text_input.custom_id == input_id and text_input.default is None
        assert (text_input.min_length, text_input.max_length) == (1, 80)

    await flow.submit_question(bot, rnd["id"], 11, "cold soup", "warm salad")
    again = click_for(bot, 11)
    await handle_write_click(again, rnd["id"])
    (prefilled,) = again.response.send_modal.await_args.args
    assert prefilled.children[1].component.default == "cold soup"
    assert prefilled.children[2].component.default == "warm salad"
    assert prefilled is not modal


async def test_write_click_refusals(bot):
    game, rnd = await running(bot, users=(11, 12, 13, 14))

    async def refused(user_id, round_id=rnd["id"]):
        click = click_for(bot, user_id)
        await handle_write_click(click, round_id)
        click.response.send_modal.assert_not_awaited()
        assert click.response.send_message.await_args.kwargs["ephemeral"] is True
        return reply_of(click)

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


async def test_write_click_missed_window_logged(bot, caplog):
    _, rnd = await running(bot)
    click = click_for(bot, 11)
    click.response.send_modal.side_effect = http_error(discord.NotFound, 404)
    with caplog.at_level(logging.WARNING):
        await handle_write_click(click, rnd["id"])
    (warning,) = warnings_in(caplog)
    assert "could not open the form" in warning and f"round {rnd['id']}" in warning
    click.response.send_message.assert_not_awaited()
    click.followup.send.assert_not_awaited()


async def test_question_modal_is_never_stored():
    """Built inside the event loop, as a click handler does, the form is finished at once."""
    modal = question_modal(42, "food")
    assert type(modal) is discord.ui.Modal
    assert modal.is_finished()
    store = ViewStore(MagicMock())
    if not modal.is_finished():
        store.add_view(modal)
    assert store._modals == {}
    assert modal.custom_id == blank_custom_id(42) == "fiftyfifty:blank:42"
    assert question_modal(42, "food") is not modal


def test_question_modal_has_the_topic_and_two_inputs():
    modal = question_modal(42, "road_trips", "soup", "salad")
    payload = modal.to_dict()
    display, label_a, label_b = payload["components"]
    assert display["type"] == 10
    assert display["content"] == (
        "**Would you rather...**\n"
        "Write both options. Aim for a question this group splits 50/50.\n"
        "Topic idea: **road\\_trips**. Ignore it if you have something better."
    )
    assert label_a["type"] == 18 and label_a["label"] == "Option A"
    assert label_a["description"] == "Topic idea: road_trips"
    assert label_b["type"] == 18 and label_b["label"] == "Option B"
    assert not label_b.get("description")
    for label, input_id, value in ((label_a, "a", "soup"), (label_b, "b", "salad")):
        text_input = label["component"]
        assert text_input["type"] == 4 and text_input["custom_id"] == input_id
        assert text_input["value"] == value and text_input["placeholder"] == OPTION_HELP[input_id]
        assert text_input["max_length"] == 80 and text_input["min_length"] == 1
    bare = question_modal(1, None)
    assert "Topic idea" not in bare.children[0].content
    assert bare.children[1].description is None
    assert not bare.to_dict()["components"][1]["component"].get("value")
    assert len(question_modal(1, "x" * 200).children[1].description) == 100


def test_options_from_submit():
    input_a = {"type": 4, "id": 3, "custom_id": "a", "value": "soup"}
    input_b = {"type": 4, "id": 4, "custom_id": "b", "value": "salad"}
    labels = {
        "custom_id": "x",
        "components": [
            {"type": 10},
            {"type": 18, "component": input_a},
            {"type": 18, "component": input_b},
        ],
    }
    assert options_from_submit(labels) == ("soup", "salad")
    rows = {
        "components": [
            {"type": 1, "components": [input_a]},
            {"type": 1, "components": [input_b]},
        ]
    }
    assert options_from_submit(rows) == ("soup", "salad")
    assert options_from_submit({"components": [{"type": 1, "components": [input_b, input_a]}]}) == (
        "soup",
        "salad",
    )
    mixed = {
        "components": [{"type": 18, "component": input_b}, {"type": 1, "components": [input_a]}]
    }
    assert options_from_submit(mixed) == ("soup", "salad")
    assert options_from_submit({"components": [{"type": 18, "component": input_a}]}) == (
        "soup",
        None,
    )
    old_form = {"type": 4, "custom_id": "blank", "value": "soup"}
    assert options_from_submit({"components": [{"type": 18, "component": old_form}]}) == (
        None,
        None,
    )
    select = {**input_a, "type": 3}
    assert options_from_submit({"components": [{"type": 18, "component": select}]}) == (None, None)
    no_value = {"type": 4, "custom_id": "a"}
    number = {**input_b, "value": 5}
    odd = {"components": [{"type": 18, "component": no_value}, {"type": 18, "component": number}]}
    assert options_from_submit(odd) == (None, None)
    assert options_from_submit({"components": "nope"}) == (None, None)
    assert options_from_submit({"components": ["nope", {"type": 18}, {"type": 1}]}) == (None, None)
    assert options_from_submit({}) == (None, None)
    assert options_from_submit(None) == (None, None)
    assert options_from_submit("fiftyfifty:blank:1") == (None, None)
    empty = {**input_a, "value": ""}
    assert options_from_submit({"components": [{"type": 18, "component": empty}]}) == ("", None)
    twice = {"components": [{"type": 18, "component": input_a}, {"type": 18, "component": empty}]}
    assert options_from_submit(twice) == ("soup", None)


async def test_listener_ignores_other_interactions(bot):
    _, rnd = await running(bot)
    button = interaction_for(bot, 11)
    button.type = discord.InteractionType.component
    button.data = {"custom_id": blank_custom_id(rnd["id"]), "component_type": 2}
    assert not await handle_question_submit(button)
    for custom_id in (
        "mostlikely:blank:1",
        "fiftyfifty:blank:abc",
        f"fiftyfifty:blank:{rnd['id']}:x",
    ):
        other = submit_for(bot, 11, custom_id, "soup", "salad")
        assert not await handle_question_submit(other)
        other.response.defer.assert_not_awaited()
        other.response.send_message.assert_not_awaited()
    empty = submit_for(bot, 11, "x", "soup", "salad")
    empty.data = None
    assert not await handle_question_submit(empty)
    await FiftyFiftyEvents.on_interaction(None, button)
    button.response.defer.assert_not_awaited()
    assert await answer_of(bot, rnd, 11) is None


async def test_submit_with_no_modal_in_memory(bot):
    _, rnd = await running(bot)
    restarted = make_bot(bot.repo.db)
    restarted.channel = bot.channel
    restarted.get_channel = bot.get_channel
    submit = submit_for(restarted, 11, blank_custom_id(rnd["id"]), "cold soup", "warm salad")
    await FiftyFiftyEvents.on_interaction(None, submit)
    assert reply_of(submit).startswith(SAVED)
    assert await answer_of(bot, rnd, 11) == ("cold soup", "warm salad")
    assert "1/3 written." in bot.channel.messages[0].content


async def test_submit_saves_and_edits(bot):
    _, rnd = await running(bot)
    submit = submit_for(bot, 11, blank_custom_id(rnd["id"]), "  cold   soup ", "Warm salad?")
    assert await handle_question_submit(submit)
    submit.response.defer.assert_awaited_once_with(ephemeral=True)
    assert submit.followup.send.await_args.kwargs["ephemeral"] is True
    assert reply_of(submit).split("\n") == [
        SAVED,
        "Would you rather cold soup or Warm salad?",
        "You can change it until writing closes.",
    ]
    assert await answer_of(bot, rnd, 11) == ("cold soup", "Warm salad")
    round_message = bot.channel.messages[0]
    assert "1/3 written." in round_message.content
    edit = submit_for(bot, 11, blank_custom_id(rnd["id"]), "warm *soup*", "_ice_", layout="row")
    assert await handle_question_submit(edit)
    assert "Would you rather warm \\*soup\\* or \\_ice\\_?" in reply_of(edit)
    assert await answer_of(bot, rnd, 11) == ("warm *soup*", "_ice_")
    assert "1/3 written." in round_message.content


async def test_submit_clears_dm_flag(bot):
    _, rnd = await running(bot)
    ctx = await bot.repo.write_context(rnd["id"], 11)
    async with bot.repo.db.transaction() as conn:
        await conn.execute(
            "UPDATE fiftyfifty_questions SET dm_prompted_at = 'x' WHERE id = ?",
            (ctx["question_id"],),
        )
    await flow.submit_question(bot, rnd["id"], 11, "by dm", "or not", via_dm=True)
    assert (await bot.repo.write_context(rnd["id"], 11))["dm_prompted_at"] == "x"
    form = submit_for(bot, 11, blank_custom_id(rnd["id"]), "by form", "or not")
    await handle_question_submit(form)
    after = await bot.repo.write_context(rnd["id"], 11)
    assert after["dm_prompted_at"] is None and after["option_a"] == "by form"


async def test_submit_after_close(bot):
    _, rnd = await running(bot)
    await flow.submit_question(bot, rnd["id"], 11, "in time", "or not")
    await bot.repo.close_writing(rnd["id"], flow.RNG)
    late = submit_for(bot, 12, blank_custom_id(rnd["id"]), "too late", "or not")
    assert await handle_question_submit(late)
    assert reply_of(late) == WRITING_CLOSED
    change = submit_for(bot, 11, blank_custom_id(rnd["id"]), "changed", "or not")
    await handle_question_submit(change)
    assert reply_of(change) == WRITING_CLOSED
    assert await answer_of(bot, rnd, 11) == ("in time", "not")
    assert await answer_of(bot, rnd, 12) is None
    gone = submit_for(bot, 11, blank_custom_id(9999), "x", "y")
    await handle_question_submit(gone)
    assert reply_of(gone) == "That game is over."


async def test_submit_needs_both_options(bot):
    _, rnd = await running(bot)
    needed = "Both options are needed, and neither can be empty."
    for option_a, option_b in (("  \n ", "salad"), ("soup", " "), ("soup", None), (None, None)):
        half = submit_for(bot, 11, blank_custom_id(rnd["id"]), option_a, option_b)
        assert await handle_question_submit(half)
        assert reply_of(half) == needed, (option_a, option_b)
    unreadable = submit_for(bot, 11, blank_custom_id(rnd["id"]), "soup", "salad")
    unreadable.data["components"] = [{"type": 99}]
    assert await handle_question_submit(unreadable)
    assert reply_of(unreadable) == needed
    old_form = submit_for(bot, 11, blank_custom_id(rnd["id"]), "soup", "salad")
    old_form.data["components"] = [
        {"type": 18, "component": {"type": 4, "custom_id": "blank", "value": "soup"}}
    ]
    assert await handle_question_submit(old_form)
    assert reply_of(old_form) == needed
    assert await answer_of(bot, rnd, 11) is None
    assert "0/3 written." in bot.channel.messages[0].content


async def test_submit_too_long_is_cut(bot):
    _, rnd = await running(bot)
    submit = submit_for(bot, 11, blank_custom_id(rnd["id"]), "x" * 90, "short")
    await handle_question_submit(submit)
    assert await answer_of(bot, rnd, 11) == ("x" * 80, "short")
    assert "\n(Each option is cut to 80 characters.)\n" in reply_of(submit)
    exact = submit_for(bot, 12, blank_custom_id(rnd["id"]), "  " + "y " * 40, "z" * 80)
    await handle_question_submit(exact)
    assert await answer_of(bot, rnd, 12) == (("y " * 40).strip(), "z" * 80)
    assert "cut to" not in reply_of(exact)


async def test_submit_after_leaving(bot):
    game, rnd = await running(bot, users=(11, 12, 13, 14))
    await flow.leave_game(bot, game, 14)
    submit = submit_for(bot, 14, blank_custom_id(rnd["id"]), "soup", "salad")
    await handle_question_submit(submit)
    assert reply_of(submit) == "You've left the game."
    assert await answer_of(bot, rnd, 14) is None


async def test_submit_not_on_roster(bot):
    game, rnd = await running(bot)
    await flow.join_game(bot, game, 14)
    late = submit_for(bot, 14, blank_custom_id(rnd["id"]), "soup", "salad")
    await handle_question_submit(late)
    assert reply_of(late) == NOT_IN_ROUND
    outsider = submit_for(bot, 99, blank_custom_id(rnd["id"]), "soup", "salad")
    await handle_question_submit(outsider)
    assert "Only players in this game can write" in reply_of(outsider)
    assert await bot.repo.write_progress(rnd["id"]) == (0, 3)


async def test_submit_unexpected_error_replies(bot, monkeypatch, caplog):
    _, rnd = await running(bot)

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(flow, "submit_question", boom)
    submit = submit_for(bot, 11, blank_custom_id(rnd["id"]), "soup", "salad")
    with caplog.at_level(logging.ERROR):
        assert await handle_question_submit(submit)
    assert reply_of(submit) == "Something went wrong; the logs have details."
    assert any("question submit failed" in r.getMessage() for r in caplog.records)
    assert not any("soup" in r.getMessage() or "salad" in r.getMessage() for r in caplog.records)

    async def refuse(*args, **kwargs):
        raise GameError("Not like that.")

    monkeypatch.setattr(flow, "submit_question", refuse)
    refused = submit_for(bot, 11, blank_custom_id(rnd["id"]), "soup", "salad")
    await handle_question_submit(refused)
    assert reply_of(refused) == "Not like that."

    async def no_reply(*args, **kwargs):
        raise http_error(discord.HTTPException, 500)

    refused.followup.send.side_effect = no_reply
    assert await handle_question_submit(refused)


async def test_submit_saved_when_defer_fails(bot, caplog):
    _, rnd = await running(bot)
    submit = submit_for(bot, 11, blank_custom_id(rnd["id"]), "soup", "salad")
    submit.response.defer.side_effect = http_error(discord.HTTPException, 500)
    with caplog.at_level(logging.WARNING):
        assert await handle_question_submit(submit)
    assert await answer_of(bot, rnd, 11) == ("soup", "salad")
    assert any("could not defer a question submit" in w for w in warnings_in(caplog))
    assert reply_of(submit).startswith("Saved.")
    assert "1/3 written." in bot.channel.messages[0].content


async def test_last_submit_starts_voting(bot):
    _, rnd = await running(bot)
    for uid in (11, 12, 13):
        form = submit_for(bot, uid, blank_custom_id(rnd["id"]), f"soup {uid}", f"salad {uid}")
        await handle_question_submit(form)
    round_message, first = bot.channel.messages
    assert round_message.content.endswith("Writing is closed: 3 questions to vote on.")
    assert round_message.view is None
    assert first.content.startswith("_ _\n🗳️ **Question 1 of 3** (round 1)")
    assert (await bot.repo.get_round(rnd["id"]))["write_closed_at"]

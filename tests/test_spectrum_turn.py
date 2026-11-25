"""The Spectrum turn buttons, both pop-ups, and the listener that saves a submitted pop-up."""

import logging
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest
from discord.ui.view import ViewStore
from spectrum_fakes import (
    CHANNEL_ID,
    click_for,
    interaction_for,
    make_bot,
    play_to_guessing,
    reply_of,
    submit_for,
)

from gaminator.core.util import GameError
from gaminator.games.fiftyfifty import lobby as fiftyfifty_lobby
from gaminator.games.fiftyfifty import vote as fiftyfifty_vote
from gaminator.games.fiftyfifty import write as fiftyfifty_write
from gaminator.games.mostlikely import lobby as mostlikely_lobby
from gaminator.games.mostlikely import vote as mostlikely_vote
from gaminator.games.spectrum import guess, lobby, turn
from gaminator.games.spectrum.events import SpectrumEvents
from gaminator.games.spectrum.guess import GuessButton, handle_guess_click
from gaminator.games.spectrum.services import game as flow
from gaminator.games.spectrum.services.rules import (
    BAD_GUESS,
    CLUE_OVER,
    EMPTY_CLUE,
    GIVER_SITS_OUT,
    GUESS_OVER,
    JOINED_LATE,
    LEFT,
    NO_DIGITS,
    NOT_GIVER,
)
from gaminator.games.spectrum.turn import (
    CLUE_TITLE,
    GUESS_TITLE,
    clue_form_id,
    clue_modal,
    guess_form_id,
    guess_modal,
    handle_submit,
    handle_target_click,
    handle_write_click,
    value_from_submit,
)

TARGET = 97
STAMPS = re.compile(r"<t:[0-9]+:R>|<@[0-9]+>")
SAVED = "Your clue is up."


@pytest.fixture
async def bot(db):
    return make_bot(db)


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="x"), "x")


def warnings_in(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


async def running(bot, users=(11, 12, 13)):
    """A started game with every target set to TARGET. Returns the game and its open turn."""
    game = await bot.repo.create_game(1, "Friday", users[0], CHANNEL_ID)
    for uid in users:
        await bot.repo.join(game["id"], uid)
    await flow.begin_game(bot, game, 1)
    async with bot.repo.db.transaction() as conn:
        await conn.execute("UPDATE spectrum_turns SET target = ?", (TARGET,))
    game = await bot.repo.get_game(game["id"])
    rnd = await bot.repo.get_current_round(game["id"])
    return game, await bot.repo.current_turn(rnd["id"])


async def others(bot, game, open_turn):
    players = await bot.repo.list_players(game["id"], active_only=True)
    return [p["user_id"] for p in players if p["user_id"] != open_turn["giver_user_id"]]


def message_of(bot, message_id):
    return next(m for m in bot.channel.messages if m.id == message_id)


def everything_public(bot):
    seen = []
    for message in bot.channel.messages:
        seen += [message.sent or "", message.content or ""]
        seen += [edit.get("content") or "" for edit in message.edits]
    return STAMPS.sub("", "\n".join(seen))


def clue_submit(bot, user_id, open_turn, value, layout="label"):
    return submit_for(bot, user_id, clue_form_id(open_turn["id"]), "clue", value, layout)


def guess_submit(bot, user_id, open_turn, value, layout="label"):
    return submit_for(bot, user_id, guess_form_id(open_turn["id"]), "guess", value, layout)


def is_private(interaction):
    """True when every reply to the interaction, direct or followup, was ephemeral."""
    calls = interaction.followup.send.await_args_list
    calls = calls + interaction.response.send_message.await_args_list
    return bool(calls) and all(call.kwargs.get("ephemeral") is True for call in calls)


def test_custom_ids_do_not_collide():
    ids = {
        "lobby": lobby.lobby_custom_id("join", 5),
        "target": turn.target_custom_id(5),
        "write": turn.write_custom_id(5),
        "guess": guess.guess_custom_id(5),
        "clueform": turn.clue_form_id(5),
        "guessform": turn.guess_form_id(5),
    }
    templates = {
        "lobby": lobby.TEMPLATE,
        "target": turn.TARGET_TEMPLATE,
        "write": turn.WRITE_TEMPLATE,
        "guess": guess.TEMPLATE,
    }
    foreign = (
        fiftyfifty_lobby.TEMPLATE,
        fiftyfifty_write.TEMPLATE,
        fiftyfifty_write.SUBMIT_TEMPLATE,
        fiftyfifty_vote.TEMPLATE,
        fiftyfifty_vote.GUESS_TEMPLATE,
        mostlikely_lobby.TEMPLATE,
        mostlikely_vote.TEMPLATE,
    )
    for kind, custom_id in ids.items():
        for name, template in templates.items():
            assert (template.fullmatch(custom_id) is not None) == (kind == name), (kind, name)
        assert (turn.SUBMIT.fullmatch(custom_id) is not None) == kind.endswith("form"), kind
        for template in foreign:
            assert template.fullmatch(custom_id) is None, (kind, template.pattern)
        assert len(custom_id) <= 100
    theirs = (
        "fiftyfifty:join:5",
        "fiftyfifty:write:5",
        "fiftyfifty:blank:5",
        "fiftyfifty:guess:5",
        "fiftyfifty:vote:5:a",
        "mostlikely:join:5",
        "mostlikely:vote:5",
    )
    for template in (*templates.values(), turn.SUBMIT):
        for custom_id in theirs:
            assert template.fullmatch(custom_id) is None, (template.pattern, custom_id)
    assert turn.TARGET_TEMPLATE.fullmatch("spectrum:target:12")["turn"] == "12"
    assert turn.WRITE_TEMPLATE.fullmatch("spectrum:writeclue:12")["turn"] == "12"
    assert guess.TEMPLATE.fullmatch("spectrum:guess:12")["turn"] == "12"
    assert guess.TEMPLATE.fullmatch("spectrum:guess:12:a") is None
    match = turn.SUBMIT.fullmatch("spectrum:guessform:7")
    assert match["kind"] == "guessform" and match["turn"] == "7"
    assert turn.SUBMIT.fullmatch("spectrum:clueform:7")["kind"] == "clueform"
    assert turn.SUBMIT.fullmatch("spectrum:clueform:x") is None


async def test_buttons_rebuild_from_custom_ids(bot):
    for cls, template, custom_id in (
        (turn.TargetButton, turn.TARGET_TEMPLATE, "spectrum:target:42"),
        (turn.WriteClueButton, turn.WRITE_TEMPLATE, "spectrum:writeclue:42"),
        (GuessButton, guess.TEMPLATE, "spectrum:guess:42"),
    ):
        item = await cls.from_custom_id(MagicMock(), MagicMock(), template.fullmatch(custom_id))
        assert item.turn_id == 42 and item.item.custom_id == custom_id
        assert len(item.item.label) <= 80
    assert [c.item.label for c in guess.guess_view(42).children] == ["Guess"]


async def test_target_button_giver_sees_target(bot):
    _, open_turn = await running(bot)
    click = click_for(bot, open_turn["giver_user_id"])
    await handle_target_click(click, open_turn["id"])
    click.response.defer.assert_awaited_once_with(ephemeral=True)
    click.response.send_modal.assert_not_awaited()
    assert is_private(click)
    ctx = await bot.repo.turn_context(open_turn["id"], open_turn["giver_user_id"])
    assert reply_of(click) == flow.target_text(ctx)
    assert reply_of(click).startswith("🎯 Your target is **97**.")
    assert "97" not in everything_public(bot)


async def test_target_button_others_refused(bot):
    game, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]
    for uid in (*await others(bot, game, open_turn), 99):
        click = click_for(bot, uid)
        await handle_target_click(click, open_turn["id"])
        click.response.defer.assert_awaited_once_with(ephemeral=True)
        assert is_private(click)
        assert reply_of(click) == f"Only **U{giver}** can see the target. Hang on for their clue."
        assert "97" not in reply_of(click)
    missing = click_for(bot, giver)
    await handle_target_click(missing, 9999)
    assert reply_of(missing) == "That game is over." and is_private(missing)


async def test_target_same_after_new_bot(bot):
    _, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]
    async with bot.repo.db.transaction() as conn:
        await conn.execute("UPDATE spectrum_turns SET target = 23 WHERE id = ?", (open_turn["id"],))
    first = await flow.show_target(bot, open_turn["id"], giver)
    assert "Your target is **23**." in first
    assert await flow.show_target(bot, open_turn["id"], giver) == first
    restarted = make_bot(bot.repo.db)
    assert restarted.repo is not bot.repo
    assert await flow.show_target(restarted, open_turn["id"], giver) == first
    click = click_for(restarted, giver)
    await handle_target_click(click, open_turn["id"])
    assert reply_of(click) == first


async def test_target_button_after_clue(bot):
    game, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]
    await play_to_guessing(bot, game)
    click = click_for(bot, giver)
    await handle_target_click(click, open_turn["id"])
    assert reply_of(click) == "Your clue is in. The target stays secret until the reveal."
    assert is_private(click)
    await bot.repo.close_turn(open_turn["id"], "revealed")
    closed = click_for(bot, giver)
    await handle_target_click(closed, open_turn["id"])
    assert reply_of(closed) == "That turn is over."


async def test_target_never_in_channel_before_reveal(bot):
    game, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]
    one, two = await others(bot, game, open_turn)
    await handle_target_click(click_for(bot, giver), open_turn["id"])
    await handle_target_click(click_for(bot, one), open_turn["id"])
    await handle_write_click(click_for(bot, giver), open_turn["id"])
    await handle_submit(clue_submit(bot, giver, open_turn, "seventy 7"))
    await handle_submit(clue_submit(bot, giver, open_turn, "rain at a picnic"))
    await handle_guess_click(click_for(bot, one), open_turn["id"])
    await handle_submit(guess_submit(bot, one, open_turn, "81"))
    shown = everything_public(bot)
    assert "97" not in shown and "81" not in shown and "rain at a picnic" in shown
    await handle_submit(guess_submit(bot, two, open_turn, "82"))
    assert "## 🎯 The target was 97" in everything_public(bot)


async def test_target_never_logged(bot, caplog):
    with caplog.at_level(logging.DEBUG):
        game, open_turn = await running(bot)
        giver = open_turn["giver_user_id"]
        one, two = await others(bot, game, open_turn)
        await handle_target_click(click_for(bot, giver), open_turn["id"])
        await handle_submit(clue_submit(bot, giver, open_turn, "seventy 7"))
        await handle_submit(clue_submit(bot, giver, open_turn, "soggy sandwiches"))
        await handle_submit(guess_submit(bot, one, open_turn, "81"))
        await handle_submit(guess_submit(bot, two, open_turn, "82"))
    logged = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("gaminator"))
    assert f"turn {open_turn['id']} closed (everyone guessed)" in logged
    for secret in ("97", "81", "82", "soggy", "seventy"):
        assert secret not in logged, secret


async def test_write_clue_opens_modal(bot):
    _, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]
    click = click_for(bot, giver)
    await handle_write_click(click, open_turn["id"])
    click.response.send_modal.assert_awaited_once()
    click.response.defer.assert_not_awaited()
    click.response.send_message.assert_not_awaited()
    (modal,) = click.response.send_modal.await_args.args
    assert modal.title == CLUE_TITLE == "Spectrum: your clue" and len(modal.title) <= 45
    assert modal.custom_id == f"spectrum:clueform:{open_turn['id']}"
    assert modal.is_finished() and modal.timeout is None
    display, label = modal.children
    assert isinstance(display, discord.ui.TextDisplay)
    ctx = await bot.repo.turn_context(open_turn["id"], giver)
    assert display.content == flow.target_text(ctx, in_form=True) and "**97**" in display.content
    assert "Press **Write clue**" not in display.content
    assert label.text == "Your clue (no digits)" and len(label.text) <= 45
    ends = f"1 = {open_turn['left_end']}, 100 = {open_turn['right_end']}"
    assert label.description == f"Target 97: {ends}" and len(label.description) <= 100
    text_input = label.component
    assert text_input.custom_id == "clue" and text_input.default is None
    assert (text_input.min_length, text_input.max_length) == (1, 80)
    assert len(text_input.placeholder) <= 100
    again = click_for(bot, giver)
    await handle_write_click(again, open_turn["id"])
    assert again.response.send_modal.await_args.args[0] is not modal


async def test_modal_text_fits_discord_limits():
    longest = {
        "turn_id": 123456789,
        "left_end": "l" * 24,
        "right_end": "r" * 24,
        "target": 100,
        "clue": "*" * 80,
        "clue_deadline_at": "2030-01-01T00:00:00+00:00",
    }
    for modal in (clue_modal(longest), guess_modal(longest, 100)):
        display, label = modal.children
        assert len(modal.title) <= 45 and len(modal.children) <= 5
        assert len(display.content) <= 4000
        assert len(label.text) <= 45 and len(label.description) <= 100
        assert len(modal.custom_id) <= 100


async def test_write_clue_refusals(bot):
    game, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]

    async def refused(user_id, turn_id=open_turn["id"]):
        click = click_for(bot, user_id)
        await handle_write_click(click, turn_id)
        click.response.send_modal.assert_not_awaited()
        assert click.response.send_message.await_args.kwargs["ephemeral"] is True
        return reply_of(click)

    for uid in (*await others(bot, game, open_turn), 99):
        assert await refused(uid) == NOT_GIVER == "Only the clue giver can write the clue."
    assert await refused(giver, 9999) == "That game is over."
    await play_to_guessing(bot, game)
    assert await refused(giver) == CLUE_OVER
    await flow.finish(bot, game)
    assert await refused(giver) == "That game is over."


async def test_write_clue_missed_window_logged(bot, caplog):
    _, open_turn = await running(bot)
    click = click_for(bot, open_turn["giver_user_id"])
    click.response.send_modal.side_effect = http_error(discord.NotFound, 404)
    with caplog.at_level(logging.WARNING):
        await handle_write_click(click, open_turn["id"])
    (warning,) = warnings_in(caplog)
    assert "the click expired" in warning and f"turn {open_turn['id']}" in warning
    assert "97" not in warning
    click.response.send_message.assert_not_awaited()
    failing = click_for(bot, open_turn["giver_user_id"])
    failing.response.send_modal.side_effect = GameError("nope")
    await handle_write_click(failing, open_turn["id"])
    assert reply_of(failing) == "nope"


def test_value_from_submit():
    def payload(layout, items):
        if layout == "row":
            return {"components": [{"type": 1, "components": [item]} for item in items]}
        return {"components": [{"type": 10}] + [{"type": 18, "component": i} for i in items]}

    clue = {"type": 4, "custom_id": "clue", "value": "rain"}
    other = {"type": 4, "custom_id": "guess", "value": "42"}
    for layout in ("label", "row"):
        assert value_from_submit(payload(layout, [clue]), "clue") == "rain"
        assert value_from_submit(payload(layout, [other, clue]), "clue") == "rain"
        assert value_from_submit(payload(layout, [other, clue]), "guess") == "42"
        assert value_from_submit(payload(layout, [other]), "clue") is None
        assert value_from_submit(payload(layout, []), "clue") is None
        number = {"type": 4, "custom_id": "clue", "value": 7}
        assert value_from_submit(payload(layout, [number]), "clue") is None
        select = {"type": 3, "custom_id": "clue", "value": "rain"}
        assert value_from_submit(payload(layout, [select]), "clue") is None
    assert value_from_submit({"components": "nope"}, "clue") is None
    assert value_from_submit({"components": ["nope", None, 7]}, "clue") is None
    assert value_from_submit({"components": [{"type": 18, "component": "x"}]}, "clue") is None
    assert value_from_submit({"components": [{"type": 1, "components": "x"}]}, "clue") is None
    assert value_from_submit({}, "clue") is None
    assert value_from_submit(None, "clue") is None
    assert value_from_submit("text", "clue") is None


async def test_listener_ignores_other_interactions(bot):
    _, open_turn = await running(bot)
    before = len(bot.channel.messages)
    click = interaction_for(bot, open_turn["giver_user_id"])
    click.type = discord.InteractionType.component
    click.data = {"custom_id": clue_form_id(open_turn["id"])}
    assert not await handle_submit(click)
    for custom_id in ("fiftyfifty:blank:1", "spectrum:guess:1", "spectrum:clueform:", "", 7):
        foreign = submit_for(bot, open_turn["giver_user_id"], custom_id, "clue", "rain")
        assert not await handle_submit(foreign)
        foreign.response.defer.assert_not_awaited()
        foreign.followup.send.assert_not_awaited()
    odd = interaction_for(bot, open_turn["giver_user_id"])
    odd.type = discord.InteractionType.modal_submit
    odd.data = None
    assert not await handle_submit(odd)
    assert len(bot.channel.messages) == before
    assert (await bot.repo.get_turn(open_turn["id"]))["clue"] is None


async def test_submit_with_no_modal_in_memory(bot):
    game, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]
    one, _ = await others(bot, game, open_turn)
    restarted = make_bot(bot.repo.db)
    restarted.channel = bot.channel
    restarted.get_channel = bot.get_channel
    cog = SimpleNamespace(bot=restarted)
    listener = SpectrumEvents.on_interaction
    submit = clue_submit(restarted, giver, open_turn, "rain at a picnic")
    await listener(cog, submit)
    assert reply_of(submit) == SAVED
    assert (await bot.repo.get_turn(open_turn["id"]))["clue"] == "rain at a picnic"
    guessed = guess_submit(restarted, one, open_turn, "64")
    await listener(cog, guessed)
    assert reply_of(guessed) == "Your guess is **64**. You can change it until the turn closes."
    row = await bot.repo.turn_context(open_turn["id"], one)
    assert row["my_guess"] == 64


async def test_modals_are_never_stored(bot):
    game, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]
    one, _ = await others(bot, game, open_turn)
    store = ViewStore(MagicMock())

    def send_through(click):
        async def send_modal(modal):
            if not modal.is_finished():
                store.add_view(modal)

        click.response.send_modal.side_effect = send_modal
        return click

    await handle_write_click(send_through(click_for(bot, giver)), open_turn["id"])
    await play_to_guessing(bot, game)
    await handle_guess_click(send_through(click_for(bot, one)), open_turn["id"])
    assert store._modals == {}
    built = clue_modal(await bot.repo.turn_context(open_turn["id"], giver))
    assert type(built) is discord.ui.Modal


async def test_clue_submit_posts_guess_then_edits_turn(bot):
    game, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]
    turn_message = message_of(bot, open_turn["turn_message_id"])
    order = []
    send, edit = bot.channel.send, turn_message.edit

    async def record_send(content=None, **kwargs):
        order.append(("send", turn_message.view is not None))
        return await send(content, **kwargs)

    async def record_edit(**kwargs):
        order.append(("edit", len(bot.channel.messages)))
        return await edit(**kwargs)

    bot.channel.send, turn_message.edit = record_send, record_edit
    before = len(bot.channel.messages)
    submit = clue_submit(bot, giver, open_turn, "  rain   at a picnic ")
    assert await handle_submit(submit)
    assert reply_of(submit) == SAVED and is_private(submit)
    assert order == [("send", True), ("edit", before + 1)]
    saved = await bot.repo.get_turn(open_turn["id"])
    assert saved["clue"] == "rain at a picnic" and saved["eligible_guessers"] == 2
    posted = bot.channel.messages[-1]
    assert posted.id == saved["guess_message_id"]
    assert posted.content == flow.guess_text(game, saved, f"U{giver}", 0, 2)
    assert isinstance(posted.view.children[0], GuessButton)
    assert posted.view.children[0].item.custom_id == f"spectrum:guess:{open_turn['id']}"
    assert turn_message.view is None
    assert turn_message.content == flow.turn_done_text(game, saved, f"U{giver}", "clue")
    assert f"**U{giver}**'s clue: **rain at a picnic**" in turn_message.content
    assert "<@" not in turn_message.content


async def test_clue_submit_digits_refused_and_echoed(bot):
    _, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]
    before = len(bot.channel.messages)
    for raw in ("about *7* out of ten", "about ٧ of ten", "level 7️⃣"):
        submit = clue_submit(bot, giver, open_turn, raw)
        assert await handle_submit(submit)
        lines = reply_of(submit).split("\n")
        assert lines[0] == NO_DIGITS and lines[-1] == "Press **Write clue** to try again."
        assert lines[1] == "You wrote: " + discord.utils.escape_markdown(raw)
        assert is_private(submit)
    for raw in ("", "   ", None):
        empty = clue_submit(bot, giver, open_turn, raw)
        assert await handle_submit(empty)
        assert reply_of(empty).split("\n") == [EMPTY_CLUE, "Press **Write clue** to try again."]
    assert (await bot.repo.get_turn(open_turn["id"]))["clue"] is None
    assert len(bot.channel.messages) == before
    assert message_of(bot, open_turn["turn_message_id"]).view is not None
    long = clue_submit(bot, giver, open_turn, "x" * 120)
    assert await handle_submit(long)
    assert reply_of(long) == SAVED
    assert (await bot.repo.get_turn(open_turn["id"]))["clue"] == "x" * 80


async def test_clue_submit_wrong_user(bot):
    game, open_turn = await running(bot)
    one, _ = await others(bot, game, open_turn)
    for uid in (one, 99):
        submit = clue_submit(bot, uid, open_turn, "rain at a picnic")
        assert await handle_submit(submit)
        assert reply_of(submit) == NOT_GIVER and is_private(submit)
    assert (await bot.repo.get_turn(open_turn["id"]))["clue"] is None
    await bot.repo.leave(open_turn["giver_id"])
    gone = clue_submit(bot, open_turn["giver_user_id"], open_turn, "rain at a picnic")
    assert await handle_submit(gone)
    assert reply_of(gone) == LEFT
    missing = submit_for(bot, one, clue_form_id(9999), "clue", "rain")
    assert await handle_submit(missing)
    assert reply_of(missing) == "That game is over."


async def test_clue_submit_after_deadline(db):
    bot = make_bot(db, clue_seconds=0)
    game, open_turn = await running(bot)
    assert await flow.close_overdue(bot) == 1
    before = len(bot.channel.messages)
    submit = clue_submit(bot, open_turn["giver_user_id"], open_turn, "rain at a picnic")
    assert await handle_submit(submit)
    assert reply_of(submit) == CLUE_OVER == "The clue step for that turn is over."
    assert (await bot.repo.get_turn(open_turn["id"]))["clue"] is None
    assert len(bot.channel.messages) == before
    again = clue_submit(bot, open_turn["giver_user_id"], open_turn, "second go")
    rnd = await bot.repo.get_current_round(game["id"])
    second = await bot.repo.current_turn(rnd["id"])
    await play_to_guessing(bot, game)
    twice = clue_submit(bot, second["giver_user_id"], second, "second go")
    assert await handle_submit(twice) and await handle_submit(again)
    assert reply_of(twice) == CLUE_OVER and reply_of(again) == CLUE_OVER
    assert (await bot.repo.get_turn(second["id"]))["clue"] == "rain at a picnic"


async def test_clue_post_fails_abandons_turn(bot):
    game, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]
    turn_message = message_of(bot, open_turn["turn_message_id"])
    before = len(bot.channel.messages)
    bot.channel.fail_with = [http_error(discord.HTTPException, 503)] * 2
    submit = clue_submit(bot, giver, open_turn, "rain at a picnic")
    assert await handle_submit(submit)
    assert reply_of(submit) == "I couldn't post your clue, so this turn is skipped."
    closed = await bot.repo.get_turn(open_turn["id"])
    assert closed["outcome"] == "abandoned" and closed["guess_message_id"] is None
    assert len(bot.channel.messages) == before
    assert turn_message.view is None
    assert turn_message.content.endswith("I couldn't post the clue, so this turn is skipped.")
    assert "rain at a picnic" not in everything_public(bot)
    assert await bot.repo.scored_turns(game["id"]) == []
    assert await flow.resume_stalled(bot, grace=0) == 1
    rnd = await bot.repo.get_current_round(game["id"])
    assert (await bot.repo.current_turn(rnd["id"]))["position"] == 2


async def test_clue_post_channel_gone_ends_game(bot):
    game, open_turn = await running(bot)
    bot.channel.fail_with = [http_error(discord.Forbidden, 403)]
    submit = clue_submit(bot, open_turn["giver_user_id"], open_turn, "rain at a picnic")
    assert await handle_submit(submit)
    assert reply_of(submit) == "I couldn't post your clue, so this turn is skipped."
    assert (await bot.repo.get_game(game["id"]))["status"] == "finished"
    assert (await bot.repo.get_turn(open_turn["id"]))["outcome"] == "abandoned"
    assert not any("The target was" in t for t in bot.channel.texts)
    assert "(I couldn't post in the game channel)" in bot.channel.texts[-1]


async def test_submit_saved_when_defer_fails(bot, caplog):
    game, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]
    one, _ = await others(bot, game, open_turn)
    submit = clue_submit(bot, giver, open_turn, "rain at a picnic")
    submit.response.defer.side_effect = http_error(discord.NotFound, 404)
    submit.response.is_done = lambda: False
    with caplog.at_level(logging.WARNING):
        assert await handle_submit(submit)
    assert (await bot.repo.get_turn(open_turn["id"]))["clue"] == "rain at a picnic"
    assert any("could not defer a clueform submit" in w for w in warnings_in(caplog))
    assert reply_of(submit) == SAVED
    guessed = guess_submit(bot, one, open_turn, "64")
    guessed.response.defer.side_effect = http_error(discord.NotFound, 404)
    guessed.response.is_done = lambda: False
    guessed.response.send_message.side_effect = http_error(discord.NotFound, 404)
    with caplog.at_level(logging.WARNING):
        assert await handle_submit(guessed)
    assert (await bot.repo.turn_context(open_turn["id"], one))["my_guess"] == 64
    assert any("could not answer a guessform submit" in w for w in warnings_in(caplog))
    assert "64" not in "\n".join(warnings_in(caplog))


async def test_submit_unexpected_error_replies(bot, monkeypatch, caplog):
    _, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]

    async def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(flow, "submit_clue", broken)
    submit = clue_submit(bot, giver, open_turn, "rain at a picnic")
    assert await handle_submit(submit)
    assert reply_of(submit) == "Something went wrong; the logs have details."
    assert is_private(submit)
    assert any("clueform submit failed for turn" in r.getMessage() for r in caplog.records)

    async def refused(*args):
        raise GameError("not now")

    monkeypatch.setattr(flow, "submit_guess", refused)
    guessed = guess_submit(bot, giver, open_turn, "5")
    assert await handle_submit(guessed)
    assert reply_of(guessed) == "not now"
    monkeypatch.setattr(flow, "close_if_done", broken)
    again = guess_submit(bot, giver, open_turn, "5")
    assert await handle_submit(again)
    assert any("after a submit failed" in r.getMessage() for r in caplog.records)


async def test_submit_replies_are_ephemeral(bot):
    game, open_turn = await running(bot)
    giver = open_turn["giver_user_id"]
    one, two = await others(bot, game, open_turn)
    submits = [
        clue_submit(bot, one, open_turn, "rain"),
        clue_submit(bot, giver, open_turn, "7"),
        clue_submit(bot, giver, open_turn, ""),
        guess_submit(bot, one, open_turn, "50"),
        clue_submit(bot, giver, open_turn, "rain at a picnic"),
        clue_submit(bot, giver, open_turn, "again"),
        guess_submit(bot, giver, open_turn, "50"),
        guess_submit(bot, 99, open_turn, "50"),
        guess_submit(bot, one, open_turn, "abc"),
        guess_submit(bot, one, open_turn, "50"),
        guess_submit(bot, two, open_turn, "60"),
        guess_submit(bot, two, open_turn, "61"),
    ]
    for submit in submits:
        assert await handle_submit(submit)
        submit.response.defer.assert_awaited_once_with(ephemeral=True)
        submit.response.send_modal.assert_not_called()
        assert is_private(submit), reply_of(submit)
    assert reply_of(submits[-1]) == GUESS_OVER


async def test_guess_click_opens_prefilled_modal(bot):
    game, open_turn = await running(bot)
    open_turn = await play_to_guessing(bot, game, clue="rain *at* a picnic")
    one, _ = await others(bot, game, open_turn)
    click = click_for(bot, one)
    await handle_guess_click(click, open_turn["id"])
    click.response.send_modal.assert_awaited_once()
    click.response.defer.assert_not_awaited()
    click.response.send_message.assert_not_awaited()
    (modal,) = click.response.send_modal.await_args.args
    assert modal.title == GUESS_TITLE == "Spectrum: your guess" and len(modal.title) <= 45
    assert modal.custom_id == f"spectrum:guessform:{open_turn['id']}"
    assert modal.is_finished() and modal.timeout is None
    display, label = modal.children
    scale = f"{open_turn['left_end']} ↔ {open_turn['right_end']}"
    assert display.content == f"**{scale}**\nClue: **rain \\*at\\* a picnic**"
    assert "97" not in display.content
    assert label.text == "Your guess, 1 to 100"
    assert label.description == f"1 = {open_turn['left_end']}, 100 = {open_turn['right_end']}"
    text_input = label.component
    assert text_input.custom_id == "guess" and text_input.default is None
    assert (text_input.min_length, text_input.max_length) == (1, 3)
    await flow.submit_guess(bot, open_turn["id"], one, "64")
    again = click_for(bot, one)
    await handle_guess_click(again, open_turn["id"])
    assert again.response.send_modal.await_args.args[0].children[1].component.default == "64"
    missed = click_for(bot, one)
    missed.response.send_modal.side_effect = http_error(discord.NotFound, 404)
    await handle_guess_click(missed, open_turn["id"])
    missed.response.send_message.assert_not_awaited()


async def test_guess_click_refusals(bot):
    game, open_turn = await running(bot, users=(11, 12, 13, 14))
    giver = open_turn["giver_user_id"]
    one, two, _ = await others(bot, game, open_turn)

    async def refused(user_id, turn_id=open_turn["id"]):
        click = click_for(bot, user_id)
        await handle_guess_click(click, turn_id)
        click.response.send_modal.assert_not_awaited()
        assert click.response.send_message.await_args.kwargs["ephemeral"] is True
        return reply_of(click)

    assert await refused(one) == GUESS_OVER
    await play_to_guessing(bot, game)
    assert await refused(giver) == GIVER_SITS_OUT
    await flow.join_game(bot, game, 15)
    assert await refused(15) == JOINED_LATE
    await flow.leave_game(bot, game, two)
    assert await refused(two) == LEFT
    assert "Only players in this game can guess" in await refused(99)
    assert await refused(one, 9999) == "That game is over."
    await bot.repo.close_turn(open_turn["id"], "revealed")
    assert await refused(one) == GUESS_OVER


async def test_guess_submit_saves_and_changes(bot):
    game, open_turn = await running(bot)
    open_turn = await play_to_guessing(bot, game)
    one, _ = await others(bot, game, open_turn)
    posted = message_of(bot, open_turn["guess_message_id"])
    first = guess_submit(bot, one, open_turn, " 40 ")
    assert await handle_submit(first)
    assert reply_of(first) == "Your guess is **40**. You can change it until the turn closes."
    assert "1/2 guesses in." in posted.content
    edits = len(posted.edits)
    second = guess_submit(bot, one, open_turn, "62", layout="row")
    assert await handle_submit(second)
    assert reply_of(second) == "Your guess is **62**. You can change it until the turn closes."
    assert (await bot.repo.turn_context(open_turn["id"], one))["my_guess"] == 62
    assert "1/2 guesses in." in posted.content and len(posted.edits) == edits
    assert posted.view is not None


async def test_guess_submit_bad_number(bot):
    game, open_turn = await running(bot)
    open_turn = await play_to_guessing(bot, game)
    one, _ = await others(bot, game, open_turn)
    for raw in ("abc", "150", "0", "4.5", "-3", "٤٢", "", None):
        submit = guess_submit(bot, one, open_turn, raw)
        assert await handle_submit(submit)
        assert reply_of(submit) == BAD_GUESS and is_private(submit)
    assert (await bot.repo.turn_context(open_turn["id"], one))["my_guess"] is None
    assert "0/2 guesses in." in message_of(bot, open_turn["guess_message_id"]).content


async def test_guess_submit_after_close(db):
    bot = make_bot(db, guess_seconds=0)
    game, open_turn = await running(bot)
    open_turn = await play_to_guessing(bot, game)
    one, _ = await others(bot, game, open_turn)
    assert await flow.close_overdue(bot) == 1
    submit = guess_submit(bot, one, open_turn, "50")
    assert await handle_submit(submit)
    assert reply_of(submit) == GUESS_OVER == "Guessing for that turn has closed."
    assert await bot.repo.turn_guesses(open_turn["id"]) == []
    early = guess_submit(bot, one, {"id": open_turn["id"] + 1}, "50")
    assert await handle_submit(early)
    assert reply_of(early) == GUESS_OVER

"""The Headlines step buttons, both pop-ups, the submit listener and the vote menu."""

import logging
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest
from discord.ui.view import ViewStore
from headlines_fakes import (
    CHANNEL_ID,
    click_for,
    interaction_for,
    make_bot,
    pairings_of,
    play_to_voting,
    play_to_writing,
    reply_of,
    select_for,
    submit_for,
)

from gaminator.core.util import GameError
from gaminator.games.fiftyfifty import lobby as fiftyfifty_lobby
from gaminator.games.fiftyfifty import vote as fiftyfifty_vote
from gaminator.games.fiftyfifty import write as fiftyfifty_write
from gaminator.games.headlines import forms, lobby, vote
from gaminator.games.headlines.events import HeadlinesEvents
from gaminator.games.headlines.forms import (
    ANSWER_TITLE,
    HEADLINE_TITLE,
    AnswerButton,
    WriteButton,
    answer_form_id,
    answer_modal,
    handle_answer_click,
    handle_submit,
    handle_write_click,
    headline_form_id,
    value_from_submit,
)
from gaminator.games.headlines.services import game as flow
from gaminator.games.headlines.services.rules import (
    ANSWER_OVER,
    BAD_PICK,
    EMPTY_ANSWER,
    EMPTY_HEADLINE,
    GAME_OVER,
    LEFT,
    NEXT_ROUND,
    NO_QUOTE,
    NOT_PLAYING,
    OWN_HEADLINE,
    VOTE_OVER,
    WRITE_OVER,
)
from gaminator.games.headlines.vote import VoteSelect, handle_vote, vote_view
from gaminator.games.mostlikely import lobby as mostlikely_lobby
from gaminator.games.mostlikely import vote as mostlikely_vote
from gaminator.games.spectrum import guess as spectrum_guess
from gaminator.games.spectrum import lobby as spectrum_lobby
from gaminator.games.spectrum import turn as spectrum_turn

STAMPS = re.compile(r"<t:[0-9]+:R>")
DEALT = re.compile(r"question [0-9]+\?")


@pytest.fixture
async def bot(db):
    return make_bot(db)


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="x"), "x")


def warnings_in(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def is_private(interaction):
    """True when every reply to the interaction was sent so only its user sees it."""
    sent = [*interaction.followup.send.await_args_list]
    sent += interaction.response.send_message.await_args_list
    return bool(sent) and all(call.kwargs.get("ephemeral") is True for call in sent)


async def running(bot, users=(11, 12, 13)):
    """A started two-round game. Returns the game and its round, open for answers."""
    game = await bot.repo.create_game(1, "Friday", users[0], CHANNEL_ID)
    for uid in users:
        await bot.repo.join(game["id"], uid)
    await flow.begin_game(bot, game, 2)
    game = await bot.repo.get_game(game["id"])
    return game, await bot.repo.get_current_round(game["id"])


def message_of(bot, message_id):
    return next(m for m in bot.channel.messages if m.id == message_id)


def everything_public(bot):
    seen = []
    for message in bot.channel.messages:
        seen += [message.sent or "", message.content or ""]
        seen += [edit.get("content") or "" for edit in message.edits]
    return STAMPS.sub("", "\n".join(seen))


def answer_submit(bot, user_id, rnd, value, layout="label"):
    return submit_for(bot, user_id, answer_form_id(rnd["id"]), "answer", value, layout)


def headline_submit(bot, user_id, rnd, value, layout="label"):
    return submit_for(bot, user_id, headline_form_id(rnd["id"]), "headline", value, layout)


async def pick(bot, user_id, rnd, writer):
    """Pick the headline a user wrote from the vote menu. Returns the interaction."""
    target = (await pairings_of(bot, rnd))[writer]
    interaction = select_for(bot, user_id, rnd["id"], str(target["id"]))
    await handle_vote(interaction, rnd["id"], str(target["id"]))
    return interaction


def modal_of(click):
    click.response.send_modal.assert_awaited_once()
    (modal,) = click.response.send_modal.await_args.args
    return modal


def test_custom_ids_do_not_collide():
    ids = {
        "lobby": lobby.lobby_custom_id("join", 5),
        "answer": forms.answer_custom_id(5),
        "write": forms.write_custom_id(5),
        "vote": vote.vote_custom_id(5),
        "answerform": forms.answer_form_id(5),
        "headlineform": forms.headline_form_id(5),
    }
    templates = {
        "lobby": lobby.TEMPLATE,
        "answer": forms.ANSWER_TEMPLATE,
        "write": forms.WRITE_TEMPLATE,
        "vote": vote.VOTE_TEMPLATE,
    }
    foreign = (
        fiftyfifty_lobby.TEMPLATE,
        fiftyfifty_write.TEMPLATE,
        fiftyfifty_write.SUBMIT_TEMPLATE,
        fiftyfifty_vote.TEMPLATE,
        fiftyfifty_vote.GUESS_TEMPLATE,
        mostlikely_lobby.TEMPLATE,
        mostlikely_vote.TEMPLATE,
        spectrum_lobby.TEMPLATE,
        spectrum_turn.TARGET_TEMPLATE,
        spectrum_turn.WRITE_TEMPLATE,
        spectrum_turn.SUBMIT,
        spectrum_guess.TEMPLATE,
    )
    for kind, custom_id in ids.items():
        for name, template in templates.items():
            assert (template.fullmatch(custom_id) is not None) == (kind == name), (kind, name)
        assert (forms.SUBMIT.fullmatch(custom_id) is not None) == kind.endswith("form"), kind
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
        "spectrum:join:5",
        "spectrum:target:5",
        "spectrum:writeclue:5",
        "spectrum:guess:5",
        "spectrum:clueform:5",
        "spectrum:guessform:5",
    )
    for template in (*templates.values(), forms.SUBMIT):
        for custom_id in theirs:
            assert template.fullmatch(custom_id) is None, (template.pattern, custom_id)
    assert forms.ANSWER_TEMPLATE.fullmatch("headlines:answer:12")["round"] == "12"
    assert forms.WRITE_TEMPLATE.fullmatch("headlines:write:12")["round"] == "12"
    assert vote.VOTE_TEMPLATE.fullmatch("headlines:vote:12")["round"] == "12"
    assert vote.VOTE_TEMPLATE.fullmatch("headlines:vote:12:a") is None
    match = forms.SUBMIT.fullmatch("headlines:headlineform:7")
    assert match["kind"] == "headlineform" and match["round"] == "7"
    assert forms.SUBMIT.fullmatch("headlines:answerform:7")["kind"] == "answerform"
    assert forms.SUBMIT.fullmatch("headlines:answerform:x") is None
    assert len(f"headlines:headlineform:{2**63}") < 100


async def test_buttons_rebuild_from_custom_ids(bot):
    for cls, template, custom_id in (
        (AnswerButton, forms.ANSWER_TEMPLATE, "headlines:answer:42"),
        (WriteButton, forms.WRITE_TEMPLATE, "headlines:write:42"),
    ):
        item = await cls.from_custom_id(MagicMock(), MagicMock(), template.fullmatch(custom_id))
        assert item.round_id == 42 and item.item.custom_id == custom_id
        assert len(item.item.label) <= 80
        assert item.item.style is discord.ButtonStyle.primary
    assert [c.item.label for c in forms.answer_view(42).children] == ["Answer"]
    assert [c.item.label for c in forms.write_view(42).children] == ["Write headline"]
    assert forms.answer_view(42).timeout is None and forms.write_view(42).timeout is None


async def test_answer_click_opens_modal(bot):
    _, rnd = await running(bot)
    ctx = await bot.repo.round_context(rnd["id"], 11)
    click = click_for(bot, 11)
    await handle_answer_click(click, rnd["id"])
    modal = modal_of(click)
    click.response.defer.assert_not_awaited()
    click.response.send_message.assert_not_awaited()
    assert modal.title == ANSWER_TITLE and len(modal.title) <= 45
    assert modal.custom_id == answer_form_id(rnd["id"]) and modal.timeout is None
    assert modal.is_finished()
    block, label = modal.children
    assert isinstance(block, discord.ui.TextDisplay)
    assert block.content == flow.question_block(ctx["question"])
    assert isinstance(label, discord.ui.Label)
    assert label.text == "Your answer" and len(label.text) <= 45
    assert label.description == ctx["question"] and len(label.description) <= 100
    field = label.component
    assert isinstance(field, discord.ui.TextInput) and field.custom_id == "answer"
    assert (field.min_length, field.max_length, field.default) == (1, 100, None)
    assert field.style is discord.TextStyle.short and len(field.placeholder) <= 100

    await flow.submit_answer(bot, rnd["id"], 11, "lick the spoon")
    again = click_for(bot, 11)
    await handle_answer_click(again, rnd["id"])
    assert modal_of(again).children[1].component.default == "lick the spoon"
    other = click_for(bot, 12)
    await handle_answer_click(other, rnd["id"])
    theirs = modal_of(other).children[1]
    assert theirs.description != ctx["question"] and theirs.component.default is None


async def test_modal_text_fits_discord_limits():
    row = {"round_id": 2**40, "question": "q" * 100, "answer": "a" * 100}
    modal = answer_modal(row)
    assert len(modal.custom_id) <= 100 and len(modal.children) <= 5
    assert len(modal.children[0].content) <= 4000
    assert len(modal.children[1].description) <= 100
    quote = {"round_id": 2**40, "quote": "*" * 100, "headline": "h" * 120}
    modal = forms.headline_modal(quote)
    assert len(modal.title) <= 45 and len(modal.custom_id) <= 100
    assert len(modal.children[0].content) <= 4000
    assert len(modal.children[1].description) <= 100
    assert modal.children[1].component.max_length == 120


async def test_answer_click_refusals(bot):
    game, rnd = await running(bot, (11, 12, 13, 14))
    await bot.repo.join(game["id"], 15)
    leaver = await bot.repo.get_player(game["id"], 14)
    await bot.repo.leave(leaver["id"])
    cases = [(99, rnd["id"], NOT_PLAYING), (15, rnd["id"], NEXT_ROUND), (14, rnd["id"], LEFT)]
    cases.append((11, 9999, GAME_OVER))
    for user_id, round_id, expected in cases:
        click = click_for(bot, user_id)
        await handle_answer_click(click, round_id)
        assert reply_of(click) == expected
        assert is_private(click)
        click.response.send_modal.assert_not_awaited()
    await play_to_writing(bot, game)
    late = click_for(bot, 11)
    await handle_answer_click(late, rnd["id"])
    assert reply_of(late) == ANSWER_OVER and is_private(late)
    late.response.send_modal.assert_not_awaited()
    await flow.finish(bot, game)
    over = click_for(bot, 11)
    await handle_answer_click(over, rnd["id"])
    assert reply_of(over) == GAME_OVER and is_private(over)


async def test_answer_click_missed_window_logged(bot, caplog):
    _, rnd = await running(bot)
    click = click_for(bot, 11)
    click.response.send_modal.side_effect = http_error(discord.NotFound, 404)
    with caplog.at_level(logging.WARNING):
        await handle_answer_click(click, rnd["id"])
    (warning,) = warnings_in(caplog)
    assert "the click expired" in warning and f"round {rnd['id']}" in warning
    assert not DEALT.search(warning)
    click.response.send_message.assert_not_awaited()
    failing = click_for(bot, 11)
    failing.response.send_modal.side_effect = GameError("nope")
    await handle_answer_click(failing, rnd["id"])
    assert reply_of(failing) == "nope"
    again = click_for(bot, 11)
    again.response.send_modal.side_effect = GameError("nope")
    await handle_write_click(again, rnd["id"])
    assert reply_of(again) == WRITE_OVER


def test_value_from_submit():
    def payload(layout, items):
        if layout == "row":
            return {"components": [{"type": 1, "components": [item]} for item in items]}
        return {"components": [{"type": 10}] + [{"type": 18, "component": i} for i in items]}

    answer = {"type": 4, "custom_id": "answer", "value": "spoon"}
    other = {"type": 4, "custom_id": "headline", "value": "Shock"}
    for layout in ("label", "row"):
        assert value_from_submit(payload(layout, [answer]), "answer") == "spoon"
        assert value_from_submit(payload(layout, [other, answer]), "answer") == "spoon"
        assert value_from_submit(payload(layout, [other, answer]), "headline") == "Shock"
        assert value_from_submit(payload(layout, [other]), "answer") is None
        assert value_from_submit(payload(layout, []), "answer") is None
        number = {"type": 4, "custom_id": "answer", "value": 7}
        assert value_from_submit(payload(layout, [number]), "answer") is None
        select = {"type": 3, "custom_id": "answer", "value": "spoon"}
        assert value_from_submit(payload(layout, [select]), "answer") is None
    assert value_from_submit({"components": "nope"}, "answer") is None
    assert value_from_submit({"components": ["nope", None, 7]}, "answer") is None
    assert value_from_submit({"components": [{"type": 18, "component": "x"}]}, "answer") is None
    assert value_from_submit({"components": [{"type": 1, "components": "x"}]}, "answer") is None
    assert value_from_submit({}, "answer") is None
    assert value_from_submit(None, "answer") is None
    assert value_from_submit("text", "answer") is None


async def test_listener_ignores_other_interactions(bot):
    _, rnd = await running(bot)
    before = len(bot.channel.messages)
    click = interaction_for(bot, 11)
    click.type = discord.InteractionType.component
    click.data = {"custom_id": answer_form_id(rnd["id"])}
    assert not await handle_submit(click)
    for custom_id in (
        "spectrum:clueform:1",
        "spectrum:guessform:1",
        "fiftyfifty:blank:1",
        "headlines:answer:1",
        "headlines:vote:1",
        "headlines:answerform:",
        "",
        7,
    ):
        foreign = submit_for(bot, 11, custom_id, "answer", "spoon")
        assert not await handle_submit(foreign)
        foreign.response.defer.assert_not_awaited()
        foreign.followup.send.assert_not_awaited()
    odd = interaction_for(bot, 11)
    odd.type = discord.InteractionType.modal_submit
    odd.data = None
    assert not await handle_submit(odd)
    assert len(bot.channel.messages) == before
    assert (await bot.repo.round_context(rnd["id"], 11))["answer"] is None


async def test_submit_with_no_modal_in_memory(bot):
    _, rnd = await running(bot)
    restarted = make_bot(bot.repo.db)
    restarted.channel = bot.channel
    restarted.get_channel = bot.get_channel
    cog = SimpleNamespace(bot=restarted)
    listener = HeadlinesEvents.on_interaction
    for uid in (11, 12, 13):
        submit = answer_submit(restarted, uid, rnd, f"answer of a{uid}")
        await listener(cog, submit)
        assert reply_of(submit).startswith(f'Saved. Your answer: "answer of a{uid}".')
    rnd = await bot.repo.get_round(rnd["id"])
    assert rnd["answer_closed_at"] and rnd["write_message_id"]
    written = headline_submit(restarted, 11, rnd, "Shock claim", layout="row")
    await listener(cog, written)
    assert reply_of(written).startswith("Saved. Your headline: **Shock claim**.")
    assert (await bot.repo.round_context(rnd["id"], 11))["headline"] == "Shock claim"


async def test_modals_are_never_stored(bot):
    game, rnd = await running(bot)
    store = ViewStore(MagicMock())

    def send_through(click):
        async def send_modal(modal):
            if not modal.is_finished():
                store.add_view(modal)

        click.response.send_modal.side_effect = send_modal
        return click

    await handle_answer_click(send_through(click_for(bot, 11)), rnd["id"])
    await play_to_writing(bot, game)
    await handle_write_click(send_through(click_for(bot, 11)), rnd["id"])
    assert store._modals == {}
    built = answer_modal(await bot.repo.round_context(rnd["id"], 11))
    assert type(built) is discord.ui.Modal


async def test_answer_submit_saves_and_changes(bot):
    _, rnd = await running(bot)
    answer_message = message_of(bot, rnd["answer_message_id"])
    first = answer_submit(bot, 11, rnd, "  lick   the spoon ")
    assert await handle_submit(first)
    assert reply_of(first) == (
        'Saved. Your answer: "lick the spoon". You can change it until answering closes.'
    )
    assert "1/3 answered." in answer_message.content and answer_message.view is not None
    changed = answer_submit(bot, 11, rnd, "x" * 150, layout="row")
    assert await handle_submit(changed)
    assert (await bot.repo.round_context(rnd["id"], 11))["answer"] == "x" * 100
    assert "1/3 answered." in answer_message.content and len(answer_message.edits) == 1
    assert "lick the spoon" not in everything_public(bot)
    assert "xxxx" not in everything_public(bot)


async def test_answer_submit_empty(bot):
    _, rnd = await running(bot)
    for value in ("", "   \n ", None):
        submit = answer_submit(bot, 11, rnd, value)
        assert await handle_submit(submit)
        assert reply_of(submit) == EMPTY_ANSWER and is_private(submit)
    assert (await bot.repo.round_context(rnd["id"], 11))["answer"] is None
    assert "0/3 answered." in message_of(bot, rnd["answer_message_id"]).content


async def test_answer_submit_after_close(bot):
    game, rnd = await running(bot)
    await play_to_writing(bot, game, {11: "in time", 12: "also in time"})
    late = answer_submit(bot, 13, rnd, "too late")
    assert await handle_submit(late)
    assert reply_of(late) == ANSWER_OVER
    assert (await bot.repo.round_context(rnd["id"], 13))["answer"] is None
    changed = answer_submit(bot, 11, rnd, "changed my mind")
    assert await handle_submit(changed)
    assert reply_of(changed) == ANSWER_OVER
    assert (await bot.repo.round_context(rnd["id"], 11))["answer"] == "in time"


async def test_answer_submit_after_leave_refused(bot):
    game, rnd = await running(bot, (11, 12, 13, 14))
    await flow.leave_game(bot, game, 14)
    submit = answer_submit(bot, 14, rnd, "I left")
    assert await handle_submit(submit)
    assert reply_of(submit) == LEFT
    outsider = answer_submit(bot, 99, rnd, "never joined")
    assert await handle_submit(outsider)
    assert reply_of(outsider) == NOT_PLAYING
    await flow.join_game(bot, game, 15)
    joiner = answer_submit(bot, 15, rnd, "just joined")
    assert await handle_submit(joiner)
    assert reply_of(joiner) == NEXT_ROUND
    rows = await bot.repo._all("SELECT answer FROM headlines_answers")
    assert all(r["answer"] is None for r in rows)


async def test_submit_saved_when_defer_fails(bot, caplog):
    _, rnd = await running(bot)
    submit = answer_submit(bot, 11, rnd, "lick the spoon")
    submit.response.defer.side_effect = http_error(discord.NotFound, 404)
    submit.response.is_done = lambda: False
    with caplog.at_level(logging.WARNING):
        assert await handle_submit(submit)
    assert (await bot.repo.round_context(rnd["id"], 11))["answer"] == "lick the spoon"
    assert any("could not defer a answerform submit" in w for w in warnings_in(caplog))
    assert reply_of(submit).startswith("Saved.")
    second = answer_submit(bot, 12, rnd, "hum a tune")
    second.response.defer.side_effect = http_error(discord.NotFound, 404)
    second.response.is_done = lambda: False
    second.response.send_message.side_effect = http_error(discord.NotFound, 404)
    with caplog.at_level(logging.WARNING):
        assert await handle_submit(second)
    assert (await bot.repo.round_context(rnd["id"], 12))["answer"] == "hum a tune"
    assert any("could not answer a answerform submit" in w for w in warnings_in(caplog))
    logged = "\n".join(warnings_in(caplog))
    assert "hum a tune" not in logged and "lick the spoon" not in logged


async def test_submit_unexpected_error_replies(bot, monkeypatch, caplog):
    _, rnd = await running(bot)

    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    with monkeypatch.context() as patch:
        patch.setattr(flow, "submit_answer", broken)
        submit = answer_submit(bot, 11, rnd, "my secret words")
        assert await handle_submit(submit)
    assert reply_of(submit) == "Something went wrong; the logs have details."
    assert is_private(submit)
    assert any("answerform submit failed for round" in r.getMessage() for r in caplog.records)
    assert "my secret words" not in "\n".join(r.getMessage() for r in caplog.records)

    async def refused(*args, **kwargs):
        raise GameError("not now")

    with monkeypatch.context() as patch:
        patch.setattr(flow, "submit_headline", refused)
        written = headline_submit(bot, 11, rnd, "Shock")
        assert await handle_submit(written)
    assert reply_of(written) == "not now"
    monkeypatch.setattr(flow, "close_step_if_done", broken)
    again = answer_submit(bot, 11, rnd, "spoon")
    assert await handle_submit(again)
    assert reply_of(again).startswith("Saved.")
    assert any("after a submit failed" in r.getMessage() for r in caplog.records)
    voted = select_for(bot, 11, rnd["id"], "1")
    await handle_vote(voted, rnd["id"], "1")
    assert reply_of(voted) == VOTE_OVER
    assert any("after a vote failed" in r.getMessage() for r in caplog.records)


async def test_submit_replies_are_ephemeral(bot):
    game, rnd = await running(bot)
    await flow.join_game(bot, game, 14)
    submits = [
        answer_submit(bot, 99, rnd, "outsider"),
        answer_submit(bot, 14, rnd, "late joiner"),
        answer_submit(bot, 11, rnd, ""),
        headline_submit(bot, 11, rnd, "too early"),
        answer_submit(bot, 11, rnd, "one"),
        answer_submit(bot, 11, rnd, "two"),
        answer_submit(bot, 12, rnd, "three"),
        answer_submit(bot, 13, rnd, "four"),
        answer_submit(bot, 13, rnd, "five"),
        headline_submit(bot, 99, rnd, "outsider"),
        headline_submit(bot, 14, rnd, "late joiner"),
        headline_submit(bot, 11, rnd, ""),
        headline_submit(bot, 11, rnd, "Shock one"),
        headline_submit(bot, 12, rnd, "Shock two"),
        headline_submit(bot, 13, rnd, "Shock three"),
        headline_submit(bot, 13, rnd, "Shock four"),
    ]
    for submit in submits:
        assert await handle_submit(submit)
        submit.response.defer.assert_awaited_once_with(ephemeral=True)
        submit.response.send_modal.assert_not_called()
        assert is_private(submit), reply_of(submit)
    assert reply_of(submits[3]) == WRITE_OVER
    assert reply_of(submits[8]) == ANSWER_OVER
    assert reply_of(submits[11]) == EMPTY_HEADLINE
    assert reply_of(submits[-1]) == WRITE_OVER
    assert (await bot.repo.get_round(rnd["id"]))["vote_deadline_at"]


async def test_write_click_opens_modal_with_quote_only(bot):
    game, rnd = await running(bot)
    answers = {11: "lick the spoon", 12: "hum loudly", 13: "nap at noon"}
    rnd = await play_to_writing(bot, game, answers)
    pairs = await pairings_of(bot, rnd)
    for writer, row in pairs.items():
        ctx = await bot.repo.round_context(rnd["id"], writer)
        click = click_for(bot, writer)
        await handle_write_click(click, rnd["id"])
        modal = modal_of(click)
        click.response.defer.assert_not_awaited()
        click.response.send_message.assert_not_awaited()
        assert modal.title == HEADLINE_TITLE and len(modal.title) <= 45
        assert modal.custom_id == headline_form_id(rnd["id"]) and modal.is_finished()
        block, label = modal.children
        quote = answers[row["quoted_user_id"]]
        assert quote != answers[writer]
        assert block.content == flow.quote_block(quote)
        assert label.text == "Your headline" and label.description == quote
        field = label.component
        assert field.custom_id == "headline" and field.default is None
        assert (field.min_length, field.max_length) == (1, 120)
        shown = f"{modal.title}\n{block.content}\n{label.text}\n{label.description}"
        shown += field.placeholder
        quoted = await bot.repo.round_context(rnd["id"], row["quoted_user_id"])
        assert quoted["question"] not in shown and ctx["question"] not in shown
        assert not DEALT.search(shown)
        assert f"U{row['quoted_user_id']}" not in shown
        assert str(row["quoted_user_id"]) not in shown
    await flow.submit_headline(bot, rnd["id"], 11, "Local shock")
    again = click_for(bot, 11)
    await handle_write_click(again, rnd["id"])
    assert modal_of(again).children[1].component.default == "Local shock"


async def test_write_click_no_quote_note(bot):
    game, rnd = await running(bot)
    rnd = await play_to_writing(bot, game, {11: "lick the spoon", 12: "hum loudly"})
    assert sorted(await pairings_of(bot, rnd)) == [11, 12]
    click = click_for(bot, 13)
    await handle_write_click(click, rnd["id"])
    assert (
        reply_of(click)
        == NO_QUOTE
        == ("You don't have a quote to write for this round. Sit back, the headlines are coming.")
    )
    assert is_private(click)
    click.response.send_modal.assert_not_awaited()
    submit = headline_submit(bot, 13, rnd, "I'll write one anyway")
    assert await handle_submit(submit)
    assert reply_of(submit) == NO_QUOTE
    assert await bot.repo.write_progress(rnd["id"]) == (0, 2)


async def test_write_click_refusals(bot):
    game, rnd = await running(bot, (11, 12, 13, 14))
    early = click_for(bot, 11)
    await handle_write_click(early, rnd["id"])
    assert reply_of(early) == WRITE_OVER and is_private(early)
    await flow.join_game(bot, game, 15)
    rnd = await play_to_writing(bot, game)
    await flow.leave_game(bot, game, 14)
    cases = [(99, rnd["id"], NOT_PLAYING), (15, rnd["id"], NEXT_ROUND), (14, rnd["id"], LEFT)]
    cases.append((11, 9999, GAME_OVER))
    for user_id, round_id, expected in cases:
        click = click_for(bot, user_id)
        await handle_write_click(click, round_id)
        assert reply_of(click) == expected
        assert is_private(click)
        click.response.send_modal.assert_not_awaited()
    await play_to_voting(bot, game)
    late = click_for(bot, 11)
    await handle_write_click(late, rnd["id"])
    assert reply_of(late) == WRITE_OVER and is_private(late)
    late.response.send_modal.assert_not_awaited()


async def test_headline_submit_saves_and_changes(bot):
    game, rnd = await running(bot)
    rnd = await play_to_writing(bot, game)
    write_message = message_of(bot, rnd["write_message_id"])
    first = headline_submit(bot, 11, rnd, "  Local   woman's shock ")
    assert await handle_submit(first)
    assert reply_of(first) == (
        "Saved. Your headline: **Local woman's shock**. You can change it until headlines close."
    )
    assert "1/3 headlines in." in write_message.content and write_message.view is not None
    changed = headline_submit(bot, 11, rnd, "h" * 150, layout="row")
    assert await handle_submit(changed)
    assert (await bot.repo.round_context(rnd["id"], 11))["headline"] == "h" * 120
    assert "1/3 headlines in." in write_message.content and len(write_message.edits) == 1
    assert "Local woman's shock" not in everything_public(bot)
    empty = headline_submit(bot, 12, rnd, "  ")
    assert await handle_submit(empty)
    assert reply_of(empty) == EMPTY_HEADLINE
    stranger = headline_submit(bot, 11, {"id": 9999}, "nothing there")
    assert await handle_submit(stranger)
    assert reply_of(stranger) == GAME_OVER


async def test_last_headline_reveals_after_the_reply(bot):
    game, rnd = await running(bot)
    rnd = await play_to_writing(bot, game)
    for uid in (11, 12):
        assert await handle_submit(headline_submit(bot, uid, rnd, f"headline by w{uid}"))
    last = headline_submit(bot, 13, rnd, "headline by w13")
    seen = {}

    async def on_send(message):
        seen.setdefault("replied", last.followup.send.await_count)

    bot.channel.on_send = on_send
    assert await handle_submit(last)
    assert seen["replied"] == 1
    assert (await bot.repo.get_round(rnd["id"]))["vote_message_id"]


async def test_vote_select_defers_first(bot):
    game, rnd = await running(bot)
    rnd = await play_to_voting(bot, game)
    target = (await pairings_of(bot, rnd))[12]
    click = select_for(bot, 11, rnd["id"], str(target["id"]))
    order = []
    real = bot.repo.round_context

    async def reading(*args):
        order.append("read")
        return await real(*args)

    async def deferring(**kwargs):
        order.append(("defer", kwargs))

    bot.repo.round_context = reading
    click.response.defer.side_effect = deferring
    await handle_vote(click, rnd["id"], str(target["id"]))
    assert order[0] == ("defer", {"ephemeral": True}) and "read" in order[1:]
    click.response.send_modal.assert_not_called()
    assert reply_of(click) == flow.vote_saved_text(target["position"])

    failing = select_for(bot, 12, rnd["id"], "abc")
    failing.response.defer.side_effect = http_error(discord.NotFound, 404)
    failing.response.is_done = lambda: False
    await handle_vote(failing, rnd["id"], "abc")
    assert reply_of(failing) == BAD_PICK and is_private(failing)


async def test_vote_select_rebuilds_from_message(bot):
    game, rnd = await running(bot)
    rnd = await play_to_voting(bot, game)
    (menu,) = message_of(bot, rnd["vote_message_id"]).view.children
    assert isinstance(menu, VoteSelect)
    assert menu.item.placeholder == "Pick the best headline"
    assert (menu.item.min_values, menu.item.max_values) == (1, 1)
    match = VoteSelect.__discord_ui_compiled_template__.fullmatch(menu.item.custom_id)
    rebuilt = await VoteSelect.from_custom_id(interaction_for(bot, 11), menu.item, match)
    assert rebuilt.round_id == rnd["id"] and rebuilt.item.custom_id == menu.item.custom_id
    assert [o.value for o in rebuilt.item.options] == [o.value for o in menu.item.options]
    assert [o.label for o in rebuilt.item.options] == [o.label for o in menu.item.options]
    assert len(rebuilt.item.options) == 3

    restarted = make_bot(bot.repo.db)
    restarted.channel = bot.channel
    restarted.get_channel = bot.get_channel
    target = (await pairings_of(bot, rnd))[12]
    click = interaction_for(restarted, 11)
    rebuilt.item._values = [str(target["id"])]
    await rebuilt.callback(click)
    assert reply_of(click) == flow.vote_saved_text(target["position"])
    assert (await bot.repo.round_context(rnd["id"], 11))["my_vote"] == target["id"]
    empty = interaction_for(restarted, 12)
    rebuilt.item._values = []
    await rebuilt.callback(empty)
    assert reply_of(empty) == BAD_PICK


def test_vote_view_options():
    rows = [
        {"id": 31, "position": 1, "headline": "First", "answer": "one"},
        {"id": 30, "position": 2, "headline": "h" * 120, "answer": "a" * 100},
    ]
    (menu,) = vote_view(8, rows).children
    assert menu.item.custom_id == "headlines:vote:8"
    assert [(o.label, o.value, o.description) for o in menu.item.options] == [
        ("1. First", "31", "one"),
        ("2. " + "h" * 96 + "…", "30", "a" * 100),
    ]
    twelve = [{"id": n, "position": n, "headline": "h", "answer": "a"} for n in range(1, 13)]
    assert len(vote_view(8, twelve).children[0].item.options) == 12
    assert vote_view(8, rows).timeout is None


async def test_vote_saves_and_changes(bot):
    game, rnd = await running(bot)
    rnd = await play_to_voting(bot, game)
    pairs = await pairings_of(bot, rnd)
    vote_message = message_of(bot, rnd["vote_message_id"])
    first = await pick(bot, 11, rnd, 12)
    assert reply_of(first) == (
        f"Your vote is on headline **{pairs[12]['position']}**. "
        "You can change it until voting closes."
    )
    assert "1/3 voted." in vote_message.content and vote_message.view is not None
    changed = await pick(bot, 11, rnd, 13)
    assert reply_of(changed) == flow.vote_saved_text(pairs[13]["position"])
    assert (await bot.repo.round_context(rnd["id"], 11))["my_vote"] == pairs[13]["id"]
    assert "1/3 voted." in vote_message.content and len(vote_message.edits) == 1
    assert await bot.repo.vote_progress(rnd["id"]) == (1, 3)


async def test_vote_own_headline_refused(bot):
    game, rnd = await running(bot)
    rnd = await play_to_voting(bot, game)
    own = await pick(bot, 11, rnd, 11)
    assert (
        reply_of(own)
        == OWN_HEADLINE
        == ("You wrote that headline, so you can't vote for it. Pick another one.")
    )
    assert is_private(own)
    assert (await bot.repo.round_context(rnd["id"], 11))["my_vote"] is None
    assert await bot.repo.vote_progress(rnd["id"]) == (0, 3)


async def test_vote_for_own_quote_allowed(bot):
    game, rnd = await running(bot)
    rnd = await play_to_voting(bot, game)
    pairs = await pairings_of(bot, rnd)
    writer = next(uid for uid, row in pairs.items() if row["quoted_user_id"] == 11)
    click = await pick(bot, 11, rnd, writer)
    assert reply_of(click) == flow.vote_saved_text(pairs[writer]["position"])
    assert (await bot.repo.round_context(rnd["id"], 11))["my_vote"] == pairs[writer]["id"]


async def test_vote_bad_value(bot):
    game, _ = await running(bot, (11, 12, 13))
    first = await play_to_voting(bot, game)
    old = (await pairings_of(bot, first))[12]
    for voter, writer in ((11, 12), (12, 11), (13, 11)):
        await pick(bot, voter, first, writer)
    second = await bot.repo.get_current_round(game["id"])
    assert second["number"] == 2
    second = await play_to_voting(bot, game)
    for value in ("abc", "", "-1", "1.5", "٣", "9" * 40, "9999", str(old["id"])):
        click = select_for(bot, 11, second["id"], value)
        await handle_vote(click, second["id"], value)
        assert reply_of(click) == BAD_PICK == "Pick a headline from the menu.", value
        assert is_private(click)
    assert await bot.repo.vote_progress(second["id"]) == (0, 3)


async def test_vote_after_close(bot):
    game, rnd = await running(bot)
    rnd = await play_to_writing(bot, game)
    early = select_for(bot, 11, rnd["id"], "1")
    await handle_vote(early, rnd["id"], "1")
    assert reply_of(early) == VOTE_OVER
    rnd = await play_to_voting(bot, game)
    for voter, writer in ((11, 12), (12, 11), (13, 11)):
        await pick(bot, voter, rnd, writer)
    assert (await bot.repo.get_round(rnd["id"]))["outcome"] == "voted"
    late = await pick(bot, 11, rnd, 13)
    assert reply_of(late) == VOTE_OVER == "Voting for that round has closed."
    assert (await bot.repo.round_context(rnd["id"], 11))["my_vote"] != (
        (await pairings_of(bot, rnd))[13]["id"]
    )
    await flow.finish(bot, game)
    over = await pick(bot, 11, rnd, 13)
    assert reply_of(over) == GAME_OVER


async def test_vote_late_joiner_refused(bot):
    game, rnd = await running(bot, (11, 12, 13, 14))
    await flow.join_game(bot, game, 15)
    rnd = await play_to_voting(bot, game)
    await flow.leave_game(bot, game, 14)
    joiner = await pick(bot, 15, rnd, 11)
    assert reply_of(joiner) == NEXT_ROUND
    leaver = await pick(bot, 14, rnd, 11)
    assert reply_of(leaver) == LEFT
    outsider = await pick(bot, 99, rnd, 11)
    assert reply_of(outsider) == NOT_PLAYING
    assert await bot.repo.vote_progress(rnd["id"]) == (0, 3)


async def test_vote_replies_are_ephemeral(bot):
    game, rnd = await running(bot)
    await flow.join_game(bot, game, 14)
    rnd = await play_to_voting(bot, game)
    clicks = [
        await pick(bot, 99, rnd, 11),
        await pick(bot, 14, rnd, 11),
        await pick(bot, 11, rnd, 11),
        await pick(bot, 11, rnd, 12),
        await pick(bot, 11, rnd, 13),
        await pick(bot, 12, rnd, 11),
        await pick(bot, 13, rnd, 11),
        await pick(bot, 13, rnd, 12),
    ]
    for click in clicks:
        click.response.defer.assert_awaited_once_with(ephemeral=True)
        assert is_private(click), reply_of(click)
    assert reply_of(clicks[-1]) == VOTE_OVER
    public = everything_public(bot)
    assert "Your vote is on" not in public

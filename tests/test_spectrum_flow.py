"""Spectrum game flow, driven through a fake bot and channel."""

import asyncio
import re
from types import SimpleNamespace

import discord
import pytest
from spectrum_fakes import (
    CHANNEL_ID,
    add_card,
    card_names,
    interaction_for,
    make_bot,
    play_to_guessing,
    submit_for,
)

from gaminator.core.messaging import NO_MENTIONS
from gaminator.core.util import GameError, NotGameAdmin
from gaminator.games.spectrum.commands.group import require_host_or_admin
from gaminator.games.spectrum.guess import GuessButton
from gaminator.games.spectrum.services import game as flow
from gaminator.games.spectrum.services.rules import GIVER_SITS_OUT, JOINED_LATE
from gaminator.games.spectrum.turn import TargetButton, WriteClueButton, handle_submit

FOUR = (11, 12, 13, 14)
DRUM = flow.drumroll_text()
TARGET = 77
STAMPS = re.compile(r"<t:[0-9]+:R>|<@[0-9]+>")


@pytest.fixture
async def bot(db):
    return make_bot(db)


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="x"), "x")


async def lobby(bot, users=(11, 12, 13)):
    game = await bot.repo.create_game(1, "Friday", users[0], CHANNEL_ID)
    for uid in users:
        await bot.repo.join(game["id"], uid)
    return game


async def set_targets(bot, value=TARGET):
    async with bot.repo.db.transaction() as conn:
        await conn.execute("UPDATE spectrum_turns SET target = ?", (value,))


async def running(bot, users=(11, 12, 13), rounds=1):
    """A started game with every target set to TARGET. The first turn is waiting for its clue."""
    game = await lobby(bot, users)
    await flow.begin_game(bot, game, rounds)
    await set_targets(bot)
    return await bot.repo.get_game(game["id"])


async def current(bot, game):
    rnd = await bot.repo.get_current_round(game["id"])
    return await bot.repo.current_turn(rnd["id"])


async def turns_of(bot, game):
    rnd = await bot.repo.get_current_round(game["id"])
    return await bot.repo._all(
        "SELECT t.*, p.user_id AS giver_user_id FROM spectrum_turns t "
        "JOIN spectrum_players p ON p.id = t.giver_id WHERE t.round_id = ? ORDER BY t.position",
        (rnd["id"],),
    )


async def others(bot, game, turn):
    """User ids of the players still in the game, other than the turn's giver."""
    players = await bot.repo.list_players(game["id"], active_only=True)
    return [p["user_id"] for p in players if p["user_id"] != turn["giver_user_id"]]


async def guess(bot, turn, uid, value):
    """Guess as a pop-up submit does: save, then close the turn when everyone is in."""
    reply = await flow.submit_guess(bot, turn["id"], uid, str(value))
    await flow.close_if_done(bot, turn["id"])
    return reply


async def play_turn(bot, game, values=None):
    """Give the open turn a clue and a guess from every other player. Returns the turn."""
    turn = await play_to_guessing(bot, game)
    for uid in await others(bot, game, turn):
        await guess(bot, turn, uid, (values or {}).get(uid, 50))
    return turn


def message_of(bot, message_id):
    return next(m for m in bot.channel.messages if m.id == message_id)


def reveals(bot):
    return [t for t in bot.channel.texts if "## 🎯 The target was" in t]


def turn_posts(bot):
    """The messages that opened a turn, as they were first sent."""
    return [m.sent for m in bot.channel.messages if "you give the clue" in (m.sent or "")]


def recaps(bot):
    return [t for t in bot.channel.texts if "is over after" in t]


def everything_public(bot):
    """Every text the channel ever showed: each message as sent or last edited, and every edit."""
    seen = []
    for message in bot.channel.messages:
        seen += [message.sent or "", message.content or ""]
        seen += [edit.get("content") or "" for edit in message.edits]
    return STAMPS.sub("", "\n".join(seen))


async def outcome(bot, turn):
    return (await bot.repo.get_turn(turn["id"]))["outcome"]


async def status(bot, game):
    return (await bot.repo.get_game(game["id"]))["status"]


async def test_begin_game_posts_first_turn(bot):
    game = await lobby(bot)
    card = await add_card(bot, game)
    reply = await flow.begin_game(bot, game, 1)
    assert reply == "Spectrum game **Friday** has started with 3 players and 1 round."
    game = await bot.repo.get_game(game["id"])
    turn = await current(bot, game)
    assert turn["position"] == 1 and turn["turn_count"] == 3 and turn["clue"] is None
    assert len(bot.channel.messages) == 2
    posted = bot.channel.messages[-1]
    assert posted.id == turn["turn_message_id"]
    assert posted.content == flow.turn_text(game, turn, turn["giver_user_id"])
    assert "Turn 1 of 3 (round 1 of 1)" in posted.content
    assert card.embed.title == "🌈 Friday: in progress"
    assert card_names(card) == ["U11", "U12", "U13"]
    assert [c.item.label for c in card.view.children] == ["Join", "Leave", "Rules"]


async def test_turn_post_pings_only_giver(db):
    bot = make_bot(db, clue_seconds=0)
    game = await running(bot)
    first = await current(bot, game)
    await play_turn(bot, game)
    assert await flow.close_overdue(bot) == 1
    await play_turn(bot, game)
    assert recaps(bot) and any("⏭️" in t for t in bot.channel.texts)
    pinging = [m for m in bot.channel.messages if m.kwargs["allowed_mentions"] is not NO_MENTIONS]
    assert len(pinging) == 3
    givers = [t["giver_user_id"] for t in await turns_of(bot, game)]
    for message, giver in zip(pinging, givers, strict=True):
        assert isinstance(message.kwargs["view"].children[0], TargetButton)
        allowed = message.kwargs["allowed_mentions"]
        assert [user.id for user in allowed.users] == [giver]
        assert allowed.everyone is False and allowed.roles is False
        assert allowed.replied_user is False
    assert first["giver_user_id"] == givers[0]
    opening = flow.turn_text(game, first, givers[0])
    assert f"<@{givers[0]}>, you give the clue." in opening
    for message in bot.channel.messages:
        if message in pinging:
            continue
        assert "<@" not in message.content
    for message in bot.channel.messages:
        for edit in message.edits:
            assert "<@" not in (edit.get("content") or "")
            if "content" in edit:
                assert edit["allowed_mentions"] is NO_MENTIONS


async def test_turn_message_has_both_buttons(bot):
    game = await running(bot)
    turn = await current(bot, game)
    view = message_of(bot, turn["turn_message_id"]).view
    assert [type(c) for c in view.children] == [TargetButton, WriteClueButton]
    assert [c.item.label for c in view.children] == ["Show my target", "Write clue"]
    assert [c.item.custom_id for c in view.children] == [
        f"spectrum:target:{turn['id']}",
        f"spectrum:writeclue:{turn['id']}",
    ]
    assert view.timeout is None


async def test_begin_game_refusals(bot):
    game = await lobby(bot, users=(11, 12))
    with pytest.raises(GameError, match="Need at least 3 players to start; 2 joined so far."):
        await flow.begin_game(bot, game, 1)
    await bot.repo.join(game["id"], 13)
    await flow.begin_game(bot, game, 1)
    with pytest.raises(GameError, match="The game has already started."):
        await flow.begin_game(bot, game, 1)
    with pytest.raises(GameError, match="The game has already started."):
        await flow.begin_game(bot, await bot.repo.get_game(game["id"]), 1)
    two = make_bot(bot.repo.db, min_players=2)
    other = await two.repo.create_game(2, "Pair", 21, CHANNEL_ID)
    for uid in (21, 22):
        await two.repo.join(other["id"], uid)
    assert "has started with 2 players" in await flow.begin_game(two, other, 1)


async def test_double_start_one_round(bot):
    game = await lobby(bot)
    results = await asyncio.gather(
        flow.begin_game(bot, game, 1), flow.begin_game(bot, game, 2), return_exceptions=True
    )
    assert sum(isinstance(r, GameError) for r in results) == 1
    rounds = await bot.repo._all("SELECT * FROM spectrum_rounds")
    assert len(rounds) == 1
    assert len(await turns_of(bot, game)) == 3
    assert sum("you give the clue" in t for t in bot.channel.texts) == 1


async def test_everyone_guessed_reveals(bot):
    game = await running(bot)
    turn = await play_to_guessing(bot, game)
    one, two = await others(bot, game, turn)
    posted = message_of(bot, turn["guess_message_id"])
    assert isinstance(posted.view.children[0], GuessButton)
    assert "0/2 guesses in." in posted.content
    assert (
        await guess(bot, turn, one, 70)
        == "Your guess is **70**. You can change it until the turn closes."
    )
    assert "1/2 guesses in." in posted.content and not reveals(bot)
    await guess(bot, turn, one, 71)
    assert "1/2 guesses in." in posted.content and not reveals(bot)
    await guess(bot, turn, two, 12)
    assert posted.content == flow.guess_closed_text(game, turn, f"U{turn['giver_user_id']}")
    assert posted.view is None
    (reveal,) = reveals(bot)
    assert bot.channel.texts.index(DRUM) + 1 == bot.channel.texts.index(reveal)
    lines = reveal.split("\n")
    assert lines[1] == "## 🎯 The target was 77"
    assert f"` 12` **U{two}**: 0" in lines and f"` 71` **U{one}**: 82" in lines
    assert lines.index("` 77` 🎯 target") == lines.index(f"` 71` **U{one}**: 82") + 1
    assert (
        lines[-1]
        == f"**U{turn['giver_user_id']}** scores **41** for the clue, the guessers' average."
    )
    assert await outcome(bot, turn) == "revealed"
    after = await current(bot, game)
    assert after["position"] == 2 and after["clue"] is None


async def test_reply_before_reveal(bot):
    game = await running(bot)
    turn = await play_to_guessing(bot, game)
    one, two = await others(bot, game, turn)
    await guess(bot, turn, one, 40)
    submit = submit_for(bot, two, f"spectrum:guessform:{turn['id']}", "guess", "60")
    seen = []
    submit.followup.send.side_effect = lambda *args, **kwargs: seen.append(
        (args[0], DRUM in bot.channel.texts, len(reveals(bot)))
    )
    assert await handle_submit(submit)
    assert seen == [("Your guess is **60**. You can change it until the turn closes.", False, 0)]
    assert DRUM in bot.channel.texts and len(reveals(bot)) == 1


async def test_reveal_pause(bot, monkeypatch):
    game = await running(bot)
    turn = await play_to_guessing(bot, game)
    seen = []

    async def pause(seconds):
        closed = (await bot.repo.get_turn(turn["id"]))["closed_at"]
        seen.append((seconds, bool(closed), bot.channel.texts[-1], len(reveals(bot))))

    monkeypatch.setattr(flow, "REVEAL_PAUSE", 3)
    monkeypatch.setattr(flow.asyncio, "sleep", pause)
    assert await flow.close_turn(bot, game, turn, cause="deadline")
    assert seen == [(3, True, DRUM, 0)]
    assert len(reveals(bot)) == 1
    assert bot.channel.texts.index(DRUM) + 1 == bot.channel.texts.index(reveals(bot)[0])
    assert not await flow.close_turn(bot, game, turn, cause="deadline")
    assert len(reveals(bot)) == 1


async def test_failed_reveal_still_moves_on(bot, monkeypatch, caplog):
    game = await running(bot)
    turn = await play_to_guessing(bot, game)

    async def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(flow, "reveal_for", broken)
    assert await flow.close_turn(bot, game, turn)
    assert not reveals(bot) and await outcome(bot, turn) == "revealed"
    assert any("reveal of turn" in r.getMessage() for r in caplog.records)
    assert (await current(bot, game))["position"] == 2


async def test_last_turn_ends_round_and_recap(bot):
    game = await running(bot)
    card = await add_card(bot, game)
    for _ in range(3):
        assert not recaps(bot)
        await play_turn(bot, game, {11: 77, 12: 67, 13: 57})
    assert await status(bot, game) == "finished"
    (recap,) = recaps(bot)
    assert bot.channel.texts[-2] == recap
    assert "🏁 Spectrum game **Friday** is over after 1 round." in recap
    assert "🏆 **U11** wins with 255 points!" in recap
    assert "Also: U12 210, U13 165" in recap
    assert "🎯 Closest guess: **U11** said 77 with the target at 77 on left " in recap
    assert not any("starts now" in t for t in bot.channel.texts)
    assert len(reveals(bot)) == 3
    assert card.embed.title == "🏁 Friday: finished" and card.view is None
    assert (await bot.repo.get_current_round(game["id"]))["closed_at"]


async def test_multi_round_game(bot):
    game = await running(bot, rounds=2)
    first_round = await bot.repo.get_current_round(game["id"])
    for _ in range(3):
        await play_turn(bot, game)
    (results,) = [t for t in bot.channel.texts if "is done." in t]
    assert results.split("\n")[1] == "🏁 **Round 1 of 2** is done."
    assert results.split("\n")[2] == "This round: U11 57, U12 57, U13 57"
    assert results.split("\n")[3] == "Totals: U11 57, U12 57, U13 57"
    assert results.endswith("Round 2 starts now.")
    second_round = await bot.repo.get_current_round(game["id"])
    assert second_round["number"] == 2 and second_round["id"] != first_round["id"]
    await set_targets(bot)
    opened = await current(bot, game)
    assert "Turn 1 of 3 (round 2 of 2)" in message_of(bot, opened["turn_message_id"]).content
    used = await bot.repo.used_scales(game["id"])
    assert len(used) == 6
    for _ in range(3):
        assert await status(bot, game) == "active"
        await play_turn(bot, game)
    (recap,) = recaps(bot)
    assert "is over after 2 rounds." in recap
    assert "🏆 **U11**, **U12** and **U13** tie with 114 points each!" in recap
    assert len([t for t in bot.channel.texts if "is done." in t]) == 1


async def test_no_clue_turn_scores_nothing(db):
    bot = make_bot(db, clue_seconds=0)
    game = await running(bot)
    turn = await current(bot, game)
    posted = message_of(bot, turn["turn_message_id"])
    assert await flow.close_overdue(bot) == 1
    assert await outcome(bot, turn) == "no_clue"
    giver = f"U{turn['giver_user_id']}"
    assert flow.no_clue_line(giver) in bot.channel.texts
    assert posted.view is None
    assert posted.content == flow.turn_done_text(game, turn, giver, "no_clue")
    assert await flow.submit_clue(bot, turn["id"], turn["giver_user_id"], "late") == (
        "The clue step for that turn is over."
    )
    assert await bot.repo.scored_turns(game["id"]) == []
    assert not reveals(bot)
    assert (await current(bot, game))["position"] == 2


async def test_no_clue_turns_not_idle(db):
    bot = make_bot(db, clue_seconds=0)
    game = await running(bot)
    assert await flow.close_overdue(bot) == 1
    assert await flow.close_overdue(bot) == 1
    assert await status(bot, game) == "active"
    assert (await current(bot, game))["position"] == 3
    await play_turn(bot, game)
    (recap,) = recaps(bot)
    assert "is over after 1 round." in recap and "nobody gave a clue" not in recap
    assert [t["outcome"] for t in await turns_of(bot, game)] == ["no_clue", "no_clue", "revealed"]


async def test_round_with_no_clue_ends_game(db):
    bot = make_bot(db, clue_seconds=0)
    game = await running(bot, rounds=2)
    for _ in range(3):
        assert await status(bot, game) == "active"
        assert await flow.close_overdue(bot) == 1
    assert await status(bot, game) == "finished"
    (recap,) = recaps(bot)
    assert "is over after 1 round (nobody gave a clue this round)." in recap
    assert "Nobody scored." in recap
    assert not any("starts now" in t for t in bot.channel.texts)
    assert len(await bot.repo._all("SELECT * FROM spectrum_rounds")) == 1


async def test_two_unguessed_turns_end_game(db):
    bot = make_bot(db, guess_seconds=0)
    game = await running(bot, users=FOUR)
    await play_to_guessing(bot, game)
    assert await flow.close_overdue(bot) == 1
    assert await status(bot, game) == "active"
    assert "Nobody guessed, so nobody scores." in reveals(bot)[0]
    await play_to_guessing(bot, game)
    assert await flow.close_overdue(bot) == 1
    assert await status(bot, game) == "finished"
    (recap,) = recaps(bot)
    assert "is over after 1 round (2 turns in a row got no guesses)." in recap
    outcomes = [t["outcome"] for t in await turns_of(bot, game)]
    assert outcomes == ["revealed", "revealed", None, None]
    assert len(turn_posts(bot)) == 2


async def test_guesserless_reveal_not_idle(db):
    bot = make_bot(db, guess_seconds=0)
    game = await running(bot, users=FOUR)
    await play_to_guessing(bot, game)
    assert await flow.close_overdue(bot) == 1
    lonely = await play_to_guessing(bot, game)
    async with bot.repo.db.transaction() as conn:
        await conn.execute("DELETE FROM spectrum_turn_guessers WHERE turn_id = ?", (lonely["id"],))
        await conn.execute(
            "UPDATE spectrum_turns SET eligible_guessers = 0 WHERE id = ?", (lonely["id"],)
        )
    assert await flow.close_if_done(bot, lonely["id"])
    assert len(reveals(bot)) == 2 and await status(bot, game) == "active"
    assert await bot.repo.revealed_guess_counts(game["id"]) == [(3, 0), (0, 0)]
    await play_to_guessing(bot, game)
    assert await flow.close_overdue(bot) == 1
    assert await status(bot, game) == "finished"
    assert "(2 turns in a row got no guesses)" in recaps(bot)[0]


async def test_guess_deadline_reveals(db):
    bot = make_bot(db, guess_seconds=0)
    game = await running(bot)
    turn = await play_to_guessing(bot, game)
    one, _ = await others(bot, game, turn)
    await guess(bot, turn, one, 80)
    assert not reveals(bot)
    assert await flow.close_overdue(bot) == 1
    (reveal,) = reveals(bot)
    assert f"` 80` **U{one}**: 91" in reveal
    assert f"**U{turn['giver_user_id']}** scores **91**" in reveal
    assert await flow.submit_guess(bot, turn["id"], one, "81") == (
        "Guessing for that turn has closed."
    )
    assert (await current(bot, game))["position"] == 2
    assert await flow.close_overdue(bot) == 0


async def test_giver_left_before_turn_skipped_with_line(bot):
    game = await running(bot, users=FOUR)
    first, second, third, _ = await turns_of(bot, game)
    await flow.leave_game(bot, game, second["giver_user_id"])
    assert (await current(bot, game))["id"] == first["id"]
    before = len(bot.channel.messages)
    await play_turn(bot, game)
    assert await outcome(bot, second) == "skipped"
    line = flow.skipped_line(f"U{second['giver_user_id']}", "left")
    assert line == f"_ _\n⏭️ **U{second['giver_user_id']}** left, so their turn is skipped."
    assert line in bot.channel.texts[before:]
    assert (await current(bot, game))["id"] == third["id"]
    assert (await bot.repo.get_turn(second["id"]))["started_at"] is None


async def test_no_guessers_turn_skipped_with_line(db):
    bot = make_bot(db, min_players=2, clue_seconds=0)
    game = await running(bot, users=(11, 12))
    first, second = await turns_of(bot, game)
    await bot.repo.leave(first["giver_id"])
    assert await flow.close_overdue(bot) == 1
    assert await outcome(bot, first) == "no_clue"
    assert await outcome(bot, second) == "skipped"
    line = (
        f"_ _\n⏭️ Nobody else is here to guess, so **U{second['giver_user_id']}**'s turn is skipped."
    )
    assert line in bot.channel.texts
    assert await status(bot, game) == "finished"
    assert len(turn_posts(bot)) == 1


async def test_all_guessers_leave_after_clue_reveals(db):
    bot = make_bot(db, min_players=2)
    game = await running(bot)
    turn = await play_to_guessing(bot, game)
    one, two = await others(bot, game, turn)
    await flow.leave_game(bot, game, one)
    assert not reveals(bot)
    assert "0/1 guesses in." in message_of(bot, turn["guess_message_id"]).content
    await flow.leave_game(bot, game, two)
    (reveal,) = reveals(bot)
    assert "Nobody guessed, so nobody scores." in reveal and DRUM not in bot.channel.texts
    assert await outcome(bot, turn) == "revealed"
    (recap,) = recaps(bot)
    assert "(too few players left)" in recap
    assert bot.channel.texts.index(reveal) < bot.channel.texts.index(recap)
    assert message_of(bot, turn["guess_message_id"]).view is None


async def test_end_during_guessing_reveals_before_recap(bot):
    game = await running(bot)
    turn = await play_to_guessing(bot, game)
    one, _ = await others(bot, game, turn)
    await guess(bot, turn, one, 67)
    assert await flow.finish(bot, game, note="ended by U11")
    (reveal,) = reveals(bot)
    (recap,) = recaps(bot)
    assert bot.channel.texts.index(reveal) + 1 == bot.channel.texts.index(recap)
    assert DRUM not in bot.channel.texts
    assert "## 🎯 The target was 77" in reveal and f"` 67` **U{one}**: 70" in reveal
    assert "is over after 1 round (ended by U11)." in recap
    assert await outcome(bot, turn) == "revealed"
    posted = message_of(bot, turn["guess_message_id"])
    assert posted.view is None and posted.content.endswith("Guessing is closed.")
    giver = turn["giver_user_id"]
    low, high = sorted((giver, one))
    assert f"🏆 **U{low}** and **U{high}** tie with 70 points each!" in recap
    waiting = [t for t in await turns_of(bot, game) if t["id"] != turn["id"]]
    assert all(t["outcome"] is None for t in waiting)


async def test_end_during_clue_abandons_turn(bot):
    game = await running(bot)
    await set_targets(bot, 97)
    turn = await current(bot, game)
    assert await flow.finish(bot, game, note="ended by U11")
    assert await outcome(bot, turn) == "abandoned"
    posted = message_of(bot, turn["turn_message_id"])
    assert posted.view is None and posted.content.endswith("The game ended.")
    (recap,) = recaps(bot)
    assert "is over after 0 rounds (ended by U11)." in recap and "Nobody scored." in recap
    assert not reveals(bot)
    assert "97" not in everything_public(bot)
    assert await flow.show_target(bot, turn["id"], turn["giver_user_id"]) == "That game is over."


async def test_double_finish_one_recap(bot):
    game = await running(bot)
    turn = await play_to_guessing(bot, game)
    results = await asyncio.gather(flow.finish(bot, game), flow.finish(bot, game))
    assert sorted(results) == [False, True]
    assert len(recaps(bot)) == 1 and len(reveals(bot)) == 1
    assert await outcome(bot, turn) == "revealed"
    assert not await flow.finish(bot, game)
    assert len(recaps(bot)) == 1


async def test_post_retries_once(bot):
    game = await lobby(bot)
    bot.channel.fail_with = [http_error(discord.HTTPException, 503)]
    await flow.begin_game(bot, game, 1)
    turn = await current(bot, game)
    assert turn is not None and turn["turn_message_id"] == bot.channel.messages[-1].id
    assert sum("you give the clue" in t for t in bot.channel.texts) == 1


async def test_post_fails_twice_abandons_turn(bot):
    game = await lobby(bot)
    bot.channel.fail_with = [http_error(discord.HTTPException, 503)] * 2
    await flow.begin_game(bot, game, 1)
    first, second, _ = await turns_of(bot, game)
    assert first["outcome"] == "abandoned" and first["turn_message_id"] is None
    assert bot.channel.messages == [] and await current(bot, game) is None
    assert await flow.resume_stalled(bot, grace=30) == 0
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert (await current(bot, game))["id"] == second["id"]
    assert await bot.repo.scored_turns(game["id"]) == []
    assert await flow.idle_note(bot, game) is None


async def test_three_abandoned_turns_end_game_mid_round(bot):
    game = await lobby(bot, users=(11, 12, 13, 14, 15, 16))
    bot.channel.fail_with = [http_error(discord.HTTPException, 503)] * 6
    await flow.begin_game(bot, game, 1)
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert await status(bot, game) == "active" and bot.channel.fail_with == []
    assert await bot.repo.abandoned_streak(game["id"]) == 3
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert await status(bot, game) == "finished"
    outcomes = [t["outcome"] for t in await turns_of(bot, game)]
    assert outcomes == ["abandoned", "abandoned", "abandoned", None, None, None]
    (recap,) = recaps(bot)
    assert "is over after 0 rounds (I couldn't post in the game channel)." in recap
    assert not any("you give the clue" in t for t in bot.channel.texts)


async def test_channel_gone_ends_game(bot):
    game = await lobby(bot)
    bot.channel.fail_with = [http_error(discord.Forbidden, 403)]
    await flow.begin_game(bot, game, 1)
    assert await status(bot, game) == "finished"
    assert "(I couldn't post in the game channel)" in recaps(bot)[0]
    assert [t["outcome"] for t in await turns_of(bot, game)] == ["abandoned", None, None]


async def test_late_joiner_gets_turn_at_end(bot):
    game = await running(bot)
    card = await add_card(bot, game)
    reply = await flow.join_game(bot, game, 14)
    assert reply == flow.late_join_text()
    assert "_ _\n🙋 **U14** joined." in bot.channel.texts
    assert card_names(card) == ["U11", "U12", "U13", "U14"]
    turns = await turns_of(bot, game)
    assert [t["position"] for t in turns] == [1, 2, 3, 4]
    assert turns[-1]["giver_user_id"] == 14 and 1 <= turns[-1]["target"] <= 100
    scales = {(t["left_end"], t["right_end"]) for t in turns}
    assert len(scales) == 4
    await set_targets(bot)
    for _ in range(3):
        await play_turn(bot, game)
    assert not recaps(bot)
    last = await current(bot, game)
    assert last["giver_user_id"] == 14 and last["position"] == 4 and last["turn_count"] == 4
    assert "Turn 4 of 4" in message_of(bot, last["turn_message_id"]).content
    await play_turn(bot, game)
    assert await outcome(bot, last) == "revealed" and len(recaps(bot)) == 1
    assert "U14" in recaps(bot)[0]


async def test_late_joiner_guesses_from_next_clue(bot):
    game = await running(bot)
    turn = await play_to_guessing(bot, game)
    one, two = await others(bot, game, turn)
    assert await flow.join_game(bot, game, 14) == flow.late_join_text()
    posted = message_of(bot, turn["guess_message_id"])
    assert "0/2 guesses in." in posted.content
    assert await flow.submit_guess(bot, turn["id"], 14, "50") == JOINED_LATE
    assert await bot.repo.turn_guesses(turn["id"]) == []
    await guess(bot, turn, one, 50)
    await guess(bot, turn, two, 50)
    assert len(reveals(bot)) == 1 and "U14" not in reveals(bot)[0]
    following = await play_to_guessing(bot, game)
    assert "0/3 guesses in." in message_of(bot, following["guess_message_id"]).content
    if following["giver_user_id"] != 14:
        assert "Your guess is **50**" in await guess(bot, following, 14, 50)


async def test_late_joiner_during_last_turn(bot):
    game = await running(bot)
    await play_turn(bot, game)
    await play_turn(bot, game)
    last = await play_to_guessing(bot, game)
    assert last["position"] == 3
    assert await flow.join_game(bot, game, 14) == flow.late_join_text()
    for uid in await others(bot, game, last):
        await guess(bot, last, uid, 50)
    assert await outcome(bot, last) == "revealed"
    assert not recaps(bot) and await status(bot, game) == "active"
    appended = await current(bot, game)
    assert appended["giver_user_id"] == 14 and appended["position"] == 4
    await set_targets(bot)
    await play_turn(bot, game)
    assert len(recaps(bot)) == 1 and len(reveals(bot)) == 4


async def test_late_joiner_after_round_closed(bot):
    game = await running(bot, rounds=2)
    rnd = await bot.repo.get_current_round(game["id"])
    for turn in await turns_of(bot, game):
        await bot.repo.close_turn(turn["id"], "abandoned")
    assert await bot.repo.close_round(rnd["id"])
    assert await flow.join_game(bot, game, 14) == "You're in from the next round."
    assert len(await turns_of(bot, game)) == 3
    await flow.advance(bot, game)
    second = await bot.repo.get_current_round(game["id"])
    assert second["number"] == 2
    assert {t["giver_user_id"] for t in await turns_of(bot, game)} == {11, 12, 13, 14}


async def test_rejoin_no_second_turn(bot):
    game = await running(bot, users=FOUR)
    first = await play_turn(bot, game)
    await flow.leave_game(bot, game, first["giver_user_id"])
    assert await flow.join_game(bot, game, first["giver_user_id"]) == "You're back in."
    turns = await turns_of(bot, game)
    assert len(turns) == 4
    assert [t["giver_user_id"] for t in turns].count(first["giver_user_id"]) == 1


async def test_rejoin_keeps_waiting_turn(bot):
    game = await running(bot, users=FOUR)
    _, second, _, _ = await turns_of(bot, game)
    await flow.leave_game(bot, game, second["giver_user_id"])
    assert await flow.join_game(bot, game, second["giver_user_id"]) == "You're back in."
    await play_turn(bot, game)
    opened = await current(bot, game)
    assert opened["id"] == second["id"] and opened["outcome"] is None
    assert not any("⏭️" in t for t in bot.channel.texts)


async def test_giver_leaves_during_clue_skips(bot):
    game = await running(bot, users=FOUR)
    turn = await current(bot, game)
    posted = message_of(bot, turn["turn_message_id"])
    reply = await flow.leave_game(bot, game, turn["giver_user_id"])
    assert reply == "You've left. Your clues and guesses so far still count."
    giver = f"U{turn['giver_user_id']}"
    assert await outcome(bot, turn) == "skipped"
    assert posted.view is None
    assert posted.content.endswith(f"**{giver}** left, so this turn is skipped.")
    assert flow.skipped_line(giver, "left") in bot.channel.texts
    assert (await current(bot, game))["position"] == 2
    assert await flow.submit_clue(bot, turn["id"], turn["giver_user_id"], "late") == (
        "The clue step for that turn is over."
    )


async def test_skip_cannot_undo_a_saved_clue(bot):
    game = await running(bot, users=FOUR)
    waiting = await current(bot, game)
    turn = await play_to_guessing(bot, game)
    assert not await flow.skip_turn(bot, game, waiting, "skipped", "left")
    assert not await flow.skip_turn(bot, game, waiting, "no_clue", "no_clue")
    assert await outcome(bot, turn) is None and not any("⏭️" in t for t in bot.channel.texts)


async def test_giver_leaves_after_clue_still_scores(bot):
    game = await running(bot, users=FOUR)
    turn = await play_to_guessing(bot, game)
    giver = turn["giver_user_id"]
    guessers = await others(bot, game, turn)
    await flow.leave_game(bot, game, giver)
    assert await outcome(bot, turn) is None and not reveals(bot)
    posted = message_of(bot, turn["guess_message_id"])
    assert posted.view is not None and "0/3 guesses in." in posted.content
    for uid in guessers:
        await guess(bot, turn, uid, 67)
    (reveal,) = reveals(bot)
    assert reveal.endswith(f"**U{giver}** scores **70** for the clue, the guessers' average.")
    await flow.finish(bot, game)
    assert "tie with 70 points each!" in recaps(bot)[0] and f"**U{giver}**" in recaps(bot)[0]


async def test_leave_during_guessing_closes_turn(bot):
    game = await running(bot, users=FOUR)
    turn = await play_to_guessing(bot, game)
    one, two, three = await others(bot, game, turn)
    await guess(bot, turn, one, 50)
    await flow.leave_game(bot, game, two)
    assert "1/2 guesses in." in message_of(bot, turn["guess_message_id"]).content
    assert not reveals(bot)
    await guess(bot, turn, three, 60)
    assert len(reveals(bot)) == 1
    following = await play_to_guessing(bot, game)
    remaining = await others(bot, game, following)
    await guess(bot, following, remaining[0], 50)
    assert len(reveals(bot)) == 1
    await flow.leave_game(bot, game, remaining[1])
    assert len(reveals(bot)) == 2 and await outcome(bot, following) == "revealed"


async def test_leavers_guess_still_scores(bot):
    game = await running(bot, users=FOUR)
    turn = await play_to_guessing(bot, game)
    one, two, three = await others(bot, game, turn)
    await guess(bot, turn, one, 77)
    await flow.leave_game(bot, game, one)
    assert "0/2 guesses in." in message_of(bot, turn["guess_message_id"]).content
    assert await flow.submit_guess(bot, turn["id"], one, "1") == "You've left the game."
    await guess(bot, turn, two, 67)
    await guess(bot, turn, three, 67)
    (reveal,) = reveals(bot)
    assert f"` 77` **U{one}**: 100" in reveal
    assert f"**U{turn['giver_user_id']}** scores **80**" in reveal
    await flow.finish(bot, game)
    assert f"🏆 **U{one}** wins with 100 points!" in recaps(bot)[0]


async def test_leave_ends_game(bot):
    game = await running(bot)
    card = await add_card(bot, game)
    turn = await current(bot, game)
    one, _ = await others(bot, game, turn)
    await flow.leave_game(bot, game, one)
    assert await status(bot, game) == "finished"
    assert f"_ _\n🚪 **U{one}** left. 2 players remain." in bot.channel.texts
    assert "(too few players left)" in recaps(bot)[0]
    assert card.embed.title == "🏁 Friday: finished"
    with pytest.raises(GameError, match="That game is over."):
        await flow.leave_game(bot, await bot.repo.get_game(game["id"]), 11)
    with pytest.raises(GameError, match="That game is over."):
        await flow.join_game(bot, await bot.repo.get_game(game["id"]), 15)


async def test_join_and_leave_in_the_lobby(bot):
    game = await lobby(bot, users=(11,))
    assert await flow.join_game(bot, game, 12) == "You're in."
    assert "2 of at least 3 players signed up, 1 more needed to start." in bot.channel.texts[-1]
    with pytest.raises(GameError, match="You're already in."):
        await flow.join_game(bot, game, 12)
    assert await flow.leave_game(bot, game, 12) == "You've left the game."
    with pytest.raises(GameError, match="You're not in this game."):
        await flow.leave_game(bot, game, 12)
    await flow.leave_game(bot, game, 11)
    assert await status(bot, game) == "finished"
    assert (
        bot.channel.texts[-1] == "_ _\n🚫 Spectrum game **Friday** was cancelled (everyone left)."
    )


async def test_host_handover(bot):
    game = await running(bot, users=FOUR)
    await flow.leave_game(bot, game, 11)
    assert (await bot.repo.get_game(game["id"]))["host_id"] == 12
    assert "_ _\n👑 **U12** is now the host." in bot.channel.texts
    await flow.leave_game(bot, await bot.repo.get_game(game["id"]), 13)
    assert (await bot.repo.get_game(game["id"]))["host_id"] == 12


async def test_require_host_or_admin(bot):
    game = await lobby(bot)
    await require_host_or_admin(interaction_for(bot, 11), game)
    await require_host_or_admin(interaction_for(bot, 99, manage_guild=True), game)
    with pytest.raises(NotGameAdmin):
        await require_host_or_admin(interaction_for(bot, 12), game)


async def test_close_overdue_clues(db):
    bot = make_bot(db, clue_seconds=0)
    game = await running(bot)
    first = await current(bot, game)
    bot.settings.spectrum_clue_seconds = 120
    assert await flow.close_overdue(bot) == 1
    assert await outcome(bot, first) == "no_clue"
    second = await current(bot, game)
    assert second["position"] == 2
    assert await flow.close_overdue(bot) == 0
    assert await outcome(bot, second) is None


async def test_close_overdue_guessing(db):
    bot = make_bot(db, guess_seconds=0)
    game = await running(bot)
    turn = await play_to_guessing(bot, game)
    assert await flow.close_overdue(bot) == 1
    assert await outcome(bot, turn) == "revealed" and len(reveals(bot)) == 1
    assert await flow.close_overdue(bot) == 0
    assert (await current(bot, game))["clue"] is None


async def test_overdue_without_message_is_abandoned(db):
    bot = make_bot(db, clue_seconds=0, guess_seconds=0)
    game = await lobby(bot)
    await bot.repo.start_game(game["id"], 1)
    game = await bot.repo.get_game(game["id"])
    rnd = await bot.repo.begin_round(game["id"], bot.games["spectrum"].scales[:12], flow.RNG)
    unposted = await bot.repo.next_turn(rnd["id"])
    assert await bot.repo.open_turn(unposted["id"], flow.deadline_in(0))
    assert await flow.close_overdue(bot) == 1
    assert await outcome(bot, unposted) == "abandoned"
    assert not any("⏭️" in t for t in bot.channel.texts)
    clued = await current(bot, game)
    assert clued["position"] == 2 and clued["turn_message_id"]
    assert await bot.repo.save_clue(clued["id"], clued["giver_id"], "a clue", flow.deadline_in(0))
    before = len(bot.channel.messages)
    assert await flow.close_overdue(bot) == 1
    assert await outcome(bot, clued) == "abandoned"
    assert not reveals(bot) and DRUM not in bot.channel.texts
    assert not any("⏭️" in t for t in bot.channel.texts)
    assert await bot.repo.revealed_guess_counts(game["id"]) == []
    assert await flow.idle_note(bot, game) is None
    assert await status(bot, game) == "active"
    assert (await current(bot, game))["position"] == 3
    assert len(bot.channel.messages) == before + 1


async def test_resume_stalled_after_crash_mid_round(bot):
    game = await running(bot)
    turn = await play_to_guessing(bot, game)
    assert await flow.resume_stalled(bot, grace=0) == 0
    assert await bot.repo.close_turn(turn["id"], "revealed")
    assert await current(bot, game) is None
    assert await flow.resume_stalled(bot, grace=30) == 0
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert (await current(bot, game))["position"] == 2
    assert await flow.resume_stalled(bot, grace=0) == 0
    fresh = make_bot(bot.repo.db)
    started = await fresh.repo.create_game(2, "Crashed", 21, CHANNEL_ID)
    for uid in (21, 22, 23):
        await fresh.repo.join(started["id"], uid)
    await fresh.repo.start_game(started["id"], 1)
    assert await flow.resume_stalled(fresh, grace=0) == 1
    opened = await current(fresh, started)
    assert opened is not None and opened["position"] == 1


async def test_target_never_in_channel_before_reveal(bot):
    game = await running(bot)
    await set_targets(bot, 97)
    turn = await current(bot, game)
    assert "**97**" in await flow.show_target(bot, turn["id"], turn["giver_user_id"])
    turn = await play_to_guessing(bot, game)
    one, two = await others(bot, game, turn)
    await flow.join_game(bot, game, 14)
    await flow.leave_game(bot, game, 14)
    await guess(bot, turn, one, 83)
    assert "97" not in everything_public(bot) and "83" not in everything_public(bot)
    for uid in (11, 12, 13, 99):
        assert "97" not in STAMPS.sub("", await flow.status_reply(bot, game, uid))
    await guess(bot, turn, two, 84)
    assert "The target was 97" in everything_public(bot)
    following = await current(bot, game)
    assert following["position"] == 2
    since = bot.channel.texts[bot.channel.texts.index(reveals(bot)[0]) + 1 :]
    assert since and all("97" not in STAMPS.sub("", t) for t in since)


async def test_guesses_hidden_until_reveal(bot):
    game = await running(bot, users=FOUR)
    turn = await play_to_guessing(bot, game)
    one, two, three = await others(bot, game, turn)
    await guess(bot, turn, one, 83)
    await guess(bot, turn, two, 84)
    await guess(bot, turn, one, 85)
    shown = everything_public(bot)
    for hidden in ("83", "84", "85", f"U{one}", f"U{two}", f"U{three}"):
        assert hidden not in shown, hidden
    for uid in FOUR:
        reply = await flow.status_reply(bot, game, uid)
        assert "2/3 guesses in." in reply
        assert ("**85**" in reply) == (uid == one) and ("**84**" in reply) == (uid == two)
        assert "83" not in STAMPS.sub("", reply)
    await guess(bot, turn, three, 86)
    (reveal,) = reveals(bot)
    assert f"` 85` **U{one}**" in reveal and f"` 84` **U{two}**" in reveal
    assert "83" not in reveal


async def test_status_in_the_lobby(bot):
    game = await lobby(bot)
    reply = await flow.status_reply(bot, game, 11)
    assert reply.split("\n") == [
        "Spectrum game **Friday**, hosted by **U11**.",
        "**Players (3):** U11, U12, U13",
        "Sign-ups are open. It needs at least 3 to start.",
    ]


async def test_status_during_the_clue_step(bot):
    game = await running(bot)
    await set_targets(bot, 97)
    turn = await current(bot, game)
    giver = turn["giver_user_id"]
    waiting = (await turns_of(bot, game))[1]["giver_user_id"]
    mine = (await flow.status_reply(bot, game, giver)).split("\n")
    assert mine[2].startswith("🌈 **Turn 1 of 3** (round 1 of 1): left ")
    assert mine[3].startswith(f"**U{giver}** is writing the clue. Clue due <t:")
    assert mine[4:] == [
        "It's your turn to give the clue. Press **Show my target** on the turn message."
    ]
    theirs = (await flow.status_reply(bot, game, waiting)).split("\n")
    assert theirs[:4] == mine[:4]
    assert theirs[4:] == ["Your turn to give a clue is coming up in this round."]
    outsider = (await flow.status_reply(bot, game, 99)).split("\n")
    assert outsider[4:] == ["You're not in this game. Join with `/gaminator spectrum join`."]
    for uid in (giver, waiting, 99):
        assert "97" not in STAMPS.sub("", await flow.status_reply(bot, game, uid))


async def test_status_during_guessing(bot):
    game = await running(bot, users=FOUR)
    turn = await play_to_guessing(bot, game)
    giver = turn["giver_user_id"]
    one, two, three = await others(bot, game, turn)
    await guess(bot, turn, one, 42)
    await flow.join_game(bot, game, 15)
    lines = (await flow.status_reply(bot, game, giver)).split("\n")
    assert lines[1].startswith("**Players (5):** ")
    assert lines[3] == f"**U{giver}**'s clue: **rain at a picnic**"
    assert lines[4].startswith("1/3 guesses in. Closes <t:")
    assert lines[5:] == [GIVER_SITS_OUT]
    done = (await flow.status_reply(bot, game, one)).split("\n")[5:]
    assert done[0] == "Your guess is **42**."
    todo = (await flow.status_reply(bot, game, two)).split("\n")[5:]
    assert todo[0] == "You haven't guessed yet. Press **Guess** on the clue message."
    late = (await flow.status_reply(bot, game, 15)).split("\n")[5:]
    assert late == [JOINED_LATE, "Your turn to give a clue is coming up in this round."]
    gone = (await flow.status_reply(bot, game, 99)).split("\n")[5:]
    assert gone == ["You're not in this game. Join with `/gaminator spectrum join`."]
    coming = [t["giver_user_id"] for t in await turns_of(bot, game) if t["started_at"] is None]
    for uid in (one, two, three):
        reply = await flow.status_reply(bot, game, uid)
        assert ("Your turn to give a clue is coming up in this round." in reply) == (uid in coming)


async def test_status_between_turns_and_rounds(bot):
    game = await running(bot, rounds=2)
    turn = await play_to_guessing(bot, game)
    assert await bot.repo.close_turn(turn["id"], "revealed")
    waiting = [t["giver_user_id"] for t in await turns_of(bot, game) if t["started_at"] is None]
    lines = (await flow.status_reply(bot, game, waiting[0])).split("\n")
    assert lines[2:] == [
        "The next turn is on its way.",
        "Your turn to give a clue is coming up in this round.",
    ]
    done = (await flow.status_reply(bot, game, turn["giver_user_id"])).split("\n")
    assert done[2:] == ["The next turn is on its way."]
    rnd = await bot.repo.get_current_round(game["id"])
    for left in await turns_of(bot, game):
        await bot.repo.close_turn(left["id"], "abandoned")
    assert await bot.repo.close_round(rnd["id"])
    between = (await flow.status_reply(bot, game, 11)).split("\n")
    assert between[2:] == ["The next round is on its way."]
    outsider = (await flow.status_reply(bot, game, 99)).split("\n")
    assert outsider[2:] == [
        "The next round is on its way.",
        "You're not in this game. Join with `/gaminator spectrum join`.",
    ]


async def finished_game(bot, guild_id=1, users=(11, 12, 13), values=None):
    """Play a one-round game to its end. Returns the game."""
    game = await bot.repo.create_game(guild_id, "Friday", users[0], CHANNEL_ID)
    for uid in users:
        await bot.repo.join(game["id"], uid)
    await flow.begin_game(bot, game, 1)
    await set_targets(bot)
    for _ in users:
        await play_turn(bot, game, values)
    assert await status(bot, game) == "finished"
    return game


async def test_leaderboard_reply(bot):
    empty = "No finished Spectrum games yet. Start one with `/gaminator spectrum create`."
    assert await flow.leaderboard_reply(bot, 1, 11) == empty
    await finished_game(bot, values={11: 77, 12: 67, 13: 57})
    await finished_game(bot, values={11: 57, 12: 77, 13: 67})
    reply = await flow.leaderboard_reply(bot, 1, 11)
    assert reply.split("\n") == [
        "_ _",
        "## 🏆 Spectrum: all-time standings",
        "`1.` **U12**: 1 game won, 465 points, 2 played",
        "`2.` **U11**: 1 game won, 420 points, 2 played",
        "`3.` **U13**: 0 games won, 375 points, 2 played",
    ]
    assert await flow.leaderboard_reply(bot, 1, 99) == reply
    assert await flow.leaderboard_reply(bot, 2, 11) == empty
    member = await flow.leaderboard_reply(bot, 1, 11, member_id=12)
    assert member == "**U12**: 1 game won, 465 points, 2 played"
    stranger = await flow.leaderboard_reply(bot, 1, 11, member_id=99)
    assert stranger == "**U99** hasn't played a finished game yet."
    assert "<@" not in reply and "<@" not in member


async def test_leaderboard_ignores_running_game(bot):
    await finished_game(bot, values={11: 77, 12: 67, 13: 57})
    before = await flow.leaderboard_reply(bot, 1, 11)
    game = await running(bot)
    await play_turn(bot, game, {11: 77, 12: 77, 13: 77})
    assert await flow.leaderboard_reply(bot, 1, 11) == before
    await flow.finish(bot, game)
    assert await flow.leaderboard_reply(bot, 1, 11) != before


async def test_recap_then_standings(bot, monkeypatch, caplog):
    game = await finished_game(bot, values={11: 77, 12: 67, 13: 57})
    (recap,) = recaps(bot)
    standings = bot.channel.texts[-1]
    assert bot.channel.texts.index(recap) + 1 == bot.channel.texts.index(standings)
    assert standings == await flow.leaderboard_reply(bot, 1, 0)
    assert "`1.` **U11**: 1 game won, 255 points, 1 played" in standings
    assert bot.channel.messages[-1].kwargs["allowed_mentions"] is NO_MENTIONS
    assert recap.endswith("All-time standings: `/gaminator spectrum leaderboard`")

    async def broken(*args):
        raise RuntimeError("boom")

    again = await running(bot)
    card = await add_card(bot, again)
    monkeypatch.setattr(bot.repo, "games_played", broken)
    before = len(bot.channel.messages)
    assert await flow.finish(bot, again, note="ended by U11")
    assert any("standings after the game failed" in r.getMessage() for r in caplog.records)
    assert len(bot.channel.messages) == before + 1 and "is over after" in bot.channel.texts[-1]
    assert card.embed.title == "🏁 Friday: finished"
    assert game["id"] != again["id"]


async def test_no_standings_before_any_finished_turn(bot):
    game = await running(bot)
    assert await flow.finish(bot, game)
    assert "is over after 0 rounds." in bot.channel.texts[-1]
    assert not any("all-time standings" in t for t in bot.channel.texts)

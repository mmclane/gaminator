"""Most Likely To game flow, driven through a fake bot and channel."""

import asyncio
from types import SimpleNamespace

import aiohttp
import discord
import pytest
from mostlikely_fakes import (
    CHANNEL_ID,
    add_card,
    card_names,
    interaction_for,
    make_bot,
    reply_of,
)

from gaminator.core.util import GameError, NotGameAdmin
from gaminator.games.mostlikely.commands.group import require_host_or_admin
from gaminator.games.mostlikely.services import game as flow
from gaminator.games.mostlikely.services.rules import CLOSED
from gaminator.games.mostlikely.vote import handle_vote


@pytest.fixture
async def bot(db):
    return make_bot(db)


async def lobby(bot, users=(11, 12, 13)):
    game = await bot.repo.create_game(1, "Friday", users[0], CHANNEL_ID)
    for uid in users:
        await bot.repo.join(game["id"], uid)
    return game


async def running(bot, users=(11, 12, 13), rounds=3):
    game = await lobby(bot, users)
    await flow.begin_game(bot, game, rounds)
    return await bot.repo.get_game(game["id"])


async def vote(bot, game, voter_uid, target_uid):
    rnd = await bot.repo.get_current_round(game["id"])
    voter = await bot.repo.get_player(game["id"], voter_uid)
    target = await bot.repo.get_player(game["id"], target_uid)
    assert await bot.repo.cast_vote(rnd["id"], voter["id"], target["id"])


async def close_current(bot, game):
    rnd = await bot.repo.get_current_round(game["id"])
    return await flow.close_round(bot, game, rnd)


def labels(message):
    return [o.label for o in message.view.children[0].item.options]


def forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "missing access")


async def test_begin_game_posts_prompt(bot):
    game = await lobby(bot)
    reply = await flow.begin_game(bot, game, 3)
    assert reply == "Most Likely To game **Friday** has started with 3 players and 3 prompts."
    game = await bot.repo.get_game(game["id"])
    assert game["status"] == "active" and game["max_rounds"] == 3 and game["round"] == 1
    (message,) = bot.channel.messages
    assert "**Prompt 1 of 3**\n## Most likely to do thing" in message.content
    assert "0/3 votes in." in message.content
    assert labels(message) == ["U11", "U12", "U13"]
    rnd = await bot.repo.get_current_round(game["id"])
    assert rnd["vote_message_id"] == message.id and rnd["vote_channel_id"] == CHANNEL_ID


async def test_begin_game_refusals(bot):
    game = await lobby(bot, users=(11, 12))
    with pytest.raises(GameError, match="Need at least 3 players to start; 2 joined"):
        await flow.begin_game(bot, game, 3)
    await bot.repo.join(game["id"], 13)
    await flow.begin_game(bot, game, 3)
    with pytest.raises(GameError, match="already started"):
        await flow.begin_game(bot, await bot.repo.get_game(game["id"]), 3)


async def test_double_start_one_prompt(bot):
    game = await lobby(bot)
    results = await asyncio.gather(
        flow.begin_game(bot, game, 3), flow.begin_game(bot, game, 3), return_exceptions=True
    )
    assert sorted(isinstance(r, GameError) for r in results) == [False, True]
    assert len(bot.channel.messages) == 1
    assert (await bot.repo.get_game(game["id"]))["round"] == 1


async def test_close_when_everyone_voted(bot):
    game = await running(bot)
    rnd = await bot.repo.get_current_round(game["id"])
    await vote(bot, game, 11, 12)
    await vote(bot, game, 12, 12)
    assert not await flow.everyone_voted(bot, rnd)
    await vote(bot, game, 13, 11)
    assert await flow.everyone_voted(bot, rnd)
    assert await flow.close_round(bot, game, rnd)
    first, reveal, second = bot.channel.messages
    assert first.content.endswith("Voting is closed.") and first.view is None
    assert "It's **U12** with 2 votes." in reveal.content
    assert reveal.content.endswith("Results: U12 2, U11 1")
    assert "**Prompt 2 of 3**" in second.content and second.view is not None
    assert not await flow.close_round(bot, game, rnd)
    assert len(bot.channel.messages) == 3


async def test_reveal_text_has_no_voter_names(bot):
    game = await running(bot)
    await vote(bot, game, 11, 12)
    await vote(bot, game, 12, 12)
    await vote(bot, game, 13, 12)
    await close_current(bot, game)
    reveal = bot.channel.messages[1].content
    assert "U12" in reveal and "U11" not in reveal and "U13" not in reveal


async def test_last_prompt_posts_recap(bot):
    game = await running(bot, rounds=1)
    await vote(bot, game, 11, 13)
    await close_current(bot, game)
    assert (await bot.repo.get_game(game["id"]))["status"] == "finished"
    assert len(bot.channel.messages) == 4
    recap = bot.channel.recap
    assert "is over after 1 prompt." in recap
    assert "🏆 **U13** wins with 1 prompt!" in recap


async def test_double_finish_one_recap(bot):
    game = await running(bot)
    results = await asyncio.gather(flow.finish(bot, game), flow.finish(bot, game))
    assert sorted(results) == [False, True]
    assert sum("is over" in t for t in bot.channel.texts) == 1


async def test_finish_during_open_prompt_leaves_it_out(bot):
    game = await running(bot)
    await vote(bot, game, 11, 12)
    rnd = await bot.repo.get_current_round(game["id"])
    assert await flow.finish(bot, game, note="ended by U11")
    prompt = bot.channel.messages[0]
    assert prompt.view is None
    assert (await bot.repo.get_round(rnd["id"]))["abandoned"] == 1
    recap = bot.channel.texts[-1]
    assert "is over after 0 prompts (ended by U11)." in recap
    assert "Nobody won a prompt." in recap
    assert not await flow.close_round(bot, game, rnd)
    assert not await flow.start_round(bot, game)


async def test_two_prompts_without_votes_end_the_game(bot):
    game = await running(bot, rounds=5)
    await close_current(bot, game)
    assert (await bot.repo.get_game(game["id"]))["status"] == "active"
    assert bot.channel.texts[1].endswith("Nobody voted.")
    await close_current(bot, game)
    assert (await bot.repo.get_game(game["id"]))["status"] == "finished"
    assert "(nobody voted on the last 2 prompts)." in bot.channel.recap


async def test_a_vote_resets_the_no_votes_count(bot):
    game = await running(bot, rounds=5)
    await close_current(bot, game)
    await vote(bot, game, 11, 12)
    await close_current(bot, game)
    await close_current(bot, game)
    assert (await bot.repo.get_game(game["id"]))["status"] == "active"


async def test_failed_reveal_still_posts_next_prompt(bot):
    game = await running(bot)
    await vote(bot, game, 11, 12)
    bot.channel.fail_with = [RuntimeError("boom")]
    await close_current(bot, game)
    game = await bot.repo.get_game(game["id"])
    assert game["status"] == "active" and game["round"] == 2
    assert "**Prompt 2 of 3**" in bot.channel.texts[-1]


async def test_post_prompt_retries_once(bot):
    game = await lobby(bot)
    bot.channel.fail_with = [aiohttp.ClientError("blip")]
    await flow.begin_game(bot, game, 3)
    assert len(bot.channel.messages) == 1
    assert (await bot.repo.get_current_round(game["id"]))["vote_message_id"]


async def test_post_prompt_fails_twice_abandons_round(bot):
    game = await lobby(bot)
    bot.channel.fail_with = [aiohttp.ClientError("blip"), aiohttp.ClientError("blip")]
    await flow.begin_game(bot, game, 3)
    game = await bot.repo.get_game(game["id"])
    rnd = await bot.repo.get_current_round(game["id"])
    assert game["status"] == "active" and rnd["abandoned"] == 1
    assert bot.channel.messages == []
    assert await flow.start_round(bot, game)
    assert "**Prompt 2 of 3**" in bot.channel.texts[-1]


async def test_post_prompt_channel_gone_ends_game(bot):
    game = await lobby(bot)
    bot.channel.fail_with = [forbidden()]
    await flow.begin_game(bot, game, 3)
    assert (await bot.repo.get_game(game["id"]))["status"] == "finished"
    assert "(I couldn't post in the game channel)." in bot.channel.texts[-1]


async def test_join_game_lobby_and_refusals(bot):
    game = await lobby(bot, users=(11,))
    assert await flow.join_game(bot, game, 12) == "You're in."
    assert "🙋 **U12** joined Most Likely To game **Friday**." in bot.channel.texts[-1]
    assert "1 more needed to start" in bot.channel.texts[-1]
    with pytest.raises(GameError, match="You're already in."):
        await flow.join_game(bot, game, 12)
    for uid in range(100, 123):
        await bot.repo.join(game["id"], uid)
    with pytest.raises(GameError, match=r"The game is full \(25 players\)."):
        await flow.join_game(bot, game, 999)
    await bot.repo.finish_game(game["id"])
    with pytest.raises(GameError, match="That game is over."):
        await flow.join_game(bot, await bot.repo.get_game(game["id"]), 999)


async def test_join_mid_game_flow(bot):
    game = await running(bot)
    assert await flow.join_game(bot, game, 14) == "You're in from the next prompt."
    assert bot.channel.texts[-1] == "🙋 **U14** joined and is in from the next prompt."
    prompt = bot.channel.messages[0]
    assert labels(prompt) == ["U11", "U12", "U13"] and prompt.edits == []
    rnd = await bot.repo.get_current_round(game["id"])
    assert await bot.repo.vote_progress(rnd["id"]) == (0, 3)
    for uid in (11, 12, 13):
        await vote(bot, game, uid, 11)
    await close_current(bot, game)
    assert labels(bot.channel.messages[-1]) == ["U11", "U12", "U13", "U14"]
    assert "0/4 votes in." in bot.channel.texts[-1]


async def test_rejoin_back_in(bot):
    game = await running(bot)
    await vote(bot, game, 13, 11)
    await flow.leave_game(bot, game, 13)
    rnd = await bot.repo.get_current_round(game["id"])
    assert await bot.repo.vote_progress(rnd["id"]) == (0, 2)
    assert "0/2 votes in." in bot.channel.messages[0].content
    assert await flow.join_game(bot, game, 13) == "You're back in."
    assert bot.channel.texts[-1] == "🙋 **U13** joined."
    assert await bot.repo.vote_progress(rnd["id"]) == (1, 3)
    assert "1/3 votes in." in bot.channel.messages[0].content


async def test_leave_game_refusals(bot):
    game = await lobby(bot)
    with pytest.raises(GameError, match="You're not in this game."):
        await flow.leave_game(bot, game, 99)
    assert await flow.leave_game(bot, game, 13) == "You've left the game."
    with pytest.raises(GameError, match="You're not in this game."):
        await flow.leave_game(bot, game, 13)


async def test_leave_closes_prompt(bot):
    game = await running(bot)
    await vote(bot, game, 11, 13)
    await vote(bot, game, 12, 13)
    reply = await flow.leave_game(bot, game, 13)
    assert reply == "You've left. Your votes so far still count."
    texts = bot.channel.texts
    assert texts[1] == "🚪 **U13** left. 2 players remain."
    assert "It's **U13** with 2 votes." in texts[2]
    assert "**Prompt 2 of 3**" in texts[3]
    assert labels(bot.channel.messages[3]) == ["U11", "U12"]


async def test_leavers_vote_still_counts(bot):
    game = await running(bot)
    await vote(bot, game, 13, 12)
    await flow.leave_game(bot, game, 13)
    await vote(bot, game, 11, 11)
    await vote(bot, game, 12, 12)
    await close_current(bot, game)
    assert "It's **U12** with 2 votes." in bot.channel.texts[2]


async def test_everyone_left_roster_closes(bot):
    game = await running(bot)
    await flow.join_game(bot, game, 14)
    await flow.join_game(bot, game, 15)
    for uid in (11, 12, 13):
        await flow.leave_game(bot, game, uid)
    game = await bot.repo.get_game(game["id"])
    assert game["status"] == "active" and game["round"] == 2 and game["host_id"] == 14
    assert any(t.endswith("Nobody voted.") for t in bot.channel.texts)
    assert labels(bot.channel.messages[-1]) == ["U14", "U15"]


async def test_leave_ends_game(db):
    bot = make_bot(db, min_players=2)
    game = await running(bot, users=(11, 12))
    await vote(bot, game, 11, 12)
    await flow.leave_game(bot, game, 12)
    assert (await bot.repo.get_game(game["id"]))["status"] == "finished"
    recap = bot.channel.texts[-1]
    assert "(too few players left)." in recap
    assert bot.channel.messages[0].view is None


async def test_host_handover(bot):
    game = await lobby(bot)
    await flow.leave_game(bot, game, 11)
    assert (await bot.repo.get_game(game["id"]))["host_id"] == 12
    assert bot.channel.texts[-1] == "👑 **U12** is now the host."
    await flow.leave_game(bot, game, 13)
    assert (await bot.repo.get_game(game["id"]))["host_id"] == 12
    await flow.leave_game(bot, game, 12)
    assert (await bot.repo.get_game(game["id"]))["status"] == "finished"
    assert "was cancelled (everyone left)" in bot.channel.texts[-1]


async def test_rejoin_keeps_wins(bot):
    game = await running(bot)
    for uid in (11, 12, 13):
        await vote(bot, game, uid, 13)
    await close_current(bot, game)
    await flow.leave_game(bot, game, 13)
    assert await flow.join_game(bot, game, 13) == "You're back in."
    await flow.leave_game(bot, game, 12)
    await flow.finish(bot, game, note="ended by U11")
    recap = bot.channel.recap
    assert "🏆 **U13** wins with 1 prompt!" in recap


async def test_recap_names_a_player_who_left(bot):
    game = await running(bot, rounds=1)
    await flow.join_game(bot, game, 14)
    await vote(bot, game, 11, 12)
    await vote(bot, game, 13, 12)
    await flow.leave_game(bot, game, 12)
    assert (await bot.repo.get_game(game["id"]))["status"] == "finished"
    assert "🏆 **U12** wins with 1 prompt!" in bot.channel.recap


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


async def test_handle_vote(bot):
    game = await running(bot)
    rnd = await bot.repo.get_current_round(game["id"])
    prompt = bot.channel.messages[0]
    ids = {u: (await bot.repo.get_player(game["id"], u))["id"] for u in (11, 12, 13)}

    outsider = interaction_for(bot, 99, message=prompt)
    await handle_vote(outsider, rnd["id"], str(ids[11]))
    assert "Only players in this game can vote" in reply_of(outsider)

    await flow.join_game(bot, game, 14)
    late = interaction_for(bot, 14, message=prompt)
    await handle_vote(late, rnd["id"], str(ids[11]))
    assert "you're in from the next one" in reply_of(late)

    first = interaction_for(bot, 11, message=prompt)
    await handle_vote(first, rnd["id"], str(ids[11]))
    assert reply_of(first).startswith("You voted for **U11**.")
    assert "1/3 votes in." in prompt.content

    for uid in (12, 13):
        await handle_vote(interaction_for(bot, uid, message=prompt), rnd["id"], str(ids[11]))
    assert (await bot.repo.get_round(rnd["id"]))["closed_at"]
    assert (await bot.repo.get_game(game["id"]))["round"] == 2

    stale = interaction_for(bot, 11, message=prompt)
    await handle_vote(stale, rnd["id"], str(ids[12]))
    assert reply_of(stale) == CLOSED


async def test_card_follows_join_leave_start_and_end(bot):
    game = await lobby(bot, users=(11, 12))
    card = await add_card(bot, game)
    await flow.join_game(bot, game, 13)
    assert card_names(card) == ["U11", "U12", "U13"]
    assert card.embed.title == "🤔 Friday: sign-ups open"
    assert len(card.view.children) == 4
    await flow.leave_game(bot, game, 12)
    assert card_names(card) == ["U11", "U13"]
    await flow.join_game(bot, game, 12)
    await flow.begin_game(bot, game, 3)
    assert card.embed.title == "🤔 Friday: in progress"
    assert [c.item.label for c in card.view.children] == ["Join", "Leave", "Rules"]
    await flow.join_game(bot, game, 14)
    assert sorted(card_names(card)) == ["U11", "U12", "U13", "U14"]
    assert card_names(card)[-1] == "U14"


async def test_end_removes_buttons(bot):
    game = await running(bot)
    card = await add_card(bot, game)
    await flow.finish(bot, game, note="ended by U11")
    assert card.embed.title == "🏁 Friday: finished"
    assert card.view is None
    assert card.edits[-1]["view"] is None


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


async def test_missing_card_is_ignored(bot):
    game = await lobby(bot, users=(11,))
    await bot.repo.set_lobby_message(game["id"], 424242)
    bot.channel.fetch_message = _missing
    assert await flow.join_game(bot, game, 12) == "You're in."


async def _missing(message_id):
    raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "unknown message")


async def expire(bot, game):
    rnd = await bot.repo.get_current_round(game["id"])
    async with bot.repo.db.transaction() as conn:
        await conn.execute(
            "UPDATE mostlikely_rounds SET deadline_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", rnd["id"]),
        )
    return rnd


async def test_close_overdue(bot):
    game = await running(bot)
    assert await flow.close_overdue(bot) == 0
    await vote(bot, game, 13, 12)
    await flow.leave_game(bot, game, 13)
    await expire(bot, game)
    assert await flow.close_overdue(bot) == 1
    assert any("It's **U12** with 1 vote." in t for t in bot.channel.texts)
    assert "**Prompt 2 of 3**" in bot.channel.texts[-1]
    assert await flow.close_overdue(bot) == 0
    assert (await bot.repo.get_game(game["id"]))["round"] == 2


async def test_close_overdue_skips_ended_game(bot):
    game = await running(bot)
    await expire(bot, game)
    await flow.finish(bot, game, note="ended by U11")
    count = len(bot.channel.messages)
    assert await flow.close_overdue(bot) == 0
    assert len(bot.channel.messages) == count


async def test_resume_stalled(bot):
    game = await running(bot, rounds=2)
    assert await flow.resume_stalled(bot, grace=0) == 0
    rnd = await bot.repo.get_current_round(game["id"])
    await vote(bot, game, 11, 12)
    await bot.repo.close_round(rnd["id"])
    assert await flow.resume_stalled(bot) == 0
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert "**Prompt 2 of 2**" in bot.channel.texts[-1]
    assert await flow.resume_stalled(bot, grace=0) == 0
    last = await bot.repo.get_current_round(game["id"])
    await bot.repo.close_round(last["id"])
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert (await bot.repo.get_game(game["id"]))["status"] == "finished"
    assert sum("is over" in t for t in bot.channel.texts) == 1
    assert "🏆 **U12** wins with 1 prompt!" in bot.channel.recap
    assert await flow.resume_stalled(bot, grace=0) == 0


async def test_resume_after_failed_first_prompt(bot):
    game = await lobby(bot)
    bot.channel.fail_with = [aiohttp.ClientError("blip"), aiohttp.ClientError("blip")]
    await flow.begin_game(bot, game, 3)
    assert bot.channel.messages == []
    assert await flow.resume_stalled(bot, grace=0) == 1
    assert "**Prompt 2 of 3**" in bot.channel.texts[-1]


async def play(bot, users, votes, guild_id=1, rounds=1):
    """Run a whole game. ``votes`` is one list of (voter, target) user id pairs per prompt."""
    game = await bot.repo.create_game(guild_id, "Friday", users[0], CHANNEL_ID)
    for uid in users:
        await bot.repo.join(game["id"], uid)
    await flow.begin_game(bot, game, rounds)
    for prompt_votes in votes:
        for voter, target in prompt_votes:
            await vote(bot, game, voter, target)
        await close_current(bot, game)
    return game


async def test_leaderboard_reply(bot):
    empty = await flow.leaderboard_reply(bot, 1, 11)
    assert empty.startswith("No finished Most Likely To games yet.")
    await play(
        bot, (11, 12, 13), [[(11, 12), (13, 12)], [(11, 13), (12, 13)], [(11, 12)]], rounds=3
    )
    await play(bot, (11, 12, 14), [[(11, 14), (12, 14)]])
    lines = (await flow.leaderboard_reply(bot, 1, 11)).split("\n")
    assert lines[1] == "`1.` **U12**: 1 game won, 2 prompts won, 2 played"
    assert lines[2] == "`2.` **U14**: 1 game won, 1 prompt won, 1 played"
    assert lines[3] == "`3.` **U13**: 0 games won, 1 prompt won, 1 played"
    assert lines[4] == "`4.` **U11**: 0 games won, 0 prompts won, 2 played"
    member = await flow.leaderboard_reply(bot, 1, 11, member_id=12)
    assert member.startswith("**U12**: 1 game won, 2 prompts won, 2 played\nRecent prompt wins:")
    assert member.count("• Most likely to do thing") == 2
    stranger = await flow.leaderboard_reply(bot, 1, 11, member_id=99)
    assert stranger == "**U99** hasn't played a finished game yet."
    assert (await flow.leaderboard_reply(bot, 2, 11)).startswith("No finished")


async def test_leaderboard_ignores_running_and_ended_early(bot):
    game = await running(bot, rounds=5)
    for uid in (11, 12, 13):
        await vote(bot, game, uid, 13)
    await close_current(bot, game)
    await vote(bot, game, 11, 11)
    assert (await flow.leaderboard_reply(bot, 1, 11)).startswith("No finished")
    await flow.finish(bot, game, note="ended by U11")
    lines = (await flow.leaderboard_reply(bot, 1, 11)).split("\n")
    assert lines[1] == "`1.` **U13**: 1 game won, 1 prompt won, 1 played"
    assert lines[2] == "`2.` **U11**: 0 games won, 0 prompts won, 1 played"


async def test_leaderboard_departed_member_falls_back_to_mention(bot):
    await play(bot, (11, 12, 13), [[(11, 12)]])

    async def fetch_user(user_id):
        raise aiohttp.ClientError("gone")

    bot.get_user = lambda user_id: None
    bot.fetch_user = fetch_user
    assert "**<@12>**: 1 game won" in await flow.leaderboard_reply(bot, 1, 11)


async def test_leaderboard_shows_caller_outside_top_ten(bot):
    users = tuple(range(101, 113))
    await play(bot, users, [[(uid, uid) for uid in users[:10]]])
    lines = (await flow.leaderboard_reply(bot, 1, 112)).split("\n")
    assert len(lines) == 13
    assert lines[11] == ""
    assert lines[12] == "`12.` **U112**: 0 games won, 0 prompts won, 1 played"
    assert len((await flow.leaderboard_reply(bot, 1, 101)).split("\n")) == 11

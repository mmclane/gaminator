"""The Spectrum lobby card buttons."""

import pytest
from spectrum_fakes import CHANNEL_ID, add_card, card_names, interaction_for, make_bot, reply_of

from gaminator.games.spectrum.lobby import TEMPLATE, handle_lobby_click, lobby_custom_id
from gaminator.games.spectrum.services import game as flow


@pytest.fixture
async def bot(db):
    return make_bot(db)


async def lobby(bot, users=(11, 12, 13)):
    game = await bot.repo.create_game(1, "Friday", users[0], CHANNEL_ID)
    for uid in users:
        await bot.repo.join(game["id"], uid)
    return game, await add_card(bot, game)


def test_lobby_custom_ids():
    for action in ("join", "leave", "rules", "start"):
        match = TEMPLATE.fullmatch(lobby_custom_id(action, 42))
        assert match["action"] == action and match["game"] == "42"
    assert TEMPLATE.fullmatch("spectrum:target:5") is None
    assert TEMPLATE.fullmatch("spectrum:guess:5") is None
    assert TEMPLATE.fullmatch("spectrum:kick:5") is None
    assert TEMPLATE.fullmatch("mostlikely:join:5") is None
    assert TEMPLATE.fullmatch("fiftyfifty:join:5") is None


async def test_button_join_updates_card(bot):
    game, card = await lobby(bot, users=(11,))
    click = interaction_for(bot, 12)
    await handle_lobby_click(click, "join", game["id"])
    assert reply_of(click) == "You're in."
    assert click.followup.send.await_args.kwargs["ephemeral"] is True
    assert card_names(card) == ["U11", "U12"]
    again = interaction_for(bot, 12)
    await handle_lobby_click(again, "join", game["id"])
    assert reply_of(again) == "You're already in."


async def test_button_leave_updates_card(bot):
    game, card = await lobby(bot)
    click = interaction_for(bot, 13)
    await handle_lobby_click(click, "leave", game["id"])
    assert reply_of(click) == "You've left the game."
    assert card_names(card) == ["U11", "U12"]
    outsider = interaction_for(bot, 99)
    await handle_lobby_click(outsider, "leave", game["id"])
    assert reply_of(outsider) == "You're not in this game."


async def test_button_rules(bot):
    game, _ = await lobby(bot)
    click = interaction_for(bot, 99)
    await handle_lobby_click(click, "rules", game["id"])
    assert reply_of(click) == flow.rules_text(game, 120, 120, 3)
    assert click.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_button_start_refused_for_non_host(bot):
    game, card = await lobby(bot)
    click = interaction_for(bot, 12)
    await handle_lobby_click(click, "start", game["id"])
    assert reply_of(click) == "Only the host or a game admin can start."
    assert (await bot.repo.get_game(game["id"]))["status"] == "open"
    assert card.edits == []


async def test_button_start_too_few_players(bot):
    game, _ = await lobby(bot, users=(11, 12))
    click = interaction_for(bot, 11)
    await handle_lobby_click(click, "start", game["id"])
    assert reply_of(click) == "Need at least 3 players to start; 2 joined so far."


async def test_button_start_removes_start(bot):
    game, card = await lobby(bot)
    click = interaction_for(bot, 11)
    await handle_lobby_click(click, "start", game["id"])
    assert "has started with 3 players and 1 round." in reply_of(click)
    assert (await bot.repo.get_game(game["id"]))["max_rounds"] == 1
    assert [c.item.label for c in card.view.children] == ["Join", "Leave", "Rules"]
    assert card.embed.title == "🌈 Friday: in progress"
    again = interaction_for(bot, 11)
    await handle_lobby_click(again, "start", game["id"])
    assert reply_of(again) == "The game has already started."
    admin = interaction_for(bot, 50, manage_guild=True)
    await handle_lobby_click(admin, "start", game["id"])
    assert reply_of(admin) == "The game has already started."


async def test_button_on_finished_game(bot):
    game, _ = await lobby(bot)
    await bot.repo.finish_game(game["id"])
    for action in ("join", "leave", "rules", "start"):
        click = interaction_for(bot, 11)
        await handle_lobby_click(click, action, game["id"])
        assert reply_of(click) == "That game is over."
    missing = interaction_for(bot, 11)
    await handle_lobby_click(missing, "join", 9999)
    assert reply_of(missing) == "That game is over."

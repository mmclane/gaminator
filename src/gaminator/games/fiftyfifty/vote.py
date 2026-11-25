"""The A and B vote buttons and the guess menu that follows a vote.

Their custom_ids carry the question id, so they survive a restart.
"""

from __future__ import annotations

import re

import discord

from ...core.util import GameError, describe_error, respond
from .context import context
from .services import game as flow
from .services.rules import CLOSED, MAX_PLAYERS, guess_problem, vote_problem

TEMPLATE = re.compile(r"fiftyfifty:vote:(?P<question>[0-9]+):(?P<choice>a|b)")
GUESS_TEMPLATE = re.compile(r"fiftyfifty:guess:(?P<question>[0-9]+)")
GUESS_PLACEHOLDER = "How many will pick A?"


def vote_custom_id(question_id: int, choice: str) -> str:
    return f"fiftyfifty:vote:{question_id}:{choice}"


class VoteButton(discord.ui.DynamicItem[discord.ui.Button], template=TEMPLATE):
    def __init__(self, question_id: int, choice: str):
        super().__init__(
            discord.ui.Button(
                custom_id=vote_custom_id(question_id, choice),
                label=choice.upper(),
                style=discord.ButtonStyle.primary,
            )
        )
        self.question_id = question_id
        self.choice = choice

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ):
        return cls(int(match["question"]), match["choice"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_vote(interaction, self.question_id, self.choice)


def vote_view(question_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(VoteButton(question_id, "a"))
    view.add_item(VoteButton(question_id, "b"))
    return view


def guess_custom_id(question_id: int) -> str:
    return f"fiftyfifty:guess:{question_id}"


def guess_label(count: int) -> str:
    if count == 0:
        return "Nobody picks A"
    return f"{count} pick{'s' if count == 1 else ''} A"


def guess_options(most: int, current: int | None = None) -> list[discord.SelectOption]:
    """One entry per possible number of A votes, from 0 to ``most``."""
    top = max(0, min(most, MAX_PLAYERS - 1))
    return [
        discord.SelectOption(label=guess_label(n), value=str(n), default=n == current)
        for n in range(top + 1)
    ]


class GuessSelect(discord.ui.DynamicItem[discord.ui.Select], template=GUESS_TEMPLATE):
    def __init__(
        self,
        question_id: int,
        most: int = 0,
        current: int | None = None,
        options: list[discord.SelectOption] | None = None,
    ):
        super().__init__(
            discord.ui.Select(
                custom_id=guess_custom_id(question_id),
                placeholder=GUESS_PLACEHOLDER,
                min_values=1,
                max_values=1,
                options=options or guess_options(most, current),
            )
        )
        self.question_id = question_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Select, match: re.Match[str], /
    ):
        return cls(int(match["question"]), options=list(item.options))

    async def callback(self, interaction: discord.Interaction) -> None:
        values = self.item.values
        await handle_guess(interaction, self.question_id, values[0] if values else "")


def guess_view(question_id: int, most: int, current: int | None = None) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(GuessSelect(question_id, most, current))
    return view


async def voting_context(interaction: discord.Interaction, question_id: int):
    """(game, question, voter, on_roster) for a click on a question's buttons or menu."""
    repo = context(interaction.client).repo
    q = await repo.get_question(question_id)
    game = await repo.get_game(q["game_id"]) if q else None
    voter = await repo.get_player(game["id"], interaction.user.id) if game else None
    on_roster = bool(q and voter) and await repo.on_roster(q["round_id"], voter["id"])
    return game, q, voter, on_roster


async def handle_vote(interaction: discord.Interaction, question_id: int, choice: str) -> None:
    """Record a vote and answer privately with the guess menu."""
    bot = interaction.client
    repo = context(bot).repo
    try:
        game, q, voter, on_roster = await voting_context(interaction, question_id)
        problem = vote_problem(game, q, voter, on_roster)
        if problem:
            await respond(interaction, problem)
            return
        if not await repo.cast_vote(q["id"], voter["id"], choice):
            await respond(interaction, CLOSED)
            return
        guess = await repo.get_guess(q["id"], voter["id"])
        current = guess["a_votes"] if guess else None
        await respond(
            interaction,
            flow.voted_text(choice, current),
            view=guess_view(q["id"], await repo.most_votes(q["id"]), current),
        )
        await flow.step_after_input(bot, game, q, interaction.message)
    except GameError as e:
        await respond(interaction, describe_error(e) or "That didn't work.")


async def handle_guess(interaction: discord.Interaction, question_id: int, value: str) -> None:
    """Record a guess at the number of A votes. It can be changed until the question closes."""
    bot = interaction.client
    repo = context(bot).repo
    try:
        game, q, voter, on_roster = await voting_context(interaction, question_id)
        guess = int(value) if value.isdecimal() else -1
        voted = bool(q and voter) and await repo.get_vote(q["id"], voter["id"]) is not None
        problem = guess_problem(game, q, voter, on_roster, voted, guess)
        if problem:
            await respond(interaction, problem)
            return
        if not await repo.set_guess(q["id"], voter["id"], guess):
            await respond(interaction, CLOSED)
            return
        await respond(interaction, flow.guessed_text(guess))
        await flow.step_after_input(bot, game, q)
    except GameError as e:
        await respond(interaction, describe_error(e) or "That didn't work.")

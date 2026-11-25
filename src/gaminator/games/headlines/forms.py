"""The Answer and Write headline buttons, both pop-ups, and the handler that saves a submit.

The buttons sit on the public step messages; their custom_ids carry the round id, so they
survive a restart. A pop-up is never kept in memory: every submit is read from the raw
interaction by ``handle_submit``, so one opened before a restart can still be submitted after it.
"""

from __future__ import annotations

import logging
import re

import discord

from ...core.messaging import NETWORK_ERRORS
from ...core.util import GameError, describe_error, respond
from .context import context
from .services import game as flow
from .services.rules import ANSWER_LIMIT, HEADLINE_LIMIT, answer_problem, write_problem

log = logging.getLogger(__name__)
ANSWER_TEMPLATE = re.compile(r"headlines:answer:(?P<round>[0-9]+)")
WRITE_TEMPLATE = re.compile(r"headlines:write:(?P<round>[0-9]+)")
SUBMIT = re.compile(r"headlines:(?P<kind>answerform|headlineform):(?P<round>[0-9]+)")
ANSWER_TITLE = "Headlines: your answer"
HEADLINE_TITLE = "Headlines: your headline"
ANSWER_INPUT = "answer"
HEADLINE_INPUT = "headline"
DESCRIPTION_LIMIT = 100


def answer_custom_id(round_id: int) -> str:
    return f"headlines:answer:{round_id}"


def write_custom_id(round_id: int) -> str:
    return f"headlines:write:{round_id}"


def answer_form_id(round_id: int) -> str:
    return f"headlines:answerform:{round_id}"


def headline_form_id(round_id: int) -> str:
    return f"headlines:headlineform:{round_id}"


class AnswerButton(discord.ui.DynamicItem[discord.ui.Button], template=ANSWER_TEMPLATE):
    def __init__(self, round_id: int):
        super().__init__(
            discord.ui.Button(
                custom_id=answer_custom_id(round_id),
                label="Answer",
                emoji="✏️",
                style=discord.ButtonStyle.primary,
            )
        )
        self.round_id = round_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ):
        return cls(int(match["round"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_answer_click(interaction, self.round_id)


class WriteButton(discord.ui.DynamicItem[discord.ui.Button], template=WRITE_TEMPLATE):
    def __init__(self, round_id: int):
        super().__init__(
            discord.ui.Button(
                custom_id=write_custom_id(round_id),
                label="Write headline",
                emoji="🗞️",
                style=discord.ButtonStyle.primary,
            )
        )
        self.round_id = round_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ):
        return cls(int(match["round"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_write_click(interaction, self.round_id)


def answer_view(round_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(AnswerButton(round_id))
    return view


def write_view(round_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(WriteButton(round_id))
    return view


def answer_modal(ctx) -> discord.ui.Modal:
    """The answer form, filled in with the player's answer so far.

    It is stopped before it is sent, so discord.py never keeps it in memory. That only holds
    when it is built inside the running event loop, as a click handler does. The question
    appears twice: in the text block at the top, and as plain text under the label for a client
    that doesn't draw the text block. ``ctx`` is the player's round_context row.
    """
    modal = discord.ui.Modal(
        title=ANSWER_TITLE, custom_id=answer_form_id(ctx["round_id"]), timeout=None
    )
    modal.add_item(discord.ui.TextDisplay(flow.question_block(ctx["question"])))
    modal.add_item(
        discord.ui.Label(
            text="Your answer",
            description=ctx["question"][:DESCRIPTION_LIMIT],
            component=discord.ui.TextInput(
                custom_id=ANSWER_INPUT,
                style=discord.TextStyle.short,
                min_length=1,
                max_length=ANSWER_LIMIT,
                default=ctx["answer"] or None,
                placeholder="A few words is plenty",
            ),
        )
    )
    modal.stop()
    return modal


def headline_modal(ctx) -> discord.ui.Modal:
    """The headline form: the quote the player was handed, never its question or its author."""
    modal = discord.ui.Modal(
        title=HEADLINE_TITLE, custom_id=headline_form_id(ctx["round_id"]), timeout=None
    )
    modal.add_item(discord.ui.TextDisplay(flow.quote_block(ctx["quote"])))
    modal.add_item(
        discord.ui.Label(
            text="Your headline",
            description=ctx["quote"][:DESCRIPTION_LIMIT],
            component=discord.ui.TextInput(
                custom_id=HEADLINE_INPUT,
                style=discord.TextStyle.short,
                min_length=1,
                max_length=HEADLINE_LIMIT,
                default=ctx["headline"] or None,
                placeholder="Make it as funny as you can",
            ),
        )
    )
    modal.stop()
    return modal


async def open_modal(interaction: discord.Interaction, modal: discord.ui.Modal, round_id: int):
    """Send a pop-up as the first response. A click that expired is logged and dropped."""
    try:
        await interaction.response.send_modal(modal)
    except discord.NotFound as e:
        log.warning(
            "headlines: could not open a form for user %s on round %s, the click expired: %s",
            interaction.user.id,
            round_id,
            e,
        )


async def handle_answer_click(interaction: discord.Interaction, round_id: int) -> None:
    """Open the answer form as the first response. One database read: there are 3 seconds."""
    try:
        ctx = await context(interaction.client).repo.round_context(round_id, interaction.user.id)
        problem = answer_problem(ctx)
        if problem:
            await respond(interaction, problem)
            return
        await open_modal(interaction, answer_modal(ctx), round_id)
    except GameError as e:
        await respond(interaction, describe_error(e) or "That didn't work.")


async def handle_write_click(interaction: discord.Interaction, round_id: int) -> None:
    """Open the headline form as the first response, or say privately why there is none."""
    try:
        ctx = await context(interaction.client).repo.round_context(round_id, interaction.user.id)
        problem = write_problem(ctx)
        if problem:
            await respond(interaction, problem)
            return
        await open_modal(interaction, headline_modal(ctx), round_id)
    except GameError as e:
        await respond(interaction, describe_error(e) or "That didn't work.")


def value_from_submit(data, input_id: str) -> str | None:
    """One text input's value from a raw modal submit, whether it sits in a label or an action row.

    None when it is missing or isn't text.
    """
    components = data.get("components") if isinstance(data, dict) else None
    for top in components if isinstance(components, list) else []:
        if not isinstance(top, dict):
            continue
        inner = top.get("components") if top.get("type") == 1 else [top.get("component")]
        for item in inner if isinstance(inner, list) else []:
            if (
                isinstance(item, dict)
                and item.get("type") == 4
                and item.get("custom_id") == input_id
                and isinstance(item.get("value"), str)
            ):
                return item["value"]
    return None


async def handle_submit(interaction: discord.Interaction) -> bool:
    """Save an answer or a headline from a pop-up submit. False, without responding, when it isn't ours.

    The player is answered before the step is checked for closing, so the reveal never delays
    their reply.
    """
    if interaction.type != discord.InteractionType.modal_submit:
        return False
    data = interaction.data if isinstance(interaction.data, dict) else {}
    match = SUBMIT.fullmatch(str(data.get("custom_id", "")))
    if match is None:
        return False
    try:
        await interaction.response.defer(ephemeral=True)
    except NETWORK_ERRORS as e:
        log.warning("headlines: could not defer a %s submit: %s", match["kind"], e)
    bot, round_id, user_id = interaction.client, int(match["round"]), interaction.user.id
    is_answer = match["kind"] == "answerform"
    try:
        raw = value_from_submit(data, ANSWER_INPUT if is_answer else HEADLINE_INPUT) or ""
        if is_answer:
            text = await flow.submit_answer(bot, round_id, user_id, raw)
        else:
            text = await flow.submit_headline(bot, round_id, user_id, raw)
    except GameError as e:
        text = describe_error(e) or "That didn't work."
    except Exception:
        log.exception("headlines: %s submit failed for round %s", match["kind"], round_id)
        text = "Something went wrong; the logs have details."
    try:
        await respond(interaction, text)
    except NETWORK_ERRORS as e:
        log.warning("headlines: could not answer a %s submit: %s", match["kind"], e)
    try:
        await flow.close_step_if_done(bot, round_id)
    except Exception:
        log.exception("headlines: closing round %s after a submit failed", round_id)
    return True

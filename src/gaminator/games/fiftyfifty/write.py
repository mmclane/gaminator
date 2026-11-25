"""The Write button, the two-option form it opens, and the handler that saves a submitted form.

The form is never kept in memory: every submit is read from the raw interaction by
``handle_question_submit``, so a form opened before a restart can still be submitted after it.
"""

from __future__ import annotations

import logging
import re

import discord

from ...core.messaging import NETWORK_ERRORS
from ...core.util import GameError, describe_error, respond
from .context import context
from .services import game as flow
from .services.prompts import topic_text
from .services.rules import OPTION_LIMIT, write_problem

log = logging.getLogger(__name__)
TEMPLATE = re.compile(r"fiftyfifty:write:(?P<round>[0-9]+)")
MODAL_TITLE = "Fifty-Fifty: write your question"
MODAL_INTRO = (
    "**Would you rather...**\nWrite both options. Aim for a question this group splits 50/50."
)
OPTION_INPUT_IDS = ("a", "b")
OPTION_LABELS = {"a": "Option A", "b": "Option B"}
OPTION_HELP = {"a": "Would you rather...", "b": "...or would you rather"}
DESCRIPTION_LIMIT = 100
SUBMIT_TEMPLATE = re.compile(r"fiftyfifty:blank:(?P<round>[0-9]+)")


def write_custom_id(round_id: int) -> str:
    return f"fiftyfifty:write:{round_id}"


def blank_custom_id(round_id: int) -> str:
    return f"fiftyfifty:blank:{round_id}"


class WriteButton(discord.ui.DynamicItem[discord.ui.Button], template=TEMPLATE):
    def __init__(self, round_id: int):
        super().__init__(
            discord.ui.Button(
                custom_id=write_custom_id(round_id),
                label="Write",
                emoji="✍️",
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


def question_modal(
    round_id: int, topic: str | None, option_a: str | None = None, option_b: str | None = None
) -> discord.ui.Modal:
    """The two-option form. Stopped before it is sent, so discord.py never keeps it in memory.

    That only holds when it is built inside the running event loop, as a click handler does.
    The topic appears twice: as markdown at the top, and as plain text under the first label for
    a client that doesn't draw the text block.
    """
    modal = discord.ui.Modal(title=MODAL_TITLE, custom_id=blank_custom_id(round_id), timeout=None)
    intro = MODAL_INTRO + (f"\n{topic_text(topic)}" if topic else "")
    modal.add_item(discord.ui.TextDisplay(intro))
    hints = {"a": f"Topic idea: {topic}"[:DESCRIPTION_LIMIT] if topic else None, "b": None}
    for input_id, current in zip(OPTION_INPUT_IDS, (option_a, option_b), strict=True):
        modal.add_item(
            discord.ui.Label(
                text=OPTION_LABELS[input_id],
                description=hints[input_id],
                component=discord.ui.TextInput(
                    custom_id=input_id,
                    style=discord.TextStyle.short,
                    min_length=1,
                    max_length=OPTION_LIMIT,
                    default=current or None,
                    placeholder=OPTION_HELP[input_id],
                ),
            )
        )
    modal.stop()
    return modal


async def handle_write_click(interaction: discord.Interaction, round_id: int) -> None:
    """Open the form as the first response. One database read, since there are 3 seconds to answer."""
    try:
        ctx = await context(interaction.client).repo.write_context(round_id, interaction.user.id)
        problem = write_problem(ctx)
        if problem:
            await respond(interaction, problem)
            return
        written = ctx["answered_at"] and ctx["answer"] is None
        modal = question_modal(
            round_id,
            ctx["topic"],
            ctx["option_a"] if written else None,
            ctx["option_b"] if written else None,
        )
        try:
            await interaction.response.send_modal(modal)
        except discord.NotFound as e:
            log.warning(
                "fiftyfifty: could not open the form for user %s in round %s, the click expired: %s",
                interaction.user.id,
                round_id,
                e,
            )
    except GameError as e:
        await respond(interaction, describe_error(e) or "That didn't work.")


def options_from_submit(data) -> tuple[str | None, str | None]:
    """The two text inputs' values from a raw modal submit, each in a label or an action row.

    A value that is missing or isn't text comes back as None.
    """
    found: dict[str, str] = {}
    components = data.get("components") if isinstance(data, dict) else None
    for top in components if isinstance(components, list) else []:
        if not isinstance(top, dict):
            continue
        inner = top.get("components") if top.get("type") == 1 else [top.get("component")]
        for item in inner if isinstance(inner, list) else []:
            if (
                isinstance(item, dict)
                and item.get("type") == 4
                and item.get("custom_id") in OPTION_INPUT_IDS
                and isinstance(item.get("value"), str)
            ):
                found.setdefault(item["custom_id"], item["value"])
    return found.get("a"), found.get("b")


async def handle_question_submit(interaction: discord.Interaction) -> bool:
    """Save a question from a form submit. False, without responding, when it isn't ours."""
    if interaction.type != discord.InteractionType.modal_submit:
        return False
    data = interaction.data if isinstance(interaction.data, dict) else {}
    match = SUBMIT_TEMPLATE.fullmatch(str(data.get("custom_id", "")))
    if match is None:
        return False
    try:
        await interaction.response.defer(ephemeral=True)
    except NETWORK_ERRORS as e:
        log.warning("fiftyfifty: could not defer a question submit: %s", e)
    try:
        option_a, option_b = options_from_submit(data)
        text = await flow.submit_question(
            interaction.client,
            int(match["round"]),
            interaction.user.id,
            option_a or "",
            option_b or "",
        )
    except GameError as e:
        text = describe_error(e) or "That didn't work."
    except Exception:
        log.exception("fiftyfifty: question submit failed for round %s", match["round"])
        text = "Something went wrong; the logs have details."
    try:
        await respond(interaction, text)
    except NETWORK_ERRORS as e:
        log.warning("fiftyfifty: could not answer a question submit: %s", e)
    return True

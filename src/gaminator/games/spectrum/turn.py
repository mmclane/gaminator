"""The turn message buttons, both pop-ups, and the handler that saves a submitted pop-up.

Show my target and Write clue sit on the public turn message; their custom_ids carry the turn
id, so they survive a restart. A pop-up is never kept in memory: every submit is read from the
raw interaction by ``handle_submit``, so one opened before a restart can still be submitted
after it.
"""

from __future__ import annotations

import logging
import re

import discord

from ...core.messaging import NETWORK_ERRORS
from ...core.util import GameError, describe_error, respond
from .context import context
from .services import game as flow
from .services.rules import CLUE_LIMIT, clue_submit_problem
from .services.scales import Scale, scale_text

log = logging.getLogger(__name__)
TARGET_TEMPLATE = re.compile(r"spectrum:target:(?P<turn>[0-9]+)")
WRITE_TEMPLATE = re.compile(r"spectrum:writeclue:(?P<turn>[0-9]+)")
SUBMIT = re.compile(r"spectrum:(?P<kind>clueform|guessform):(?P<turn>[0-9]+)")
CLUE_TITLE = "Spectrum: your clue"
GUESS_TITLE = "Spectrum: your guess"
CLUE_INPUT = "clue"
GUESS_INPUT = "guess"
DESCRIPTION_LIMIT = 100


def target_custom_id(turn_id: int) -> str:
    return f"spectrum:target:{turn_id}"


def write_custom_id(turn_id: int) -> str:
    return f"spectrum:writeclue:{turn_id}"


def clue_form_id(turn_id: int) -> str:
    return f"spectrum:clueform:{turn_id}"


def guess_form_id(turn_id: int) -> str:
    return f"spectrum:guessform:{turn_id}"


class TargetButton(discord.ui.DynamicItem[discord.ui.Button], template=TARGET_TEMPLATE):
    def __init__(self, turn_id: int):
        super().__init__(
            discord.ui.Button(
                custom_id=target_custom_id(turn_id),
                label="Show my target",
                emoji="🎯",
                style=discord.ButtonStyle.primary,
            )
        )
        self.turn_id = turn_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ):
        return cls(int(match["turn"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_target_click(interaction, self.turn_id)


class WriteClueButton(discord.ui.DynamicItem[discord.ui.Button], template=WRITE_TEMPLATE):
    def __init__(self, turn_id: int):
        super().__init__(
            discord.ui.Button(
                custom_id=write_custom_id(turn_id),
                label="Write clue",
                emoji="✍️",
                style=discord.ButtonStyle.secondary,
            )
        )
        self.turn_id = turn_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ):
        return cls(int(match["turn"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_write_click(interaction, self.turn_id)


def turn_view(turn_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(TargetButton(turn_id))
    view.add_item(WriteClueButton(turn_id))
    return view


def ends_hint(turn) -> str:
    """Which end is which, as plain text for a label's description."""
    return f"1 = {turn['left_end']}, 100 = {turn['right_end']}"


def clue_modal(turn) -> discord.ui.Modal:
    """The clue form. Stopped before it is sent, so discord.py never keeps it in memory.

    That only holds when it is built inside the running event loop, as a click handler does.
    The target appears twice: in the text block at the top, and as plain text under the label
    for a client that doesn't draw the text block. ``turn`` is the giver's turn_context row.
    """
    modal = discord.ui.Modal(
        title=CLUE_TITLE, custom_id=clue_form_id(turn["turn_id"]), timeout=None
    )
    modal.add_item(discord.ui.TextDisplay(flow.target_text(turn, in_form=True)))
    modal.add_item(
        discord.ui.Label(
            text="Your clue (no digits)",
            description=f"Target {turn['target']}: {ends_hint(turn)}"[:DESCRIPTION_LIMIT],
            component=discord.ui.TextInput(
                custom_id=CLUE_INPUT,
                style=discord.TextStyle.short,
                min_length=1,
                max_length=CLUE_LIMIT,
                placeholder="A word or a short phrase. No digits.",
            ),
        )
    )
    modal.stop()
    return modal


def guess_modal(turn, current: int | None) -> discord.ui.Modal:
    """The guess form, filled in with the player's guess so far. Stopped before it is sent."""
    modal = discord.ui.Modal(
        title=GUESS_TITLE, custom_id=guess_form_id(turn["turn_id"]), timeout=None
    )
    scale = Scale(turn["left_end"], turn["right_end"])
    clue = discord.utils.escape_markdown(turn["clue"])
    modal.add_item(discord.ui.TextDisplay(f"**{scale_text(scale)}**\nClue: **{clue}**"))
    modal.add_item(
        discord.ui.Label(
            text="Your guess, 1 to 100",
            description=ends_hint(turn)[:DESCRIPTION_LIMIT],
            component=discord.ui.TextInput(
                custom_id=GUESS_INPUT,
                style=discord.TextStyle.short,
                min_length=1,
                max_length=3,
                default=str(current) if current else None,
                placeholder="A whole number from 1 to 100",
            ),
        )
    )
    modal.stop()
    return modal


async def open_modal(interaction: discord.Interaction, modal: discord.ui.Modal, turn_id: int):
    """Send a pop-up as the first response. A click that expired is logged and dropped."""
    try:
        await interaction.response.send_modal(modal)
    except discord.NotFound as e:
        log.warning(
            "spectrum: could not open a form for user %s on turn %s, the click expired: %s",
            interaction.user.id,
            turn_id,
            e,
        )


async def handle_target_click(interaction: discord.Interaction, turn_id: int) -> None:
    """Show the clue giver their target privately. Anyone else gets a private refusal."""
    try:
        await interaction.response.defer(ephemeral=True)
        text = await flow.show_target(interaction.client, turn_id, interaction.user.id)
        await respond(interaction, text)
    except GameError as e:
        await respond(interaction, describe_error(e) or "That didn't work.")


async def handle_write_click(interaction: discord.Interaction, turn_id: int) -> None:
    """Open the clue form as the first response. One database read: there are 3 seconds to answer."""
    try:
        ctx = await context(interaction.client).repo.turn_context(turn_id, interaction.user.id)
        problem = clue_submit_problem(ctx)
        if problem:
            await respond(interaction, problem)
            return
        await open_modal(interaction, clue_modal(ctx), turn_id)
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
    """Save a clue or a guess from a pop-up submit. False, without responding, when it isn't ours.

    The player is answered before the turn is checked for closing, so the pause before a reveal
    never delays their reply.
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
        log.warning("spectrum: could not defer a %s submit: %s", match["kind"], e)
    bot, turn_id, user_id = interaction.client, int(match["turn"]), interaction.user.id
    is_clue = match["kind"] == "clueform"
    try:
        raw = value_from_submit(data, CLUE_INPUT if is_clue else GUESS_INPUT) or ""
        if is_clue:
            text = await flow.submit_clue(bot, turn_id, user_id, raw)
        else:
            text = await flow.submit_guess(bot, turn_id, user_id, raw)
    except GameError as e:
        text = describe_error(e) or "That didn't work."
    except Exception:
        log.exception("spectrum: %s submit failed for turn %s", match["kind"], turn_id)
        text = "Something went wrong; the logs have details."
    try:
        await respond(interaction, text)
    except NETWORK_ERRORS as e:
        log.warning("spectrum: could not answer a %s submit: %s", match["kind"], e)
    try:
        await flow.close_if_done(bot, turn_id)
    except Exception:
        log.exception("spectrum: closing turn %s after a submit failed", turn_id)
    return True

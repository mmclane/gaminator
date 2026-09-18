"""Shared helpers: time, emoji, user-facing errors, admin check, game lookup, replies.

``require_game`` and ``require_player`` work with any game repo that has ``get_current_game``
and ``get_player``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import discord
from discord import app_commands

log = logging.getLogger(__name__)

VARIATION_SELECTOR = "️"


def now() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def same_emoji(a: object, b: object) -> bool:
    """Compare emoji loosely: ``☠`` and ``☠️`` are the same reaction to a player."""
    return str(a).replace(VARIATION_SELECTOR, "") == str(b).replace(VARIATION_SELECTOR, "")


class GameError(app_commands.AppCommandError):
    """Base for user-facing errors. Subclassing AppCommandError routes them to the handler."""


class NoCurrentGame(GameError):
    pass


class WrongStatus(GameError):
    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


class NotGameAdmin(GameError):
    pass


class NotJoined(GameError):
    pass


class NotAlive(GameError):
    pass


class NotAPlayer(GameError):
    """The referenced user is not a living player in this game."""


async def is_game_admin(interaction: discord.Interaction) -> bool:
    """Caller has Manage Server, or holds the server's configured admin role. Raises otherwise."""
    if interaction.permissions.manage_guild:
        return True
    settings = interaction.client.guild_settings  # type: ignore[attr-defined]
    role_id = await settings.get_admin_role(interaction.guild_id)
    member = interaction.user
    if role_id and isinstance(member, discord.Member) and member.get_role(role_id):
        return True
    raise NotGameAdmin()


async def require_game(repo, guild_id: int, *statuses: str):
    game = await repo.get_current_game(guild_id)
    if game is None:
        raise NoCurrentGame()
    if statuses and game["status"] not in statuses:
        raise WrongStatus(game["status"])
    return game


async def require_player(repo, game, user_id: int, alive: bool = True):
    player = await repo.get_player(game["id"], user_id)
    if player is None:
        raise NotJoined()
    if alive and not player["alive"]:
        raise NotAlive()
    return player


def missing_channel_permissions(channel, me) -> list[str]:
    """Names of the permissions the bot lacks to run a game in ``channel`` (empty = all good)."""
    if channel is None or me is None or not hasattr(channel, "permissions_for"):
        return []
    perms = channel.permissions_for(me)
    needed = (
        ("view_channel", "View Channel"),
        ("send_messages", "Send Messages"),
        ("read_message_history", "Read Message History"),
    )
    if getattr(channel, "parent", None) is not None and hasattr(channel, "archived"):
        needed = needed + (("send_messages_in_threads", "Send Messages in Threads"),)
    return [label for attr, label in needed if not getattr(perms, attr)]


def signup_text(count: int, minimum: int) -> str:
    """'3 of at least 5 players signed up' style progress line for join/leave announcements."""
    if count >= minimum:
        return f"{count} player{'s' if count != 1 else ''} signed up. ✅ Enough to start!"
    more = minimum - count
    return f"{count} of at least {minimum} players signed up, {more} more needed to start."


async def respond(interaction: discord.Interaction, content: str | None = None, **kwargs):
    """Send an ephemeral reply whether or not the interaction has been deferred."""
    kwargs.setdefault("ephemeral", True)
    if interaction.response.is_done():
        return await interaction.followup.send(content, **kwargs)
    return await interaction.response.send_message(content, **kwargs)


def describe_error(error: Exception) -> str | None:
    """User-facing text for known errors; None means unexpected."""
    if isinstance(error, app_commands.CommandInvokeError):
        error = error.original  # type: ignore[assignment]
    if isinstance(error, NoCurrentGame):
        return "There is no game right now."
    if isinstance(error, WrongStatus):
        return f"That can't be done while the game is **{error.status}**."
    if isinstance(error, NotGameAdmin):
        return "You don't have permission to do that."
    if isinstance(error, NotJoined):
        return "You haven't joined this game. Join while sign-ups are open."
    if isinstance(error, NotAlive):
        return "You've been eliminated, so you can't do that. Better luck next game."
    if isinstance(error, NotAPlayer):
        return "That person isn't a living player in this game."
    if isinstance(error, app_commands.NoPrivateMessage):
        return "That command only works inside a server."
    if isinstance(error, discord.Forbidden):
        return "I don't have permission to do that in Discord. Check my channel permissions."
    if isinstance(error, GameError):
        return str(error) or "That didn't work."
    return None


async def handle_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    """Tree-wide error handler: report known errors to the user, log the rest."""
    text = describe_error(error)
    if text is None:
        log.error("unhandled command error", exc_info=error)
        text = "Something went wrong; the logs have details."
    try:
        await respond(interaction, text)
    except discord.HTTPException as e:
        log.warning(
            "could not report error to user %s: reply failed with %s; original error: %r",
            getattr(interaction.user, "id", "?"),
            e,
            error,
        )

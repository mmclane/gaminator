"""Discord-facing helpers every game uses: names, DMs, and the announce channel."""

from __future__ import annotations

import logging

import aiohttp
import discord

log = logging.getLogger(__name__)
# Discord's own errors plus transport-level failures (dropped connections, truncated bodies).
NETWORK_ERRORS = (discord.HTTPException, aiohttp.ClientError)
NO_MENTIONS = discord.AllowedMentions.none()


async def display_name(bot, guild_id: int | None, user_id: int) -> str:
    guild = bot.get_guild(guild_id) if guild_id else None
    if guild is not None:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except NETWORK_ERRORS:
                member = None
        if member is not None:
            return member.display_name
    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except NETWORK_ERRORS:
            return f"<@{user_id}>"
    return user.display_name


async def dm(bot, user_id: int, content: str | None = None, **kwargs) -> bool:
    """Send a DM; False when the user has DMs closed or can't be reached."""
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send(content, allowed_mentions=NO_MENTIONS, **kwargs)
        return True
    except NETWORK_ERRORS as e:
        log.info("could not DM user %s: %s", user_id, e)
        return False


async def announce(bot, guild_id: int, text: str) -> None:
    """Post in the server's announce channel, if one is configured."""
    channel_id = await bot.guild_settings.get_announce_channel(guild_id)
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            log.warning("announce channel %s not found for guild %s", channel_id, guild_id)
            return
    try:
        await channel.send(text, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException as e:
        log.warning("could not announce in %s: %s", channel_id, e)

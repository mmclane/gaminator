"""Gateway listeners: activity tracking, kill reactions, and the inactivity/bounty sweep."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from ...core.messaging import dm
from ...core.util import same_emoji
from .context import context
from .services.game import (
    bounty_due,
    eliminate_player,
    inactivity_deadline,
    is_inactive,
    needs_warning,
    place_bounty,
    title,
)
from .services.rules import judge_kill

log = logging.getLogger(__name__)
DEFAULT_SWEEP_MINUTES = 10


class AssassinEvents(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        settings = getattr(bot, "settings", None)
        minutes = getattr(settings, "sweep_minutes", DEFAULT_SWEEP_MINUTES)
        self.sweep.change_interval(minutes=minutes)
        self.sweep.start()

    def cog_unload(self) -> None:
        self.sweep.cancel()

    @property
    def repo(self):
        return context(self.bot).repo

    async def _channel_in_play(self, game, channel_id: int, parent_id: int | None) -> bool:
        allowed = await self.repo.list_channels(game["id"])
        return not allowed or channel_id in allowed or parent_id in allowed

    # -- activity --------------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        game = await self.repo.get_current_game(message.guild.id)
        if game is None or game["status"] != "active":
            return
        parent = getattr(message.channel, "parent_id", None)
        if not await self._channel_in_play(game, message.channel.id, parent):
            return
        await self.repo.touch_activity(game["id"], message.author.id)

    # -- kills -----------------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.user_id == getattr(self.bot.user, "id", None):
            return
        game = await self.repo.get_current_game(payload.guild_id)
        if game is None or game["status"] != "active":
            return
        if not same_emoji(payload.emoji, game["kill_emoji"]):
            return
        assassin = await self.repo.get_player(game["id"], payload.user_id)
        if assassin is None or not assassin["alive"]:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(payload.channel_id)
            except discord.HTTPException:
                return
        parent = getattr(channel, "parent_id", None)
        if not await self._channel_in_play(game, channel.id, parent):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return
        if message.author.bot:
            return
        victim = await self.repo.get_player(game["id"], message.author.id)
        is_bounty_target = victim is not None and victim["id"] == game["bounty_player_id"]
        aimed = victim is not None and (assassin["target_id"] == victim["id"] or is_bounty_target)
        if not aimed:
            # Random reactions to other people's messages are none of our business.
            return
        shielded = await self._is_shielded(message, game["shield_emoji"])
        replied_user, replied_content = await self._replied_to(message)
        verdict, method = judge_kill(
            assassin,
            victim,
            message.content,
            shielded,
            channel.last_message_id == message.id,
            replied_user,
            replied_content,
            game["bounty_player_id"],
        )
        if method == "poison":
            detail = assassin["poison_word"]
        elif method == "trap":
            detail = assassin["bait_word"]
        else:
            detail = (
                "bounty" if is_bounty_target and assassin["target_id"] != victim["id"] else None
            )

        if not verdict.ok:
            await dm(self.bot, assassin["user_id"], f"❌ No kill: {verdict.reason}")
            await self._remove_reaction(message, payload)
            return

        log.info(
            "assassin game %s: player %s eliminated %s by %s",
            game["id"],
            assassin["id"],
            victim["id"],
            method,
        )
        await eliminate_player(self.bot, game, victim, assassin, method, detail)

    @staticmethod
    async def _replied_to(message: discord.Message) -> tuple[int | None, str]:
        """(author id, content) of the message this one replies to, or (None, "")."""
        ref = message.reference
        if ref is None or ref.message_id is None:
            return None, ""
        resolved = ref.resolved
        if isinstance(resolved, discord.Message):
            return resolved.author.id, resolved.content
        try:
            original = await message.channel.fetch_message(ref.message_id)
        except discord.HTTPException:
            return None, ""
        return original.author.id, original.content

    @staticmethod
    async def _is_shielded(message: discord.Message, shield_emoji: str) -> bool:
        """The author has reacted to their own message with the shield emoji."""
        for reaction in message.reactions:
            if not same_emoji(reaction.emoji, shield_emoji):
                continue
            try:
                async for user in reaction.users():
                    if user.id == message.author.id:
                        return True
            except discord.HTTPException:
                return False
        return False

    @staticmethod
    async def _remove_reaction(message: discord.Message, payload) -> None:
        try:
            await message.remove_reaction(payload.emoji, discord.Object(id=payload.user_id))
        except discord.HTTPException:
            pass

    # -- inactivity and bounties -----------------------------------------------------------

    @tasks.loop(minutes=DEFAULT_SWEEP_MINUTES)
    async def sweep(self) -> None:
        try:
            for game in await self.repo.list_active_games():
                await self._sweep_game(game)
        except Exception:
            log.exception("assassin sweep failed")

    @sweep.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    async def _sweep_game(self, game) -> None:
        if bounty_due(game, await self.repo.last_action_at(game["id"])):
            await place_bounty(self.bot, game)
        for player in await self.repo.list_players(game["id"], alive_only=True):
            current = await self.repo.get_player_by_id(player["id"])
            if current is None or not current["alive"]:
                continue
            if is_inactive(game, current):
                log.info(
                    "assassin game %s: player %s eliminated for inactivity",
                    game["id"],
                    current["id"],
                )
                await eliminate_player(self.bot, game, current, None, "inactive")
                fresh = await self.repo.get_game(game["id"])
                if fresh is None or fresh["status"] != "active":
                    return
            elif needs_warning(game, current):
                deadline = inactivity_deadline(game, current)
                stamp = f"<t:{int(deadline.timestamp())}:R>" if deadline else "soon"
                await self.repo.mark_warned(current["id"])
                await dm(
                    self.bot,
                    current["user_id"],
                    f"💤 You've been quiet. Post something in the server {stamp} or you'll be "
                    f"eliminated from {title(game)}.",
                )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AssassinEvents(bot))

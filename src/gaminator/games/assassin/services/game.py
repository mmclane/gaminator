"""Game flow that touches Discord: DMs, announcements, eliminations, bounties, win detection."""

from __future__ import annotations

import logging
import random
from datetime import timedelta

from ....core.messaging import announce, display_name, dm
from ....core.util import now, parse_iso
from ..context import context
from .words import pick_word

log = logging.getLogger(__name__)
RNG = random.SystemRandom()
MIN_PLAYERS = 3


def required_players(bot) -> int:
    """Players needed to start, honouring the MIN_PLAYERS testing override (never below 2)."""
    override = getattr(bot.settings, "min_players", None)
    return max(2, override) if override is not None else MIN_PLAYERS


DEFAULT_GAME = {
    "kill_emoji": "☠️",
    "shield_emoji": "🛡️",
    "bounty_hours": 48,
    "max_wrong_reports": 3,
    "inactivity_hours": 24,
}

CAUSE_LABELS = {
    "poison": "poisoned",
    "quickdraw": "taken out in a quick draw",
    "trap": "lured into a trap",
    "arrested": "arrested",
    "false_reports": "eliminated for too many false reports",
    "inactive": "eliminated for going quiet",
    "removed": "removed by an organizer",
    "executed": "executed by the state",
    "left": "left the game",
}


def title(game) -> str:
    """How messages refer to the game: 'Assassin game **Friday Night**'."""
    return f"Assassin game **{game['name']}**"


# -- text ------------------------------------------------------------------------------------


def rules_text(game, footer: bool = True) -> str:
    """The rules. ``footer`` adds the slash-command hint; leave it out of DMs, where it can't run."""
    kill, shield = game["kill_emoji"], game["shield_emoji"]
    bounty_hours = game["bounty_hours"]
    bounty = (
        f"**Bounty** 💰: if nobody is eliminated for {bounty_hours} hours, a bounty is placed on "
        f"the player who has gone longest without a kill. *Anyone* can quick-draw them with "
        f"{kill} until the next elimination.\n"
        if bounty_hours
        else ""
    )
    return (
        "**How to play**\n"
        "Everyone has one target, one poison word, and one bait word. When your target is "
        "eliminated you inherit their target and get new words. Last one standing wins.\n\n"
        f"To eliminate your target, react {kill} to one of their messages. It counts if any of "
        "these is true:\n"
        "**Poison** ☠️: the message uses your poison word.\n"
        "**Trap** 🪤: the message is a reply to a message of yours that contains your bait word.\n"
        "**Quick draw** 🔪: it's their latest message and nobody has posted in that channel since.\n"
        f"**Shield** {shield}: react to your *own* message with {shield} and it can no longer be "
        "used against you. Shield before the assassin strikes.\n"
        + bounty
        + "**Report** `/assassin report`: think you know who's hunting you? Report them. If "
        f"you're right they're arrested and eliminated. {game['max_wrong_reports']} wrong "
        "reports and *you're* out.\n"
        f"**Stay active**: go {game['inactivity_hours']} hours without posting in the server and "
        "you're eliminated."
        + (
            "\n\nUse `/assassin status` in the server to see your target, poison word, and "
            "standing."
            if footer
            else ""
        )
    )


def intro_text(game) -> str:
    """Public message posted where the game is created: how to join and how it works."""
    kill, shield = game["kill_emoji"], game["shield_emoji"]
    return (
        f"🎯 {title(game)} is open for sign-ups! Join with `/assassin join`.\n\n"
        "**How it works**: everyone gets a secret target, a poison word, and a bait word by DM. "
        f"Take out your target by reacting {kill} to one of their messages: it counts when they "
        "used your poison word ☠️, when they replied to a message of yours containing your bait "
        "word 🪤, or when it's their latest message and nobody has posted since 🔪. Shield your "
        f"own messages with {shield}. Think you know who's hunting you? `/assassin report` them. "
        "Stay active or you're out. Last one standing wins.\n\n"
        "`/assassin rules` has the full rules."
    )


def assignment_text(game, target_name: str, word: str, bait: str | None) -> str:
    return (
        f"🎯 Your target is **{target_name}**.\n"
        f"☠️ Your poison word is **{word}**.\n"
        f"🪤 Your bait word is **{bait or '-'}**.\n\n"
        f"React {game['kill_emoji']} to one of their messages to strike. It counts if they used "
        "your poison word, replied to a message of yours containing your bait word, or if it's "
        "their latest message and nobody has posted since. Shielded messages don't count. Keep "
        "this to yourself."
    )


def elimination_text(
    game, victim_name: str, killer_name: str | None, cause: str, detail: str | None, remaining: int
) -> str:
    label = CAUSE_LABELS.get(cause, "eliminated")
    if cause == "left":
        line = f"🚪 **{victim_name}** left the game."
    elif cause == "poison":
        who = f" by **{killer_name}**" if killer_name and game["reveal_killer"] else ""
        word = f" (poison word: *{detail}*)" if detail else ""
        line = f"☠️ **{victim_name}** was {label}{who}{word}."
    elif cause == "quickdraw":
        who = f" by **{killer_name}**" if killer_name and game["reveal_killer"] else ""
        bounty = " The bounty has been collected." if detail == "bounty" else ""
        line = f"🔪 **{victim_name}** was {label}{who}.{bounty}"
    elif cause == "trap":
        who = f" by **{killer_name}**" if killer_name and game["reveal_killer"] else ""
        word = f" (bait word: *{detail}*)" if detail else ""
        line = f"🪤 **{victim_name}** was {label}{who}{word}."
    elif cause == "arrested":
        who = f" after **{killer_name}** reported them" if killer_name else ""
        line = f"🚔 **{victim_name}** was {label}{who}."
    elif cause == "false_reports":
        line = f"🙈 **{victim_name}** was {label}."
    elif cause == "inactive":
        line = f"💤 **{victim_name}** was {label}."
    elif cause == "executed":
        why = f" Reason: {detail}" if detail else ""
        line = f"⚖️ **{victim_name}** was {label}.{why}"
    else:
        line = f"💀 **{victim_name}** was {label}."
    tail = f" {remaining} player{'s' if remaining != 1 else ''} remain."
    return line + (tail if remaining > 1 else "")


def victim_dm_text(game, cause: str, killer_name: str | None, detail: str | None) -> str:
    if cause == "poison":
        who = f" **{killer_name}**" if killer_name and game["reveal_killer"] else " your assassin"
        return f"☠️ You said **{detail}**, and{who} was waiting. You've been eliminated."
    if cause == "quickdraw":
        who = f" **{killer_name}**" if killer_name and game["reveal_killer"] else " your assassin"
        if detail == "bounty":
            return f"🔪 The bounty on you was collected by{who}. You've been eliminated."
        return f"🔪 Too slow:{who} got to your message first. You've been eliminated."
    if cause == "trap":
        who = f" **{killer_name}**" if killer_name and game["reveal_killer"] else " your assassin"
        return f"🪤 You replied to{who}'s bait (**{detail}**). You've been eliminated."
    if cause == "arrested":
        return f"🚔 **{killer_name}** figured out you were hunting them. You've been arrested."
    if cause == "false_reports":
        return "🙈 That was one wrong report too many. You've been eliminated."
    if cause == "inactive":
        return (
            f"💤 You went more than {game['inactivity_hours']} hours without posting. "
            "You've been eliminated."
        )
    if cause == "executed":
        why = f" Reason: {detail}" if detail else ""
        return f"⚖️ The state has executed you.{why}"
    return "💀 You've been removed from the game by an organizer."


# -- timing ----------------------------------------------------------------------------------


def inactivity_deadline(game, player):
    last = parse_iso(player["last_message_at"])
    if last is None:
        return None
    return last + timedelta(hours=game["inactivity_hours"])


def is_inactive(game, player, at=None) -> bool:
    deadline = inactivity_deadline(game, player)
    return deadline is not None and (at or now()) >= deadline


def needs_warning(game, player, at=None, fraction: float = 0.75) -> bool:
    """True once ``fraction`` of the inactivity window has passed and no warning was sent."""
    if player["inactivity_warned"]:
        return False
    last = parse_iso(player["last_message_at"])
    if last is None:
        return False
    warn_at = last + timedelta(hours=game["inactivity_hours"] * fraction)
    return (at or now()) >= warn_at


def bounty_due(game, last_action_at: str | None, at=None) -> bool:
    """A bounty is due when nothing has happened for ``bounty_hours`` and none is open."""
    hours = game["bounty_hours"]
    if not hours or game["bounty_player_id"]:
        return False
    last = parse_iso(last_action_at) or parse_iso(game["started_at"])
    if last is None:
        return False
    return (at or now()) >= last + timedelta(hours=hours)


def current_words(players) -> set[str]:
    out = set()
    for p in players:
        if p["alive"]:
            out.update(w for w in (p["poison_word"], p["bait_word"]) if w)
    return out


# -- Discord flow ----------------------------------------------------------------------------


async def send_assignment(bot, game, player) -> bool:
    if not player["target_id"]:
        return False
    target = await context(bot).repo.get_player_by_id(player["target_id"])
    if target is None:
        return False
    name = await display_name(bot, game["guild_id"], target["user_id"])
    return await dm(
        bot,
        player["user_id"],
        assignment_text(game, name, player["poison_word"], player["bait_word"]),
    )


async def eliminate_player(bot, game, victim, by_player, cause: str, detail: str | None = None):
    """Eliminate ``victim``, re-target their hunter, notify everyone involved, and check for a win.

    Returns the hunter's refreshed row (or None).
    """
    ctx = context(bot)
    alive = await ctx.repo.list_players(game["id"], alive_only=True)
    used = current_words(alive)
    new_word = pick_word(ctx.words, RNG, exclude=used)
    new_bait = pick_word(ctx.words, RNG, exclude=used | {new_word})
    hunter = await ctx.repo.eliminate(
        victim["id"], by_player["id"] if by_player else None, cause, new_word, new_bait, detail
    )

    victim_name = await display_name(bot, game["guild_id"], victim["user_id"])
    killer_name = (
        await display_name(bot, game["guild_id"], by_player["user_id"]) if by_player else None
    )
    remaining = await ctx.repo.alive_count(game["id"])

    if cause != "left":
        await dm(bot, victim["user_id"], victim_dm_text(game, cause, killer_name, detail))
    if by_player is not None and cause in ("poison", "quickdraw", "trap", "arrested"):
        verb = "arrested" if cause == "arrested" else "eliminated"
        await dm(bot, by_player["user_id"], f"✅ **{victim_name}** has been {verb}.")
    if hunter is not None and hunter["target_id"]:
        await send_assignment(bot, game, hunter)

    await announce(
        bot,
        game["guild_id"],
        elimination_text(game, victim_name, killer_name, cause, detail, remaining),
    )
    await check_win(bot, game)
    return hunter


async def place_bounty(bot, game) -> None:
    """Put a bounty on the living player who has gone longest without a kill."""
    repo = context(bot).repo
    player = await repo.bounty_candidate(game["id"])
    if player is None:
        return
    await repo.set_bounty(game["id"], player["id"])
    name = await display_name(bot, game["guild_id"], player["user_id"])
    log.info("assassin game %s: bounty placed on player %s", game["id"], player["id"])
    await dm(
        bot,
        player["user_id"],
        f"💰 Things have been too quiet, and you haven't made a kill in a while. There's a "
        f"bounty on you: *anyone* can quick-draw {game['kill_emoji']} your messages until the "
        "next elimination. Shield up and post carefully.",
    )
    await announce(
        bot,
        game["guild_id"],
        f"💰 **Bounty!** Nobody has been eliminated in {game['bounty_hours']} hours. "
        f"**{name}** has gone longest without a kill, so anyone can take them out with a quick "
        f"draw {game['kill_emoji']} until the next elimination.",
    )


async def check_win(bot, game) -> bool:
    """Finish the game when one (or zero) players remain. Returns True when it finished."""
    repo = context(bot).repo
    current = await repo.get_game(game["id"])
    if current is None or current["status"] != "active":
        return False
    alive = await repo.list_players(game["id"], alive_only=True)
    if len(alive) > 1:
        return False
    winner = alive[0] if alive else None
    await repo.finish_game(game["id"], winner["id"] if winner else None)
    if winner is not None:
        name = await display_name(bot, game["guild_id"], winner["user_id"])
        await dm(
            bot,
            winner["user_id"],
            f"# 🏆 You're the last one standing!\nYou win {title(game)}.",
        )
        await announce(
            bot,
            game["guild_id"],
            f"🏆 **{name}** is the last one standing and wins {title(game)} "
            f"with {winner['kills']} kill{'s' if winner['kills'] != 1 else ''}!",
        )
    else:
        await announce(bot, game["guild_id"], f"{title(game)} is over with no survivors.")
    return True

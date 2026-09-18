"""Pure decisions about whether an attempted elimination counts.

These functions take plain values (rows and booleans the listeners have already fetched) so
the rules can be tested without Discord.
"""

from __future__ import annotations

from dataclasses import dataclass

from .words import contains_word


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""


def judge_target(assassin, victim) -> Verdict:
    """Common checks: both alive players, and ``victim`` is ``assassin``'s current target."""
    if assassin is None or not assassin["alive"]:
        return Verdict(False, "You're not a living player in this game.")
    if victim is None or not victim["alive"]:
        return Verdict(False, "That person isn't a living player.")
    if assassin["id"] == victim["id"]:
        return Verdict(False, "You can't eliminate yourself.")
    if assassin["target_id"] != victim["id"]:
        return Verdict(False, "That's not your target.")
    return Verdict(True)


def judge_poison(assassin, victim, content: str, shielded: bool) -> Verdict:
    base = judge_target(assassin, victim)
    if not base.ok:
        return base
    if shielded:
        return Verdict(False, "That message is shielded.")
    word = assassin["poison_word"] or ""
    if not contains_word(content, word):
        return Verdict(False, f"That message doesn't contain your poison word (**{word}**).")
    return Verdict(True)


def judge_quickdraw(
    assassin, victim, is_latest: bool, shielded: bool, bounty_player_id: int | None = None
) -> Verdict:
    """Quick draw works on your target, or on the bounty player (anyone may collect a bounty)."""
    base = judge_target(assassin, victim)
    is_bounty = (
        victim is not None
        and bounty_player_id is not None
        and victim["id"] == bounty_player_id
        and assassin is not None
        and assassin["alive"]
        and victim["alive"]
        and assassin["id"] != victim["id"]
    )
    if not base.ok and not is_bounty:
        return base
    if shielded:
        return Verdict(False, "That message is shielded.")
    if not is_latest:
        return Verdict(False, "Too slow: someone else posted after that message.")
    return Verdict(True)


def judge_trap(
    assassin, victim, replied_to_user_id: int | None, replied_content: str, shielded: bool
) -> Verdict:
    """The victim replied to a message by the assassin that contains the assassin's bait word."""
    base = judge_target(assassin, victim)
    if not base.ok:
        return base
    if shielded:
        return Verdict(False, "That message is shielded.")
    if replied_to_user_id is None:
        return Verdict(False, "That message isn't a reply.")
    if replied_to_user_id != assassin["user_id"]:
        return Verdict(False, "They replied to someone else, not to you.")
    bait = assassin["bait_word"] or ""
    if not contains_word(replied_content, bait):
        return Verdict(
            False, f"The message they replied to doesn't contain your bait word (**{bait}**)."
        )
    return Verdict(True)


def judge_kill(
    assassin,
    victim,
    content: str,
    shielded: bool,
    is_latest: bool,
    replied_to_user_id: int | None,
    replied_content: str,
    bounty_player_id: int | None = None,
) -> tuple[Verdict, str | None]:
    """One trigger reaction, three methods. Returns (verdict, method).

    Tries poison (the message contains the poison word), then trap (it replies to the
    assassin's bait), then quick draw (it is the channel's latest message, or the victim carries
    the bounty). The first that fits wins; if none do, the reason covers all three.
    """
    poison = judge_poison(assassin, victim, content, shielded)
    if poison.ok:
        return poison, "poison"
    trap = judge_trap(assassin, victim, replied_to_user_id, replied_content, shielded)
    if trap.ok:
        return trap, "trap"
    quick = judge_quickdraw(assassin, victim, is_latest, shielded, bounty_player_id)
    if quick.ok:
        return quick, "quickdraw"
    if not judge_target(assassin, victim).ok:
        # Not their target: the quick-draw verdict already explains that (or the bounty case).
        return quick, None
    if shielded:
        return Verdict(False, "That message is shielded."), None
    return (
        Verdict(
            False,
            f"that message doesn't contain your poison word (**{assassin['poison_word']}**), "
            f"isn't a reply to a message of yours with your bait word "
            f"(**{assassin['bait_word']}**), and someone posted after it, so it's too slow for "
            "a quick draw.",
        ),
        None,
    )


def judge_report(reporter, accused) -> Verdict:
    """A report is correct when the accused is alive and currently targeting the reporter."""
    if reporter is None or not reporter["alive"]:
        return Verdict(False, "You're not a living player in this game.")
    if accused is None or not accused["alive"]:
        return Verdict(False, "That person isn't a living player.")
    if reporter["id"] == accused["id"]:
        return Verdict(False, "You can't report yourself.")
    if accused["target_id"] == reporter["id"]:
        return Verdict(True)
    return Verdict(False, "wrong")

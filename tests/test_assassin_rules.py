from gaminator.games.assassin.services.rules import (
    judge_poison,
    judge_quickdraw,
    judge_report,
    judge_target,
)


def row(**kw):
    base = {
        "id": 1,
        "alive": 1,
        "target_id": 2,
        "poison_word": "banana",
        "bait_word": "coffee",
        "user_id": 100,
    }
    base.update(kw)
    return base


def test_target_checks():
    me, them = row(id=1, target_id=2), row(id=2, target_id=3)
    assert judge_target(me, them).ok
    assert not judge_target(me, row(id=3)).ok
    assert not judge_target(me, None).ok
    assert not judge_target(row(alive=0), them).ok
    assert not judge_target(me, row(id=2, alive=0)).ok
    assert not judge_target(me, me).ok


def test_poison():
    me, them = row(id=1, target_id=2), row(id=2, target_id=3)
    assert judge_poison(me, them, "I want a banana", False).ok
    assert "shielded" in judge_poison(me, them, "I want a banana", True).reason
    assert "poison word" in judge_poison(me, them, "bananas", False).reason


def test_quickdraw():
    me, them = row(id=1, target_id=2), row(id=2, target_id=3)
    assert judge_quickdraw(me, them, True, False).ok
    assert "slow" in judge_quickdraw(me, them, False, False).reason
    assert "shielded" in judge_quickdraw(me, them, True, True).reason


def test_report():
    me = row(id=1, target_id=2)
    hunter = row(id=3, target_id=1)
    bystander = row(id=4, target_id=5)
    assert judge_report(me, hunter).ok
    assert judge_report(me, bystander).reason == "wrong"
    assert not judge_report(me, me).ok
    assert not judge_report(me, row(id=3, target_id=1, alive=0)).ok
    assert not judge_report(row(alive=0), hunter).ok


def test_quickdraw_bounty_lets_anyone_kill():
    me, other = row(id=1, target_id=2), row(id=5, target_id=6)
    assert not judge_quickdraw(me, other, True, False).ok
    assert judge_quickdraw(me, other, True, False, bounty_player_id=5).ok
    assert "slow" in judge_quickdraw(me, other, False, False, bounty_player_id=5).reason
    assert "shielded" in judge_quickdraw(me, other, True, True, bounty_player_id=5).reason
    assert not judge_quickdraw(me, me, True, False, bounty_player_id=1).ok


def test_trap():
    from gaminator.games.assassin.services.rules import judge_trap

    me, them = row(id=1, target_id=2, user_id=100), row(id=2, target_id=3, user_id=200)
    assert judge_trap(me, them, 100, "want some coffee?", False).ok
    assert "isn't a reply" in judge_trap(me, them, None, "", False).reason
    assert "someone else" in judge_trap(me, them, 300, "coffee", False).reason
    assert "bait word" in judge_trap(me, them, 100, "tea?", False).reason
    assert "shielded" in judge_trap(me, them, 100, "coffee", True).reason
    assert "not your target" in judge_trap(me, row(id=9), 100, "coffee", False).reason


def test_judge_kill_picks_the_method():
    from gaminator.games.assassin.services.rules import judge_kill

    me, them = row(id=1, target_id=2, user_id=100), row(id=2, target_id=3, user_id=200)
    # poison beats everything else
    v, m = judge_kill(me, them, "a banana!", False, True, 100, "coffee", None)
    assert v.ok and m == "poison"
    # trap when it's a reply to my bait
    v, m = judge_kill(me, them, "sure", False, False, 100, "want coffee?", None)
    assert v.ok and m == "trap"
    # quick draw when it's simply the latest message
    v, m = judge_kill(me, them, "hello", False, True, None, "", None)
    assert v.ok and m == "quickdraw"
    # none fit: one reason covering all three
    v, m = judge_kill(me, them, "hello", False, False, None, "", None)
    assert not v.ok and m is None
    assert "banana" in v.reason and "coffee" in v.reason and "quick draw" in v.reason
    # shield blocks every method
    v, m = judge_kill(me, them, "a banana!", True, True, 100, "coffee", None)
    assert not v.ok and "shielded" in v.reason
    # not my target
    v, m = judge_kill(me, row(id=5, target_id=6), "banana", False, True, None, "", None)
    assert not v.ok and "not your target" in v.reason
    # bounty: anyone can quick-draw the bounty player, but not poison them
    v, m = judge_kill(me, row(id=5, target_id=6), "banana", False, True, None, "", 5)
    assert v.ok and m == "quickdraw"
    v, m = judge_kill(me, row(id=5, target_id=6), "banana", False, False, None, "", 5)
    assert not v.ok and "slow" in v.reason

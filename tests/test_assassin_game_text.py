from datetime import UTC, datetime, timedelta

from gaminator.games.assassin.services.game import (
    bounty_due,
    elimination_text,
    is_inactive,
    needs_warning,
    rules_text,
)

GAME = {
    "name": "Test",
    "kill_emoji": "☠️",
    "shield_emoji": "🛡️",
    "bounty_hours": 48,
    "bounty_player_id": None,
    "started_at": "2026-01-01T00:00:00+00:00",
    "max_wrong_reports": 3,
    "inactivity_hours": 10,
    "reveal_killer": 1,
}


def test_elimination_text_reveal_toggle():
    text = elimination_text(GAME, "Ann", "Bob", "poison", "banana", 5)
    assert "Ann" in text and "Bob" in text and "banana" in text and "5 players remain" in text
    hidden = elimination_text({**GAME, "reveal_killer": 0}, "Ann", "Bob", "poison", "banana", 5)
    assert "Bob" not in hidden
    assert "remain" not in elimination_text(GAME, "Ann", "Bob", "quickdraw", None, 1)
    assert "reported" in elimination_text(GAME, "Ann", "Bob", "arrested", None, 3)
    trap = elimination_text(GAME, "Ann", "Bob", "trap", "coffee", 3)
    assert "trap" in trap and "coffee" in trap
    assert "bounty" in elimination_text(GAME, "Ann", "Bob", "quickdraw", "bounty", 3).lower()


def test_rules_mentions_settings():
    text = rules_text(GAME)
    assert "react ☠️" in text and "3 wrong reports" in text and "10 hours" in text
    assert "🪤" in text and "🔪" in text and "Bounty" in text
    assert "Bounty" not in rules_text({**GAME, "bounty_hours": 0})


def test_inactivity():
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    fresh = {"last_message_at": (now - timedelta(hours=1)).isoformat(), "inactivity_warned": 0}
    stale = {"last_message_at": (now - timedelta(hours=11)).isoformat(), "inactivity_warned": 0}
    warn = {"last_message_at": (now - timedelta(hours=8)).isoformat(), "inactivity_warned": 0}
    assert not is_inactive(GAME, fresh, now)
    assert is_inactive(GAME, stale, now)
    assert not needs_warning(GAME, fresh, now)
    assert needs_warning(GAME, warn, now)
    assert not needs_warning(GAME, {**warn, "inactivity_warned": 1}, now)
    assert not is_inactive(GAME, {"last_message_at": None, "inactivity_warned": 0}, now)


def test_executed_text():
    text = elimination_text(GAME, "Ann", None, "executed", "cheating", 4)
    assert "executed by the state" in text and "cheating" in text


def test_bounty_due():
    now = datetime(2026, 1, 3, 1, tzinfo=UTC)  # 49h after start
    assert bounty_due(GAME, None, now)
    assert not bounty_due(GAME, "2026-01-02T12:00:00+00:00", now)
    assert not bounty_due({**GAME, "bounty_hours": 0}, None, now)
    assert not bounty_due({**GAME, "bounty_player_id": 7}, None, now)


def test_intro():
    from gaminator.games.assassin.services.game import intro_text

    text = intro_text(GAME)
    assert "/assassin join" in text and "🔪" in text and "☠️" in text and "🪤" in text
    assert "/assassin rules" in text and "Test" in text


def test_rules_footer_is_optional():
    assert "/assassin status" in rules_text(GAME)
    assert "/assassin status" not in rules_text(GAME, footer=False)

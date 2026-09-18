from gaminator.games.imposter.services.game import (
    DEFAULT_GAME,
    crew_dm_text,
    imposter_dm_text,
    intro_text,
    result_text,
    rules_text,
    tally_text,
    winner_text,
)
from gaminator.games.imposter.services.rules import tally

GAME = {"name": "Test", "imposters": 2, "max_rounds": 5, "channel_id": 7, "imposter_hint": 1}
ROUND = {"number": 3, "prompt": "Name a food.", "hint": "It's about food."}


def test_rules_reflect_settings():
    text = rules_text(GAME)
    assert "2 players are" in text and "after 5 rounds" in text and "nobody is eliminated" in text
    assert "one player is" in rules_text(DEFAULT_GAME)
    assert "after 1 round." in rules_text({**DEFAULT_GAME, "max_rounds": 1})


def test_dms():
    crew = crew_dm_text(GAME, ROUND, "<#7>")
    assert "Name a food." in crew and "<#7>" in crew
    imp = imposter_dm_text(GAME, ROUND, ["Ann"], "<#7>")
    assert "Name a food." not in imp and "It's about food." in imp and "Ann" in imp
    no_clue = imposter_dm_text({**GAME, "imposter_hint": 0}, ROUND, ["Ann"], "<#7>")
    assert "It's about food." not in no_clue
    assert "no clue" in rules_text({**GAME, "imposter_hint": 0})
    assert "no clue" in intro_text({**GAME, "imposter_hint": 0})
    assert "fellow" not in imposter_dm_text(GAME, {**ROUND, "hint": None}, [], "<#7>").lower()


def test_results():
    ejected = {"id": 1, "imposter": 1}
    t = tally([1, 1, 2])
    text = result_text(GAME, ROUND, t, ejected, "Bob", 1)
    assert "Bob" in text and "were an imposter" in text and "1 imposter remain" in text
    assert "Name a food." in text and "2 rounds left" in text
    innocent = result_text(GAME, ROUND, t, {"id": 1, "imposter": 0}, "Bob", 2)
    assert "not" in innocent and "Nobody is eliminated" in innocent
    final = result_text(GAME, {**ROUND, "number": 5}, t, {"id": 1, "imposter": 0}, "Bob", 2)
    assert "left" not in final
    assert "round left" not in result_text(GAME, ROUND, t, ejected, "Bob", 0)
    assert "tied" in result_text(GAME, ROUND, tally([1, 2]), None, None, 2)
    assert "no accusation" in result_text(GAME, ROUND, tally([None]), None, None, 2).lower()
    assert "Nobody voted" in result_text(GAME, ROUND, tally([]), None, None, 2)
    assert tally_text(tally([1, 1, None]), {1: "Bob"}) == "Results: Bob 2, No accusation 1"


def test_winner():
    assert "crew wins" in winner_text(GAME, "crew", ["Ann", "Bob"], "")
    text = winner_text(GAME, "imposters", ["Ann"], "They survived 5 rounds.")
    assert "imposters win" in text and "survived" in text and "imposter was **Ann**" in text


def test_intro():

    text = intro_text(GAME)
    assert (
        "/imposter join" in text and "5 rounds, 2 imposters" in text and "/imposter rules" in text
    )
    solo = intro_text({**DEFAULT_GAME, "name": "Solo", "max_rounds": 1})
    assert "1 round, one imposter" in solo and "Solo" in solo


def test_rules_footer_is_optional():
    assert "/imposter status" in rules_text(GAME)
    assert "/imposter status" not in rules_text(GAME, footer=False)

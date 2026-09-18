import random

import pytest

from gaminator.games.imposter.services.rules import (
    CREW,
    IMPOSTERS,
    choose_imposters,
    min_players,
    outcome,
    tally,
)


def test_min_players():
    assert min_players(1) == 3
    assert min_players(2) == 5


def test_choose_imposters():
    ids = list(range(1, 8))
    chosen = choose_imposters(ids, 2, random.Random(1))
    assert len(chosen) == 2 and chosen <= set(ids)
    with pytest.raises(ValueError):
        choose_imposters(ids, 0, random.Random())
    with pytest.raises(ValueError):
        choose_imposters(ids, 8, random.Random())


def test_tally_plurality():
    t = tally([1, 1, 2, None])
    assert t.ejected == 1 and t.result == "ejected"
    assert t.counts == {1: 2, 2: 1, None: 1}


def test_tally_tie_and_nobody():
    t = tally([1, 2])
    assert t.ejected is None and t.result == "tie" and set(t.leaders) == {1, 2}
    assert tally([1, None]).result == "tie"
    t = tally([None, None, 3])
    assert t.ejected is None and t.result == "nobody"
    assert tally([]).result == "no_votes"


def test_outcome():
    assert outcome(3, 1, 1, 5) is None
    assert outcome(3, 0, 1, 5) == CREW  # every imposter caught
    assert outcome(1, 1, 1, 5) is None  # being outnumbered no longer matters
    assert outcome(3, 1, 5, 5) == IMPOSTERS  # survived every round
    assert outcome(3, 1, 4, 5) is None
    assert outcome(0, 1, 1, 5) == IMPOSTERS  # every crew member left
    assert outcome(0, 0, 1, 5) == CREW


def test_min_players_override():
    assert min_players(1, 2) == 2
    assert min_players(2, 2) == 3  # never fewer than one crew member
    assert min_players(1, 10) == 10

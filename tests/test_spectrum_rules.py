"""Pure Spectrum rules: clues, guesses, scoring, refusals and the idle checks."""

import pytest

from gaminator.games.spectrum.services import rules
from gaminator.games.spectrum.services.rules import (
    BAD_GUESS,
    CLUE_OVER,
    EMPTY_CLUE,
    GAME_OVER,
    GIVER_SITS_OUT,
    GUESS_OVER,
    JOINED_LATE,
    LEFT,
    NO_DIGITS,
    NOT_GIVER,
    NOT_YOUR_TARGET,
    ScoredTurn,
    Standing,
    clean_clue,
    closest_guess,
    clue_problem,
    clue_submit_problem,
    game_points,
    game_winners,
    giver_points,
    guess_points,
    guess_problem,
    leaderboard_rows,
    min_players,
    next_host,
    parse_guess,
    round_without_clue,
    standings,
    target_problem,
    too_few_to_continue,
    unguessed_streak,
)


def ctx(**changes):
    """A turn_context row for the clue giver of a turn in its clue step."""
    row = {
        "game_id": 1,
        "status": "active",
        "turn_id": 5,
        "giver_id": 21,
        "clue": None,
        "started_at": "2026-01-01T00:00:00+00:00",
        "closed_at": None,
        "giver_left_at": None,
        "player_id": 21,
        "left_at": None,
        "may_guess": 0,
        "my_guess": None,
    }
    row.update(changes)
    return row


def turn(giver, target, guesses, closed_at="2026-01-01T00:00:00+00:00"):
    return ScoredTurn(giver, "Calm", "Chaotic", target, "a clue", closed_at, tuple(guesses))


def test_min_players():
    assert rules.MIN_PLAYERS == 3 and rules.MAX_PLAYERS == 12
    assert min_players() == 3 and min_players(None) == 3
    assert min_players(2) == 2 and min_players(1) == 2 and min_players(5) == 5


def test_too_few_to_continue():
    assert too_few_to_continue(2, 3)
    assert not too_few_to_continue(3, 3)
    assert not too_few_to_continue(2, 2)


def test_next_host():
    players = [
        {"id": 1, "joined_at": "2026-01-01T00:00:03", "left_at": None},
        {"id": 2, "joined_at": "2026-01-01T00:00:01", "left_at": "2026-01-01T00:01:00"},
        {"id": 3, "joined_at": "2026-01-01T00:00:02", "left_at": None},
        {"id": 4, "joined_at": "2026-01-01T00:00:02", "left_at": None},
    ]
    assert next_host(players, 1)["id"] == 3
    assert next_host(players, 3)["id"] == 4
    assert next_host(players[:2], 1) is None


@pytest.mark.parametrize(
    ("target", "guess", "points"),
    [
        (73, 73, 100),
        (73, 72, 97),
        (73, 68, 85),
        (73, 63, 70),
        (73, 53, 40),
        (73, 40, 1),
        (73, 39, 0),
        (1, 100, 0),
    ],
)
def test_guess_points(target, guess, points):
    assert guess_points(guess, target) == points
    assert guess_points(target, guess) == points


@pytest.mark.parametrize(
    ("scores", "points"),
    [((), 0), ((91, 55, 0), 49), ((85, 84), 85), ((100,), 100)],
)
def test_giver_points(scores, points):
    assert giver_points(scores) == points


def test_clean_clue():
    assert clean_clue("  rain   at a\npicnic ") == "rain at a picnic"
    assert clean_clue("") is None and clean_clue(" \n\t ") is None
    assert clean_clue("x" * 81) == "x" * 80
    assert rules.CLUE_LIMIT == 80


def test_clue_problem():
    assert clue_problem("rain at a picnic") is None
    assert clue_problem("about 7 out of ten") == NO_DIGITS
    assert clue_problem("about ٧ out of ten") == NO_DIGITS
    assert clue_problem("level 7️⃣") == NO_DIGITS
    assert clue_problem("") == EMPTY_CLUE
    assert clue_problem("   ") == EMPTY_CLUE
    assert clue_problem("x" * 80 + "7") is None


def test_parse_guess():
    assert parse_guess("42") == 42
    assert parse_guess(" 7 ") == 7
    assert parse_guess("042") == 42
    assert parse_guess("1") == 1 and parse_guess("100") == 100
    for bad in ("0", "101", "4.5", "-3", "٤٢", "", "  ", "abc", "4 2", "²"):
        assert parse_guess(bad) is None, bad
    assert "**Guess** again" in BAD_GUESS


def test_target_problem():
    assert target_problem(None) == GAME_OVER
    assert target_problem(ctx(status="finished")) == GAME_OVER
    assert target_problem(ctx(closed_at="x")) == "That turn is over."
    assert target_problem(ctx(player_id=22)) == NOT_YOUR_TARGET
    assert target_problem(ctx(player_id=None)) == NOT_YOUR_TARGET
    assert target_problem(ctx(closed_at="x", player_id=22)) == "That turn is over."
    saved = target_problem(ctx(clue="rain"))
    assert saved == "Your clue is in. The target stays secret until the reveal."
    assert target_problem(ctx()) is None
    named = NOT_YOUR_TARGET.format(giver="Ann")
    assert named == "Only **Ann** can see the target. Hang on for their clue."


def test_clue_submit_problem():
    assert clue_submit_problem(None) == GAME_OVER
    assert clue_submit_problem(ctx(status="finished")) == GAME_OVER
    assert clue_submit_problem(ctx(closed_at="x")) == CLUE_OVER
    assert clue_submit_problem(ctx(clue="rain")) == CLUE_OVER
    assert clue_submit_problem(ctx(player_id=22)) == NOT_GIVER
    assert clue_submit_problem(ctx(player_id=None)) == NOT_GIVER
    assert clue_submit_problem(ctx(left_at="x", giver_left_at="x")) == LEFT
    assert clue_submit_problem(ctx(closed_at="x", player_id=22)) == CLUE_OVER
    assert clue_submit_problem(ctx()) is None


def test_guess_problem():
    guessing = {"clue": "rain", "player_id": 22, "may_guess": 1}
    assert guess_problem(None) == GAME_OVER
    assert guess_problem(ctx(**guessing, status="finished")) == GAME_OVER
    assert guess_problem(ctx(**guessing, closed_at="x")) == GUESS_OVER
    assert guess_problem(ctx(player_id=22, may_guess=1)) == GUESS_OVER
    outsider = guess_problem(ctx(clue="rain", player_id=None))
    assert outsider == "Only players in this game can guess. Join with `/gaminator spectrum join`."
    assert guess_problem(ctx(**guessing, left_at="x")) == LEFT
    assert guess_problem(ctx(clue="rain")) == GIVER_SITS_OUT
    assert guess_problem(ctx(clue="rain", player_id=23, may_guess=0)) == JOINED_LATE
    assert guess_problem(ctx(**guessing)) is None
    assert guess_problem(ctx(**guessing, my_guess=40)) is None


def test_unguessed_streak():
    assert unguessed_streak([(3, 0), (3, 0)])
    assert unguessed_streak([(3, 0), (0, 0), (3, 0)])
    assert unguessed_streak([(3, 2), (3, 0), (3, 0)])
    assert not unguessed_streak([(3, 0), (3, 1)])
    assert not unguessed_streak([(3, 0), (3, 1), (3, 0)])
    assert not unguessed_streak([(3, 0)])
    assert not unguessed_streak([(3, 0), (0, 0)])
    assert not unguessed_streak([])


def test_round_without_clue():
    assert round_without_clue(1, 0) and round_without_clue(3, 0)
    assert not round_without_clue(2, 1)
    assert not round_without_clue(0, 0)
    assert not round_without_clue(0, 3)


def test_turn_points():
    scored = turn(11, 73, [(12, 70), (13, 88), (14, 12)])
    assert scored.points() == {12: 91, 13: 55, 14: 0, 11: 49}
    assert turn(11, 73, []).points() == {11: 0}
    assert turn(11, 50, [(12, 50)]).points() == {12: 100, 11: 100}


def test_game_points():
    turns = [turn(11, 73, [(12, 70), (13, 88)]), turn(12, 20, [(11, 20), (14, 30)])]
    expected = [(12, 176), (11, 173), (14, 70), (13, 55), (15, 0)]
    assert game_points(turns, [11, 12, 13, 15]) == expected
    assert game_points(turns, [])[0] == (12, 176)
    assert game_points([], [13, 11]) == [(11, 0), (13, 0)]


def test_game_winners():
    assert game_winners([(12, 176), (11, 173)]) == (12,)
    assert game_winners([(11, 90), (12, 90), (13, 10)]) == (11, 12)
    assert game_winners([(11, 0), (12, 0)]) == ()
    assert game_winners([]) == ()


def test_closest_guess():
    assert closest_guess([]) is None
    assert closest_guess([turn(11, 50, [])]) is None
    early = turn(11, 73, [(12, 70), (13, 75)], closed_at="2026-01-01T00:00:00+00:00")
    late = turn(12, 20, [(11, 22), (13, 40)], closed_at="2026-01-01T00:05:00+00:00")
    assert closest_guess([early, late]) == (early, 13, 75)
    assert closest_guess([late, early]) == (early, 13, 75)
    tied = turn(14, 60, [(13, 58), (12, 62)], closed_at="2026-01-01T00:00:00+00:00")
    assert closest_guess([tied]) == (tied, 12, 62)
    exact = turn(13, 5, [(11, 5)], closed_at="2026-01-01T00:09:00+00:00")
    assert closest_guess([early, late, exact]) == (exact, 11, 5)


def test_standings():
    first = [turn(11, 73, [(12, 73), (13, 40)]), turn(12, 20, [(11, 23), (13, 20)])]
    second = [turn(13, 50, [(11, 50)])]
    rows = standings({1: first, 2: second}, {11: 2, 12: 1, 13: 2, 14: 1})
    assert rows == [
        Standing(11, 1, 242, 2),
        Standing(13, 1, 201, 2),
        Standing(12, 1, 196, 1),
        Standing(14, 0, 0, 1),
    ]
    assert standings({}, {}) == []
    assert standings({1: []}, {11: 1}) == [Standing(11, 0, 0, 1)]


def test_standings_ties():
    tied = [turn(11, 50, [(12, 50)])]
    rows = standings({1: tied, 2: tied}, {11: 2, 12: 2})
    assert rows == [Standing(11, 2, 200, 2), Standing(12, 2, 200, 2)]
    nobody = [turn(11, 1, [(12, 100)])]
    assert standings({1: nobody}, {11: 1, 12: 1}) == [
        Standing(11, 0, 0, 1),
        Standing(12, 0, 0, 1),
    ]
    order = standings({1: [turn(11, 50, [(12, 50), (13, 60)])]}, {11: 1, 12: 3, 13: 1})
    assert [s.user_id for s in order] == [12, 11, 13]


def test_standings_left_player():
    played = [turn(11, 50, [(12, 50), (13, 49)])]
    rows = standings({1: played}, {11: 1, 12: 1})
    assert Standing(13, 0, 97, 0) in rows
    assert [s.user_id for s in rows] == [12, 11, 13]


def test_leaderboard_rows():
    rows = [Standing(uid, 0, 200 - uid, 1) for uid in range(1, 15)]
    top = leaderboard_rows(rows, caller_id=3)
    assert [rank for rank, _ in top] == list(range(1, 11))
    lower = leaderboard_rows(rows, caller_id=13)
    assert [rank for rank, _ in lower] == [*range(1, 11), 13]
    assert lower[-1][1].user_id == 13
    assert leaderboard_rows(rows, caller_id=99) == top
    assert [rank for rank, _ in leaderboard_rows(rows, 14, top=2)] == [1, 2, 14]
    assert leaderboard_rows([], 1) == []

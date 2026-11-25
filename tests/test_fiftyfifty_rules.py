"""Fifty-Fifty rules: scoring, options, guesses, write and vote checks, totals, host handover."""

from datetime import UTC, datetime

from gaminator.games.fiftyfifty.services.rules import (
    CLOSED,
    OPTION_LIMIT,
    VOTE_FIRST,
    ScoredQuestion,
    Standing,
    best_question,
    best_questions,
    clean_option,
    dm_action,
    game_points,
    game_winners,
    guess_problem,
    guess_score,
    leaderboard_rows,
    min_players,
    next_host,
    nobody_voted_streak,
    score,
    split_options,
    split_text,
    standings,
    too_few_to_continue,
    too_long,
    vote_problem,
    write_problem,
)


def test_min_players():
    assert min_players() == 3
    assert min_players(2) == 2
    assert min_players(1) == 2
    assert min_players(5) == 5


def test_too_few_to_continue():
    assert too_few_to_continue(2, 3)
    assert not too_few_to_continue(3, 3)
    assert not too_few_to_continue(2, 2)
    assert too_few_to_continue(1, 2)


def test_score():
    table = {
        (0, 0): 0,
        (1, 0): 0,
        (1, 1): 100,
        (2, 2): 100,
        (6, 6): 100,
        (2, 1): 100,
        (3, 2): 100,
        (6, 5): 100,
        (3, 1): 50,
        (4, 1): 50,
        (5, 2): 67,
        (6, 1): 33,
        (7, 4): 80,
        (10, 1): 20,
        (2, 0): 0,
        (3, 0): 0,
        (11, 0): 0,
    }
    for (a, b), points in table.items():
        assert score(a, b) == points, (a, b)
        assert score(b, a) == points, (b, a)


def test_split_text():
    assert split_text(3, 2) == "as even as 5 votes can be"
    assert split_text(2, 3) == "as even as 5 votes can be"
    assert split_text(2, 2) == "a 50/50 split"
    assert split_text(6, 4) == "a 60/40 split"
    assert split_text(4, 6) == "a 60/40 split"
    assert split_text(2, 1) == "as even as 3 votes can be"
    assert split_text(5, 2) == "a 71/29 split"
    assert split_text(1, 5) == "a 83/17 split"
    assert split_text(3, 0) == "a 100/0 split"


def test_clean_option():
    assert clean_option("  cold   pizza \n now ") == "cold pizza now"
    assert clean_option("") is None
    assert clean_option("   \n\t ") is None
    assert clean_option("?") is None
    assert clean_option("would you rather") is None
    assert OPTION_LIMIT == 80
    assert clean_option("x" * 81) == "x" * 80
    assert clean_option("x" * 80) == "x" * 80
    assert clean_option("word " * 20) == ("word " * 16).strip()
    assert clean_option("Would you rather  eat soup?") == "eat soup"
    assert clean_option("WOULD YOU RATHER: nap ??") == "nap"
    assert clean_option("would you rathers") == "would you rathers"
    assert clean_option("order pizza") == "order pizza"
    assert clean_option("or run?", second=True) == "run"
    assert clean_option("Or: run", second=True) == "run"
    assert clean_option("or run") == "or run"
    assert clean_option("organize", second=True) == "organize"
    assert clean_option("or", second=True) == "or"
    assert not too_long("or " + "x" * 80, second=True) and too_long("or " + "x" * 80)
    assert not too_long("x" * 80) and too_long("x" * 81)
    assert not too_long("would you rather " + "x" * 80 + "?")


def test_split_options():
    assert split_options("eat soup | eat salad") == ("eat soup", "eat salad")
    assert split_options("eat soup\neat salad") == ("eat soup", "eat salad")
    assert split_options("\n eat soup \n\n  eat salad \n") == ("eat soup", "eat salad")
    assert split_options("would you rather nap | or run?") == ("would you rather nap", "or run?")
    assert split_options("nap |\n run") == ("nap", "run")
    assert split_options("a | or") == ("a", "or")
    for bad in ("nap", "nap or run", "a | b | c", "a\nb\nc", "| b", "a |", "|", ""):
        assert split_options(bad) is None, bad


def test_guess_score():
    assert guess_score(2, 2, 2) == 50
    assert guess_score(0, 0, 3) == 50
    assert guess_score(1, 2, 2) == 20 and guess_score(3, 2, 2) == 20
    assert guess_score(0, 2, 2) == 0 and guess_score(4, 2, 2) == 0
    assert guess_score(1, 2, 1) == 0 and guess_score(2, 2, 1) == 50
    assert guess_score(1, 1, 1) == 50 and guess_score(0, 1, 1) == 0
    assert guess_score(1, 1, 0) == 0 and guess_score(0, 0, 1) == 0
    assert guess_score(0, 0, 0) == 0
    assert guess_score(11, 3, 2) == 0
    assert guess_score(7, 6, 5) == 20


def test_nobody_voted_streak():
    assert nobody_voted_streak([0, 0])
    assert nobody_voted_streak([3, 0, 0])
    assert not nobody_voted_streak([0, 2, 0])
    assert not nobody_voted_streak([0, 0, 1])
    assert not nobody_voted_streak([0])
    assert not nobody_voted_streak([])


def player(pid, joined, left=None):
    return {"id": pid, "joined_at": joined, "left_at": left}


def test_next_host():
    players = [player(1, "t1"), player(2, "t3"), player(3, "t2"), player(4, "t0", left="t5")]
    assert next_host(players, 1)["id"] == 3
    assert next_host(players, 3)["id"] == 1
    tied = [player(5, "t1"), player(4, "t1"), player(6, "t1")]
    assert next_host(tied, 4)["id"] == 5
    assert next_host([player(1, "t1")], 1) is None
    assert next_host([], 1) is None


def write_row(**changes):
    row = {
        "game_id": 7,
        "status": "active",
        "round_id": 3,
        "write_closed_at": None,
        "round_closed_at": None,
        "player_id": 5,
        "left_at": None,
        "on_roster": 1,
        "question_id": 9,
    }
    return {**row, **changes}


def test_write_problem():
    assert write_problem(write_row()) is None
    assert write_problem(None) == "That game is over."
    assert write_problem(write_row(status="finished")) == "That game is over."
    closed = "Writing for that round has closed."
    assert write_problem(write_row(write_closed_at="t")) == closed
    assert write_problem(write_row(round_closed_at="t")) == closed
    assert write_problem(write_row(write_closed_at="t", player_id=None)) == closed
    outsider = write_row(player_id=None, on_roster=0, question_id=None)
    assert write_problem(outsider) == (
        "Only players in this game can write. Join with `/gaminator fiftyfifty join`."
    )
    assert write_problem(write_row(left_at="t")) == "You've left the game."
    not_in = "You're not in this round. If you just joined, you're in from the next one."
    assert write_problem(write_row(on_roster=0, question_id=None)) == not_in
    assert write_problem(write_row(question_id=None)) == not_in


GAME = {"id": 7, "status": "active"}
QUESTION = {"id": 9, "writer_id": 5, "started_at": "t", "closed_at": None}
VOTER = {"id": 6, "left_at": None}


def test_vote_problem():
    assert vote_problem(GAME, QUESTION, VOTER, True) is None
    assert vote_problem(None, QUESTION, VOTER, True) == CLOSED
    assert vote_problem(GAME, None, VOTER, True) == CLOSED
    assert vote_problem({**GAME, "status": "finished"}, QUESTION, VOTER, True) == CLOSED
    assert vote_problem(GAME, {**QUESTION, "started_at": None}, VOTER, True) == CLOSED
    assert vote_problem(GAME, {**QUESTION, "closed_at": "t"}, VOTER, True) == CLOSED
    assert vote_problem(GAME, QUESTION, None, False) == (
        "Only players in this game can vote. Join with `/gaminator fiftyfifty join`."
    )
    not_in = "You're not in this round. If you just joined, you're in from the next one."
    assert vote_problem(GAME, QUESTION, {**VOTER, "left_at": "t"}, True) == not_in
    assert vote_problem(GAME, QUESTION, VOTER, False) == not_in
    writer = {"id": 5, "left_at": None}
    assert vote_problem(GAME, QUESTION, writer, True) == (
        "You wrote this one, so you sit this vote out."
    )
    assert vote_problem(GAME, {**QUESTION, "closed_at": "t"}, writer, True) == CLOSED


def test_guess_problem():
    assert guess_problem(GAME, QUESTION, VOTER, True, True, 0) is None
    assert guess_problem(GAME, QUESTION, VOTER, True, True, 11) is None
    assert guess_problem(GAME, QUESTION, VOTER, True, False, 2) == VOTE_FIRST
    assert guess_problem(GAME, QUESTION, VOTER, True, True, -1) == "That guess is out of range."
    assert guess_problem(GAME, QUESTION, VOTER, True, True, 12) == "That guess is out of range."
    assert guess_problem(GAME, {**QUESTION, "closed_at": "t"}, VOTER, True, True, 1) == CLOSED
    assert guess_problem(GAME, QUESTION, None, False, False, 1) == (
        "Only players in this game can vote. Join with `/gaminator fiftyfifty join`."
    )
    writer = {"id": 5, "left_at": None}
    assert guess_problem(GAME, QUESTION, writer, True, False, 1) == (
        "You wrote this one, so you sit this vote out."
    )


def scored(writer, a, b, closed_at="t1", guesses=()):
    return ScoredQuestion(writer, "eat soup", "skip lunch", None, a, b, closed_at, guesses)


def test_guess_points_count_in_totals():
    q = scored(11, 2, 2, guesses=((12, 2), (13, 3), (14, 0), (15, 1)))
    assert q.points == 100
    assert q.guess_points == [(12, 50), (13, 20), (15, 20)]
    assert game_points([q], [11, 12, 13, 14]) == [(11, 100), (12, 50), (13, 20), (15, 20), (14, 0)]
    small = scored(11, 2, 1, guesses=((12, 2), (13, 1)))
    assert small.guess_points == [(12, 50)]
    lone = scored(11, 1, 0, guesses=((12, 1),))
    assert lone.guess_points == [] and game_points([lone], []) == [(11, 0)]
    old = ScoredQuestion(11, "eat ____", "skip lunch", "soup", 1, 1, "t1")
    assert old.guesses == () and old.guess_points == [] and old.points == 100


def test_guess_points_can_win_a_game_and_reach_the_standings():
    games = {
        1: [
            scored(11, 3, 1, guesses=((12, 3), (13, 3), (14, 2))),
            scored(12, 4, 0, guesses=((11, 0), (13, 4))),
        ]
    }
    assert game_points(games[1], []) == [(13, 100), (11, 50), (12, 50), (14, 20)]
    assert standings(games, {11: 1, 12: 1, 13: 1, 14: 1}) == [
        Standing(13, 1, 100, 0, 1, guess_points=100),
        Standing(11, 0, 50, 1, 1),
        Standing(12, 0, 50, 1, 1, guess_points=50),
        Standing(14, 0, 20, 0, 1, guess_points=20),
    ]
    assert standings({1: [scored(11, 2, 2, guesses=((16, 2),))]}, {})[1] == Standing(
        16, 0, 50, 0, 0, guess_points=50
    )


def test_game_points():
    questions = [scored(11, 2, 2), scored(12, 3, 1), scored(11, 5, 2), scored(14, 1, 1)]
    assert game_points(questions, [11, 12, 13]) == [(11, 167), (14, 100), (12, 50), (13, 0)]
    assert game_points([], [13, 11]) == [(11, 0), (13, 0)]
    assert game_points([scored(12, 1, 1), scored(11, 1, 1)], []) == [(11, 100), (12, 100)]
    assert game_points([], []) == []


def test_game_winners():
    assert game_winners([(11, 167), (12, 50)]) == (11,)
    assert game_winners([(11, 100), (12, 100), (13, 0)]) == (11, 12)
    assert game_winners([(11, 0), (12, 0)]) == ()
    assert game_winners([]) == ()


def test_best_question():
    assert best_question([]) is None
    low, high = scored(11, 3, 1), scored(12, 1, 1)
    assert best_question([low, high]) is high
    more_votes = scored(13, 3, 3, "t9")
    assert best_question([low, high, more_votes]) is more_votes
    earlier = scored(14, 2, 4, "t0")
    later = scored(15, 4, 2, "t5")
    assert best_question([later, earlier]) is earlier
    assert scored(11, 5, 2).points == 67


def dm_row(question_id, write_closed_at=None, dm_prompted_at=None):
    return {
        "question_id": question_id,
        "write_closed_at": write_closed_at,
        "dm_prompted_at": dm_prompted_at,
    }


def test_dm_action():
    now = datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC)
    just = "2026-01-01T00:09:00+00:00"
    stale = "2026-01-01T00:08:59+00:00"
    assert dm_action([], now) == ("ignore", None)
    fresh = dm_row(1)
    assert dm_action([fresh], now) == ("open", fresh)
    prompted = dm_row(2, dm_prompted_at="t")
    assert dm_action([prompted, fresh], now) == ("open", prompted)
    closed = dm_row(3, write_closed_at=just, dm_prompted_at="t")
    assert dm_action([closed], now) == ("closed", closed)
    assert dm_action([closed, fresh], now) == ("open", fresh)
    assert dm_action([dm_row(4, write_closed_at=stale, dm_prompted_at="t")], now) == (
        "ignore",
        None,
    )
    assert dm_action([dm_row(5, write_closed_at=just)], now) == ("ignore", None)
    late = dm_row(6, write_closed_at=stale, dm_prompted_at="t")
    assert dm_action([late, dm_row(7, write_closed_at=just), closed], now) == ("closed", closed)


def test_standings():
    games = {
        1: [scored(11, 2, 2), scored(12, 3, 1), scored(11, 5, 2)],
        2: [scored(12, 1, 1), scored(13, 2, 1), scored(11, 3, 0)],
    }
    played = {11: 2, 12: 2, 13: 1, 14: 1}
    assert standings(games, played) == [
        Standing(11, games_won=1, points=167, questions=3, games_played=2),
        Standing(12, games_won=1, points=150, questions=2, games_played=2),
        Standing(13, games_won=1, points=100, questions=1, games_played=1),
        Standing(14, games_won=0, points=0, questions=0, games_played=1),
    ]
    assert standings({}, {}) == []


def test_standings_ties():
    games = {1: [scored(13, 1, 1), scored(12, 1, 1)], 2: [scored(11, 1, 1), scored(14, 2, 0)]}
    played = {11: 1, 12: 2, 13: 1, 14: 3}
    rows = standings(games, played)
    assert [s.user_id for s in rows] == [12, 11, 13, 14]
    assert [s.games_won for s in rows] == [1, 1, 1, 0]
    assert [s.points for s in rows] == [100, 100, 100, 0]


def test_standings_early_end():
    games = {1: [scored(11, 1, 1)], 2: [], 3: [scored(12, 2, 0)]}
    rows = standings(games, {11: 3, 12: 3, 13: 3})
    assert rows == [
        Standing(11, 1, 100, 1, 3),
        Standing(12, 0, 0, 1, 3),
        Standing(13, 0, 0, 0, 3),
    ]


def test_standings_left_player():
    rows = standings({1: [scored(11, 1, 1), scored(15, 3, 3)]}, {11: 1, 12: 1})
    assert rows == [
        Standing(11, 1, 100, 1, 1),
        Standing(15, 1, 100, 1, 0),
        Standing(12, 0, 0, 0, 1),
    ]


def test_leaderboard_rows():
    rows = [Standing(uid, 0, 200 - uid, 1, 1) for uid in range(101, 113)]
    top = leaderboard_rows(rows, 101)
    assert [rank for rank, _ in top] == list(range(1, 11))
    assert [s.user_id for _, s in top] == list(range(101, 111))
    low = leaderboard_rows(rows, 112)
    assert len(low) == 11 and low[-1] == (12, rows[-1])
    assert leaderboard_rows(rows, 999) == top
    assert leaderboard_rows(rows[:3], 102, top=2) == [(1, rows[0]), (2, rows[1])]
    assert leaderboard_rows([], 101) == []


def test_best_questions():
    old, new, newest = scored(11, 1, 1, "t1"), scored(11, 2, 2, "t3"), scored(11, 3, 2, "t5")
    weak, zero, other = scored(11, 3, 1, "t9"), scored(11, 2, 0, "t8"), scored(12, 1, 1, "t7")
    games = {1: [old, weak, other], 2: [new, zero], 3: [newest]}
    assert best_questions(games, 11) == [newest, new, old]
    assert best_questions(games, 11, limit=5) == [newest, new, old, weak, zero]
    assert best_questions(games, 12) == [other]
    assert best_questions(games, 99) == []

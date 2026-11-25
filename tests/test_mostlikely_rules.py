"""Most Likely To rules: tallies, recap, winners, vote checks, host handover."""

from gaminator.games.mostlikely.services.rules import (
    CLOSED,
    ClosedPrompt,
    Standing,
    game_winners,
    leaderboard_rows,
    min_players,
    next_host,
    nobody_voted_streak,
    recap,
    recent_prompt_wins,
    standings,
    tally,
    too_few_to_continue,
    vote_problem,
)


def test_min_players():
    assert min_players() == 3
    assert min_players(2) == 2
    assert min_players(1) == 2
    assert min_players(5) == 5


def test_too_few_to_continue():
    assert too_few_to_continue(1)
    assert not too_few_to_continue(2)


def test_nobody_voted_streak():
    assert not nobody_voted_streak([])
    assert not nobody_voted_streak([[]])
    assert nobody_voted_streak([[], []])
    assert nobody_voted_streak([[1], [], []])
    assert not nobody_voted_streak([[], [1], []])
    assert not nobody_voted_streak([[], [], [1]])


def test_tally():
    t = tally([1, 2, 1])
    assert t.counts == {1: 2, 2: 1}
    assert t.leaders == (1,)
    assert t.top == 2
    tie = tally([2, 1])
    assert tie.leaders == (1, 2)
    assert tie.top == 1
    empty = tally([])
    assert empty.leaders == () and empty.top == 0 and empty.counts == {}


def test_recap():
    assert recap([[1, 1, 2], [2, 1], [], [2]]) == [(1, 2), (2, 2)]
    assert recap([[3], [3], [1]]) == [(3, 2), (1, 1)]
    assert recap([[], []]) == []


def test_game_winners():
    assert game_winners([(3, 2), (1, 1)]) == (3,)
    assert game_winners([(1, 2), (2, 2), (3, 1)]) == (1, 2)
    assert game_winners([]) == ()


def test_next_host():
    players = [
        {"id": 1, "joined_at": "2026-01-01T00:00:00+00:00", "left_at": None},
        {"id": 2, "joined_at": "2026-01-01T00:00:05+00:00", "left_at": None},
        {"id": 3, "joined_at": "2026-01-01T00:00:01+00:00", "left_at": "x"},
        {"id": 4, "joined_at": "2026-01-01T00:00:05+00:00", "left_at": None},
    ]
    assert next_host(players, 1)["id"] == 2
    assert next_host(players, 2)["id"] == 1
    assert next_host(players[:1], 1) is None
    assert next_host([players[0], players[2]], 1) is None


def test_vote_problem():
    game = {"status": "active", "round": 2}
    rnd = {"closed_at": None, "number": 2}
    voter = {"left_at": None}
    assert vote_problem(game, rnd, voter, True, True) is None
    assert vote_problem(game, {"closed_at": "x", "number": 2}, voter, True, True) == CLOSED
    assert vote_problem(game, {"closed_at": None, "number": 1}, voter, True, True) == CLOSED
    assert vote_problem({"status": "finished", "round": 2}, rnd, voter, True, True) == CLOSED
    assert vote_problem(None, None, None, False, False) == CLOSED
    assert "Only players" in vote_problem(game, rnd, None, False, True)
    assert "not voting on this prompt" in vote_problem(game, rnd, {"left_at": "x"}, True, True)
    assert "not voting on this prompt" in vote_problem(game, rnd, voter, False, True)
    assert "isn't on this prompt" in vote_problem(game, rnd, voter, True, False)


def closed(targets, at="2026-01-01T00:00:00+00:00", prompt="p"):
    return ClosedPrompt(prompt, at, tuple(targets))


def test_standings():
    games = {
        1: [closed([7, 7, 8]), closed([8]), closed([7])],
        2: [closed([9, 9])],
    }
    played = {7: 1, 8: 1, 9: 1, 10: 2}
    assert standings(games, played) == [
        Standing(7, 1, 2, 1),
        Standing(9, 1, 1, 1),
        Standing(8, 0, 1, 1),
        Standing(10, 0, 0, 2),
    ]
    assert standings({}, {}) == []


def test_standings_ties():
    rows = standings({1: [closed([7, 8])]}, {7: 1, 8: 1})
    assert rows == [Standing(7, 1, 1, 1), Standing(8, 1, 1, 1)]


def test_standings_early_end():
    one_prompt = standings({1: [closed([7])]}, {7: 1, 8: 1})
    assert one_prompt[0] == Standing(7, 1, 1, 1)
    no_votes = standings({1: [closed([])]}, {7: 1, 8: 1})
    assert no_votes == [Standing(7, 0, 0, 1), Standing(8, 0, 0, 1)]


def test_standings_zero_vote_prompt():
    rows = standings({1: [closed([]), closed([8])]}, {7: 1, 8: 1})
    assert rows == [Standing(8, 1, 1, 1), Standing(7, 0, 0, 1)]


def test_standings_left_player():
    rows = standings({1: [closed([7, 7]), closed([8])], 2: [closed([7])]}, {7: 2, 8: 1})
    assert rows[0] == Standing(7, 2, 2, 2)


def test_leaderboard_rows():
    rows = [Standing(u, 0, 20 - u, 1) for u in range(1, 15)]
    top = leaderboard_rows(rows, caller_id=3)
    assert [rank for rank, _ in top] == list(range(1, 11))
    outside = leaderboard_rows(rows, caller_id=13)
    assert [rank for rank, _ in outside] == [*range(1, 11), 13]
    assert outside[-1][1].user_id == 13
    assert len(leaderboard_rows(rows, caller_id=99)) == 10
    assert leaderboard_rows([], caller_id=1) == []


def test_recent_prompt_wins():
    games = {
        1: [closed([7], f"2026-01-0{d}T00:00:00+00:00", f"p{d}") for d in range(1, 8)],
        2: [
            closed([7, 8], "2026-02-01T00:00:00+00:00", "tied"),
            closed([], "2026-02-02T00:00:00+00:00", "empty"),
            closed([8], "2026-02-03T00:00:00+00:00", "other"),
        ],
    }
    recent = recent_prompt_wins(games, 7)
    assert [p.prompt for p in recent] == ["tied", "p7", "p6", "p5", "p4"]
    assert [p.prompt for p in recent_prompt_wins(games, 8)] == ["other", "tied"]
    assert recent_prompt_wins(games, 99) == []

"""Pure Headlines rules."""

import itertools
import random

import pytest

from gaminator.games.headlines.services.rules import (
    ANSWER_LIMIT,
    ANSWER_OVER,
    GAME_OVER,
    HEADLINE_LIMIT,
    LEFT,
    NEXT_ROUND,
    NO_ANSWERS_NOTE,
    NO_HEADLINES_NOTE,
    NO_QUOTE,
    NO_VOTES_NOTE,
    NOT_PLAYING,
    OWES_ANSWER,
    OWES_HEADLINE,
    OWES_VOTE,
    VOTE_OVER,
    WRITE_OVER,
    ScoredPairing,
    Standing,
    answer_problem,
    assign_quotes,
    clean_text,
    game_points,
    game_winners,
    idle_reason,
    leaderboard_rows,
    min_players,
    next_host,
    owed_line,
    reveal_pause,
    standings,
    too_few_to_continue,
    top_headline,
    vote_problem,
    write_problem,
)

A, B, C, D, E, F, G, H = range(1, 9)


def answer_of(player):
    return 100 + player


def ctx(step="answering", **over):
    """A round_context row for a roster player still in, at the given step."""
    row = {
        "status": "active",
        "answer_closed_at": None,
        "write_deadline_at": None,
        "write_closed_at": None,
        "vote_deadline_at": None,
        "vote_closed_at": None,
        "closed_at": None,
        "player_id": 7,
        "left_at": None,
        "answer_id": 70,
        "answer": None,
        "pairing_id": None,
        "headline": None,
        "my_vote": None,
        "can_vote": 0,
    }
    if step in ("writing", "reveal", "voting"):
        row.update(answer_closed_at="t", write_deadline_at="t", pairing_id=5)
    if step in ("reveal", "voting"):
        row.update(write_closed_at="t")
    if step == "voting":
        row.update(vote_deadline_at="t", can_vote=1)
    row.update(over)
    return row


class Fixed:
    """A stand-in for random.Random whose shuffles put each list in a given order."""

    def __init__(self, *orders):
        self.orders = list(orders)

    def shuffle(self, items):
        items[:] = self.orders.pop(0)


def pairing(votes, round_number=1, position=1, writer=B, quoted=A):
    return ScoredPairing(round_number, position, writer, quoted, "headline", "answer", votes)


def test_min_players():
    assert min_players() == 3
    assert min_players(None) == 3
    assert min_players(2) == 2
    assert min_players(1) == 2
    assert min_players(5) == 5


def test_too_few_to_continue():
    assert too_few_to_continue(2, 3)
    assert not too_few_to_continue(3, 3)


def test_next_host():
    players = [
        {"id": 1, "joined_at": "2026-01-01T00:00:03", "left_at": None},
        {"id": 2, "joined_at": "2026-01-01T00:00:01", "left_at": "x"},
        {"id": 3, "joined_at": "2026-01-01T00:00:02", "left_at": None},
    ]
    assert next_host(players, 1)["id"] == 3
    assert next_host(players, 3)["id"] == 1
    assert next_host(players[:2], 1) is None


def test_clean_text():
    assert clean_text("  lick \n the\tspoon  ", ANSWER_LIMIT) == "lick the spoon"
    assert clean_text("", ANSWER_LIMIT) is None
    assert clean_text("   \n ", ANSWER_LIMIT) is None
    assert clean_text("a" * 101, ANSWER_LIMIT) == "a" * 100
    assert clean_text("b" * 121, HEADLINE_LIMIT) == "b" * 120
    assert clean_text("a" * 99 + " b", ANSWER_LIMIT) == "a" * 99


def test_answer_problem():
    assert answer_problem(None) == GAME_OVER
    assert answer_problem(ctx(status="finished")) == GAME_OVER
    assert answer_problem(ctx(answer_closed_at="t")) == ANSWER_OVER
    assert answer_problem(ctx(closed_at="t")) == ANSWER_OVER
    assert answer_problem(ctx(player_id=None, answer_id=None)) == NOT_PLAYING
    assert answer_problem(ctx(left_at="t")) == LEFT
    assert answer_problem(ctx(answer_id=None)) == NEXT_ROUND
    assert answer_problem(ctx()) is None
    assert answer_problem(ctx(answer="saved")) is None


def test_write_problem():
    assert write_problem(None) == GAME_OVER
    assert write_problem(ctx("writing", status="finished")) == GAME_OVER
    assert write_problem(ctx("answering")) == WRITE_OVER
    assert write_problem(ctx("reveal")) == WRITE_OVER
    assert write_problem(ctx("writing", closed_at="t")) == WRITE_OVER
    assert write_problem(ctx("writing", player_id=None, answer_id=None)) == NOT_PLAYING
    assert write_problem(ctx("writing", left_at="t")) == LEFT
    assert write_problem(ctx("writing", answer_id=None, pairing_id=None)) == NEXT_ROUND
    assert write_problem(ctx("writing", pairing_id=None)) == NO_QUOTE
    assert write_problem(ctx("writing")) is None
    assert write_problem(ctx("writing", headline="saved")) is None


def test_vote_problem():
    assert vote_problem(None) == GAME_OVER
    assert vote_problem(ctx("voting", status="finished")) == GAME_OVER
    assert vote_problem(ctx("reveal")) == VOTE_OVER
    assert vote_problem(ctx("voting", vote_closed_at="t")) == VOTE_OVER
    assert vote_problem(ctx("voting", closed_at="t")) == VOTE_OVER
    assert vote_problem(ctx("voting", player_id=None, answer_id=None)) == NOT_PLAYING
    assert vote_problem(ctx("voting", left_at="t")) == LEFT
    assert vote_problem(ctx("voting", answer_id=None)) == NEXT_ROUND
    assert vote_problem(ctx("voting")) is None
    assert vote_problem(ctx("voting", my_vote=3)) is None


def test_owed_line():
    assert owed_line(None) is None
    assert owed_line(ctx()) == OWES_ANSWER
    assert owed_line(ctx(answer="in")) is None
    assert owed_line(ctx("writing")) == OWES_HEADLINE
    assert owed_line(ctx("writing", headline="in")) is None
    assert owed_line(ctx("writing", pairing_id=None)) is None
    assert owed_line(ctx("reveal")) is None
    assert owed_line(ctx("voting")) == OWES_VOTE
    assert owed_line(ctx("voting", my_vote=4)) is None
    assert owed_line(ctx("voting", can_vote=0)) is None
    for step in ("answering", "writing", "voting"):
        assert owed_line(ctx(step, left_at="t")) is None
        assert owed_line(ctx(step, answer_id=None, pairing_id=None)) is None
        assert owed_line(ctx(step, player_id=None, answer_id=None, pairing_id=None)) is None
        assert owed_line(ctx(step, closed_at="t")) is None
        assert owed_line(ctx(step, status="finished")) is None


def test_reveal_pause(monkeypatch):
    from gaminator.games.headlines.services import game as flow

    monkeypatch.setattr(flow, "REVEAL_PAUSE", 4)
    assert reveal_pause(3) == 4
    assert reveal_pause(10) == 2
    assert reveal_pause(12) == pytest.approx(20 / 12)
    assert reveal_pause(0) == 0
    monkeypatch.setattr(flow, "REVEAL_PAUSE", 0)
    assert reveal_pause(3) == 0


def test_idle_reason():
    assert idle_reason("no_answers", None, 0) == NO_ANSWERS_NOTE
    assert idle_reason("no_headlines", None, 0) == NO_HEADLINES_NOTE
    assert idle_reason("voted", 3, 0) == NO_VOTES_NOTE
    assert idle_reason("voted", 3, 1) is None
    assert idle_reason("voted", 0, 0) is None
    assert idle_reason("voted", None, 0) is None
    assert idle_reason("one_headline", None, 0) is None
    assert idle_reason("abandoned", None, 0) is None
    assert idle_reason(None, None, 0) is None


TRACE = [
    (
        [A, B, C, D],
        [A, B, C, D],
        ([B, D, A, C], [], []),
        [(B, D), (D, A), (A, C), (C, B)],
    ),
    ([A, B, C, D], [A, B, C], ([A, B, C], [D], []), [(A, B), (B, C), (C, A)]),
    ([A, B, C], [A], ([A], [C, B], []), [(A, C)]),
    ([A, B, C], [A, E], ([A], [C, B], [E]), [(A, C), (E, B)]),
    ([A, B, C], [A, E, F, G, H], ([A], [C, B], [E, F, G, H]), [(A, C), (E, B), (F, A)]),
    (
        [A, B, C, D],
        [A, B, E, F, G],
        ([A, B], [D, C], [F, E, G]),
        [(A, B), (B, A), (F, D), (E, C)],
    ),
    ([A, B], [E], ([], [B, A], [E]), [(E, B)]),
    ([A], [A, E], None, []),
    ([A, B, C], [], ([], [A, B, C], []), []),
]


@pytest.mark.parametrize("writers, authors, shuffled, expected", TRACE)
def test_assign_quotes(writers, authors, shuffled, expected):
    answers = [(answer_of(author), author) for author in authors]
    if shuffled is None:
        rng = Fixed()
    else:
        answering, free, leavers = shuffled
        rng = Fixed(answering, free, [answer_of(author) for author in leavers])
    pairs = assign_quotes(answers, writers, rng)
    assert pairs == [(answer_of(author), writer) for author, writer in expected]


def cases():
    """(writers, answers) for every writer count, answering count and 0 to 4 leavers."""
    for writer_count in range(13):
        writers = list(range(1, writer_count + 1))
        for answering, leavers in itertools.product(range(writer_count + 1), range(5)):
            authors = writers[:answering] + list(range(50, 50 + leavers))
            yield writers, [(answer_of(author), author) for author in authors]


def test_assign_quotes_properties():
    for seed in range(200):
        rng = random.Random(seed)
        for writers, answers in cases():
            author_of = dict(answers)
            pairs = assign_quotes(answers, list(writers), rng)
            if len(writers) < 2:
                assert pairs == []
                continue
            assert len(pairs) == min(len(answers), len(writers))
            assert all(author_of[answer_id] != writer for answer_id, writer in pairs)
            assert all(writer in writers for _, writer in pairs)
            assert len({writer for _, writer in pairs}) == len(pairs)
            assert len({answer_id for answer_id, _ in pairs}) == len(pairs)


def test_every_answering_writer_gets_a_quote():
    for seed in range(200):
        rng = random.Random(seed)
        for writers, answers in cases():
            answering = {author for _, author in answers if author in writers}
            if len(answering) < 2:
                continue
            pairs = assign_quotes(answers, list(writers), rng)
            got = {writer for _, writer in pairs}
            assert answering <= got
            used = {answer_id for answer_id, _ in pairs}
            assert {answer_of(author) for author in answering} <= used


def test_assign_quotes_does_not_change_its_arguments():
    answers = [(answer_of(A), A), (answer_of(B), B)]
    writers = [A, B, C]
    assign_quotes(answers, writers, random.Random(1))
    assert writers == [A, B, C] and answers == [(answer_of(A), A), (answer_of(B), B)]


@pytest.mark.parametrize("votes, writer, quoted", [(0, 0, 0), (1, 100, 25), (4, 400, 100)])
def test_pairing_points(votes, writer, quoted):
    assert pairing(votes).points() == {B: writer, A: quoted}


def test_pairing_points_eleven_votes():
    assert pairing(11).points() == {B: 1100, A: 275}


def test_game_points():
    pairings = [
        pairing(2, writer=B, quoted=A),
        pairing(1, position=2, writer=A, quoted=C),
        pairing(1, round_number=2, writer=D, quoted=B),
    ]
    points = game_points(pairings, [A, B, C, E])
    assert points == [(B, 225), (A, 150), (D, 100), (C, 25), (E, 0)]
    assert game_points([], [C, A]) == [(A, 0), (C, 0)]


def test_game_winners():
    assert game_winners([(B, 225), (A, 150)]) == (B,)
    assert game_winners([(A, 100), (B, 100), (C, 25)]) == (A, B)
    assert game_winners([(A, 0), (B, 0)]) == ()
    assert game_winners([]) == ()


def test_top_headline():
    first = pairing(2, round_number=1, position=2)
    assert top_headline([pairing(1), first, pairing(2, round_number=2)]) is first
    assert top_headline([pairing(2, position=3), first]) is first
    assert top_headline([pairing(3, round_number=2), first]).round_number == 2
    assert top_headline([pairing(0), pairing(0, position=2)]) is None
    assert top_headline([]) is None


def test_standings():
    games = {
        1: [pairing(2, writer=B, quoted=A), pairing(1, position=2, writer=A, quoted=C)],
        2: [pairing(1, writer=A, quoted=B)],
    }
    rows = standings(games, {A: 2, B: 2, C: 1, D: 1})
    assert rows == [
        Standing(A, 1, 250, 2),
        Standing(B, 1, 225, 2),
        Standing(C, 0, 25, 1),
        Standing(D, 0, 0, 1),
    ]
    assert standings({}, {}) == []


def test_standings_ties():
    games = {1: [pairing(1, writer=A, quoted=C), pairing(1, position=2, writer=B, quoted=D)]}
    rows = standings(games, {A: 1, B: 1, C: 1, D: 1})
    assert [(s.user_id, s.games_won) for s in rows] == [(A, 1), (B, 1), (C, 0), (D, 0)]
    scoreless = standings({1: [pairing(0)]}, {A: 1, B: 1})
    assert [s.games_won for s in scoreless] == [0, 0]


def test_standings_left_player():
    """A player with points but no played game on record is still listed."""
    rows = standings({1: [pairing(1, writer=B, quoted=A)]}, {B: 1})
    assert rows == [Standing(B, 1, 100, 1), Standing(A, 0, 25, 0)]


def test_leaderboard_rows():
    rows = [Standing(n, 0, 1000 - n, 1) for n in range(1, 15)]
    top = leaderboard_rows(rows, 3)
    assert [rank for rank, _ in top] == list(range(1, 11))
    lower = leaderboard_rows(rows, 13)
    assert [rank for rank, _ in lower] == [*range(1, 11), 13]
    assert lower[-1][1].user_id == 13
    assert leaderboard_rows(rows, 0) == top
    assert leaderboard_rows([], 1) == []

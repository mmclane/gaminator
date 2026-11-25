"""The Headlines question deck: loading, validating and picking."""

import logging
import random

from gaminator.games.headlines.services.questions import (
    FALLBACK,
    QUESTION_LIMIT,
    load_questions,
    pick_questions,
)


def warnings_in(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_packaged_deck():
    questions = load_questions(None)
    assert len(questions) == 100
    assert len({q.lower() for q in questions}) == 100
    for question in questions:
        assert question == " ".join(question.split())
        assert 1 <= len(question) <= QUESTION_LIMIT
        assert question[-1] in "?."
    assert QUESTION_LIMIT == 100
    assert max(len(q) for q in questions) == 57
    assert len(FALLBACK) >= 3 and all(q in questions for q in FALLBACK)


def test_file_override(tmp_path):
    path = tmp_path / "deck.yaml"
    path.write_text(
        "questions:\n  - '  What is   for lunch? '\n  - WHAT IS FOR LUNCH?\n  - How do you nap?\n"
    )
    assert load_questions(path) == ["What is for lunch?", "How do you nap?"]
    bare = tmp_path / "bare.yaml"
    bare.write_text("- Tea or coffee?\n")
    assert load_questions(bare) == ["Tea or coffee?"]


def test_invalid_entries_skipped(tmp_path, caplog):
    path = tmp_path / "deck.yaml"
    path.write_text(
        "questions:\n"
        "  - How do you nap?\n"
        "  - 7\n"
        "  - {text: nested}\n"
        "  - ''\n"
        "  - '   '\n"
        f"  - {'x' * (QUESTION_LIMIT + 1)}\n"
        f"  - {'y' * QUESTION_LIMIT}\n"
    )
    with caplog.at_level(logging.WARNING):
        questions = load_questions(path)
    assert questions == ["How do you nap?", "y" * QUESTION_LIMIT]
    assert len(warnings_in(caplog)) == 1 and "skipped 5" in warnings_in(caplog)[0]


def test_missing_or_empty_file_falls_back(tmp_path, caplog):
    packaged = load_questions(None)
    with caplog.at_level(logging.WARNING):
        assert load_questions(tmp_path / "nope.yaml") == packaged
        assert load_questions("") == packaged
    assert warnings_in(caplog) == []
    empty = tmp_path / "empty.yaml"
    empty.write_text("questions: []\n")
    assert load_questions(empty) == packaged
    words = tmp_path / "words.yaml"
    words.write_text("questions: nothing here\n")
    assert load_questions(words) == packaged


def test_unreadable_override_falls_back(tmp_path, caplog):
    packaged = load_questions(None)
    broken = tmp_path / "broken.yaml"
    broken.write_text("questions:\n  - [How do you nap?\n")
    binary = tmp_path / "binary.yaml"
    binary.write_bytes(b"questions:\n  - \xff\xfe nap?\n")
    with caplog.at_level(logging.WARNING):
        assert load_questions(broken) == packaged
        assert load_questions(binary) == packaged
        assert load_questions(tmp_path) == packaged
    assert sum("could not read" in w for w in warnings_in(caplog)) == 3


def test_pick_questions_unused_first():
    deck = [f"question {n}?" for n in range(10)]
    used = set(deck[:6])
    picked = pick_questions(deck, random.Random(1), 6, used)
    assert len(picked) == 6
    assert set(picked[:4]) == set(deck[6:])
    assert set(picked[4:]) <= used
    assert pick_questions(deck, random.Random(1), 3) != pick_questions(deck, random.Random(2), 3)
    assert len(set(pick_questions(deck, random.Random(3), 10))) == 10


def test_pick_questions_small_deck_repeats():
    deck = ["Tea or coffee?", "How do you nap?"]
    picked = pick_questions(deck, random.Random(1), 5)
    assert len(picked) == 5 and set(picked) == set(deck)
    assert set(picked[:2]) == set(deck)

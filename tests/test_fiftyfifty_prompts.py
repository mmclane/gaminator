"""The Fifty-Fifty topic deck and question rendering: loading, validating, picking."""

import logging
import random

import pytest

from gaminator.games.fiftyfifty.services.prompts import (
    FALLBACK,
    TOPIC_LIMIT,
    fill,
    load_topics,
    pick_topics,
    question_text,
    topic_text,
)


def test_packaged_topics():
    topics = load_topics(None)
    assert len(topics) >= 60
    assert len({t.lower() for t in topics}) == len(topics)
    for topic in topics:
        assert topic == " ".join(topic.split()) and 0 < len(topic) <= TOPIC_LIMIT
    assert {"food", "superpowers", "this server", "road trips"} <= set(topics)
    assert len(FALLBACK) >= 3 and all(isinstance(t, str) and t for t in FALLBACK)


def test_question_text_and_fill():
    assert question_text("eat *soup*", "skip_lunch") == (
        "Would you rather eat \\*soup\\* or skip\\_lunch?"
    )
    assert fill("a *big* _dog_") == "a \\*big\\* \\_dog\\_"
    assert fill("# loud") == "\\# loud"
    assert fill("[click](http://x.test)") != "[click](http://x.test)"
    assert fill("plain words") == "plain words"


def test_old_question_renders_with_its_blank():
    assert question_text("eat ____ every day", "never eat pizza again", "cold soup") == (
        "Would you rather eat **cold soup** every day or never eat pizza again?"
    )
    assert fill("____", "a *big* _dog_") == "**a \\*big\\* \\_dog\\_**"
    assert fill("never wait in a line again", "x") == "never wait in a line again"
    assert question_text("have a theme song", "____", "nap") == (
        "Would you rather have a theme song or **nap**?"
    )


def test_topic_text():
    assert topic_text("food") == "Topic idea: **food**. Ignore it if you have something better."
    assert topic_text("snake_case") == (
        "Topic idea: **snake\\_case**. Ignore it if you have something better."
    )
    assert topic_text(None) == "" and topic_text("") == ""


def test_file_override(tmp_path):
    path = tmp_path / "deck.yaml"
    path.write_text("topics:\n  - '  road   trips '\n  - Road Trips\n  - this server\n")
    assert load_topics(path) == ["road trips", "this server"]
    bare = tmp_path / "bare.yaml"
    bare.write_text("- soup\n- salad\n")
    assert load_topics(bare) == ["soup", "salad"]


def test_invalid_entries_skipped(tmp_path, caplog):
    path = tmp_path / "deck.yaml"
    path.write_text(
        f"topics:\n  - food\n  - {{a: 'eat ____', b: 'skip lunch'}}\n  - 7\n  - ''\n"
        f"  - {'x' * (TOPIC_LIMIT + 1)}\n  - {'y' * TOPIC_LIMIT}\n"
    )
    with caplog.at_level(logging.WARNING):
        topics = load_topics(path)
    assert topics == ["food", "y" * TOPIC_LIMIT]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "skipped 4" in warnings[0]


def test_missing_file_falls_back_quietly(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        assert load_topics(tmp_path / "nope.yaml") == load_topics(None)
    assert not caplog.records


def test_old_format_or_empty_file_falls_back_with_a_warning(tmp_path, caplog):
    old = tmp_path / "old.yaml"
    old.write_text("prompts:\n  - {a: 'eat ____ daily', b: 'skip lunch'}\n")
    empty = tmp_path / "empty.yaml"
    empty.write_text("topics: []\n")
    blank = tmp_path / "blank.yaml"
    blank.write_text("")
    text = tmp_path / "text.yaml"
    text.write_text("topics: food\n")
    for path in (old, empty, blank, text):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert load_topics(path) == load_topics(None)
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1 and "no usable `topics:` list" in warnings[0], path


def test_unreadable_override_falls_back(tmp_path, caplog):
    broken = tmp_path / "broken.yaml"
    broken.write_text("topics: [unclosed\n  - : :\n")
    binary = tmp_path / "binary.yaml"
    binary.write_bytes(b"topics:\n  - '\xff\xfe food'\n")
    with caplog.at_level(logging.WARNING):
        assert load_topics(broken) == load_topics(None)
        assert load_topics(binary) == load_topics(None)
        assert load_topics(tmp_path) == load_topics(None)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert sum("fiftyfifty: could not read" in w for w in warnings) == 3


def test_packaged_file_missing_uses_the_built_in_few(monkeypatch):
    from gaminator.games.fiftyfifty.services import prompts

    monkeypatch.setattr(prompts, "PACKAGE", "gaminator.core")
    assert load_topics(None) == list(FALLBACK)


def deck(count):
    return [f"topic {n}" for n in range(count)]


def test_pick_topics_distinct_and_skips_used():
    rng = random.Random(1)
    topics = deck(30)
    used = set(topics[:10])
    for _ in range(20):
        picked = pick_topics(topics, rng, 12, used)
        assert len(picked) == 12 and len(set(picked)) == 12
        assert not set(picked) & used
    mostly_used = set(topics[:25])
    picked = pick_topics(topics, rng, 12, mostly_used)
    assert len(set(picked)) == 12
    assert set(picked[:5]) == set(topics[25:])
    assert set(picked[5:]) <= mostly_used
    assert pick_topics(topics, rng, 12) != pick_topics(topics, rng, 12)


def test_pick_topics_small_deck_repeats():
    rng = random.Random(1)
    topics = deck(5)
    picked = pick_topics(topics, rng, 12)
    assert len(picked) == 12 and set(picked) == set(topics)
    assert len(set(picked[:5])) == 5
    with pytest.raises(ValueError):
        pick_topics([], rng, 12)

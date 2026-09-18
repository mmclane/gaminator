import random

import pytest

from gaminator.games.assassin.services.words import _clean, contains_word, load_words, pick_word


@pytest.mark.parametrize(
    "text,word,expected",
    [
        ("I love BANANA!", "banana", True),
        ("*banana* bread", "banana", True),
        ("bananas are great", "banana", False),
        ("ice  cream please", "ice cream", True),
        ("icecream", "ice cream", False),
        ("nothing here", "banana", False),
        ("", "banana", False),
        ("banana", "", False),
        ("a.b.c banana.", "banana", True),
        ("say (banana)", "banana", True),
    ],
)
def test_contains_word(text, word, expected):
    assert contains_word(text, word) is expected


def test_pick_word_avoids_used_when_possible():
    rng = random.Random(1)
    words = ["a", "b", "c"]
    for _ in range(20):
        assert pick_word(words, rng, exclude={"a", "b"}) == "c"
    # all excluded: still returns something
    assert pick_word(words, rng, exclude={"a", "b", "c"}) in words


def test_pick_word_empty():
    with pytest.raises(ValueError):
        pick_word([], random.Random())


def test_clean_dedupes_and_normalizes():
    assert _clean([" Banana ", "banana", "Ice   Cream", ""]) == ["banana", "ice cream"]


def test_load_words_packaged_default():
    words = load_words(None)
    assert len(words) > 50
    assert "coffee" in words


def test_load_words_from_file(tmp_path):
    f = tmp_path / "w.yaml"
    f.write_text("words:\n  - Alpha\n  - beta\n")
    assert load_words(f) == ["alpha", "beta"]


def test_load_words_missing_file_falls_back(tmp_path):
    assert len(load_words(tmp_path / "nope.yaml")) > 50

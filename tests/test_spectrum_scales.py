"""The Spectrum scale deck: loading, validating, picking and rendering."""

import logging
import random

from gaminator.games.spectrum.services.scales import (
    END_LIMIT,
    FALLBACK,
    Scale,
    load_scales,
    pick_scales,
    scale_text,
)


def warnings_in(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_packaged_deck():
    scales = load_scales(None)
    assert len(scales) == 80
    assert len({(s.left.lower(), s.right.lower()) for s in scales}) == 80
    for scale in scales:
        for end in (scale.left, scale.right):
            assert end == " ".join(end.split()) and 1 <= len(end) <= END_LIMIT
        assert scale.left.lower() != scale.right.lower()
    assert Scale("Tiny hiccup", "Total disaster") in scales
    assert END_LIMIT == 24
    assert len(FALLBACK) >= 3 and all(isinstance(s, Scale) for s in FALLBACK)


def test_scale_text_escapes():
    assert scale_text(Scale("Tiny hiccup", "Total disaster")) == "Tiny hiccup ↔ Total disaster"
    assert scale_text(Scale("*soft*", "snake_case")) == "\\*soft\\* ↔ snake\\_case"


def test_file_override(tmp_path):
    path = tmp_path / "deck.yaml"
    path.write_text(
        "scales:\n"
        "  - {left: '  Cold   soup ', right: Hot soup}\n"
        "  - {left: cold soup, right: HOT SOUP}\n"
        "  - {left: Quiet, right: Loud}\n"
    )
    assert load_scales(path) == [Scale("Cold soup", "Hot soup"), Scale("Quiet", "Loud")]
    bare = tmp_path / "bare.yaml"
    bare.write_text("- {left: Up, right: Down}\n")
    assert load_scales(bare) == [Scale("Up", "Down")]


def test_invalid_entries_skipped(tmp_path, caplog):
    path = tmp_path / "deck.yaml"
    path.write_text(
        "scales:\n"
        "  - {left: Quiet, right: Loud}\n"
        "  - just text\n"
        "  - {left: Quiet}\n"
        "  - {left: 7, right: Loud}\n"
        "  - {left: '', right: Loud}\n"
        "  - {left: Same, right: same}\n"
        f"  - {{left: {'x' * (END_LIMIT + 1)}, right: Loud}}\n"
        f"  - {{left: {'y' * END_LIMIT}, right: Loud}}\n"
    )
    with caplog.at_level(logging.WARNING):
        scales = load_scales(path)
    assert scales == [Scale("Quiet", "Loud"), Scale("y" * END_LIMIT, "Loud")]
    assert len(warnings_in(caplog)) == 1 and "skipped 6" in warnings_in(caplog)[0]


def test_missing_or_empty_file_falls_back(tmp_path, caplog):
    packaged = load_scales(None)
    with caplog.at_level(logging.WARNING):
        assert load_scales(tmp_path / "nope.yaml") == packaged
        assert load_scales("") == packaged
    assert warnings_in(caplog) == []
    empty = tmp_path / "empty.yaml"
    empty.write_text("scales: []\n")
    assert load_scales(empty) == packaged
    words = tmp_path / "words.yaml"
    words.write_text("scales: nothing here\n")
    assert load_scales(words) == packaged


def test_unreadable_override_falls_back(tmp_path, caplog):
    packaged = load_scales(None)
    broken = tmp_path / "broken.yaml"
    broken.write_text("scales:\n  - {left: Quiet, right: [\n")
    binary = tmp_path / "binary.yaml"
    binary.write_bytes(b"scales:\n  - {left: \xff\xfe, right: Loud}\n")
    with caplog.at_level(logging.WARNING):
        assert load_scales(broken) == packaged
        assert load_scales(binary) == packaged
        assert load_scales(tmp_path) == packaged
    assert sum("could not read" in w for w in warnings_in(caplog)) == 3


def test_pick_scales_unused_first():
    deck = [Scale(f"left {n}", f"right {n}") for n in range(10)]
    used = set(deck[:6])
    picked = pick_scales(deck, random.Random(1), 6, used)
    assert len(picked) == 6
    assert set(picked[:4]) == set(deck[6:])
    assert set(picked[4:]) <= used
    assert pick_scales(deck, random.Random(1), 3) != pick_scales(deck, random.Random(2), 3)
    assert len(set(pick_scales(deck, random.Random(3), 10))) == 10


def test_pick_scales_small_deck_repeats():
    deck = [Scale("Up", "Down"), Scale("Quiet", "Loud")]
    picked = pick_scales(deck, random.Random(1), 5)
    assert len(picked) == 5 and set(picked) == set(deck)
    assert set(picked[:2]) == set(deck)

"""The Most Likely To deck: loading, cleaning, and picking."""

import random

from gaminator.games.mostlikely.services.prompts import FALLBACK, load_prompts, pick_prompt


def test_packaged_deck():
    prompts = load_prompts(None)
    assert len(prompts) >= 50
    assert len({p.lower() for p in prompts}) == len(prompts)
    assert not any(p.lower().startswith("most likely to") for p in prompts)


def test_file_override(tmp_path):
    path = tmp_path / "deck.yaml"
    path.write_text(
        "prompts:\n"
        "  - Most likely to win a   pie contest\n"
        "  - win a pie contest\n"
        "  - most LIKELY to nap at noon.\n"
    )
    assert load_prompts(path) == ["win a pie contest", "nap at noon"]


def test_missing_or_empty_file_falls_back_to_packaged(tmp_path):
    assert load_prompts(tmp_path / "nope.yaml") == load_prompts(None)
    empty = tmp_path / "empty.yaml"
    empty.write_text("prompts: []\n")
    assert load_prompts(empty) == load_prompts(None)
    assert len(FALLBACK) == 3


def test_broken_file_falls_back_to_packaged(tmp_path):
    broken = tmp_path / "broken.yaml"
    broken.write_text("prompts: [unclosed\n  - : :\n")
    assert load_prompts(broken) == load_prompts(None)


def test_pick_prompt_skips_used():
    rng = random.Random(1)
    prompts = ["a", "b", "c"]
    for _ in range(20):
        assert pick_prompt(prompts, rng, {"a", "b"}) == "c"
    assert pick_prompt(prompts, rng, {"a", "b", "c"}) in prompts

import random

import pytest

from gaminator.games.imposter.services.prompts import Prompt, load_prompts, pick_prompt


def test_packaged_prompts():
    prompts = load_prompts(None)
    assert len(prompts) >= 30
    assert all(p.text for p in prompts)
    assert any(p.hint for p in prompts)
    assert len({p.text.lower() for p in prompts}) == len(prompts)


def test_load_from_file_supports_strings_and_dicts(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text(
        "prompts:\n  - Plain   string\n  - prompt: With  hint\n    hint:  About  things\n"
        "  - prompt: No hint\n  - plain string\n"
    )
    assert load_prompts(f) == [
        Prompt("Plain string"),
        Prompt("With hint", "About things"),
        Prompt("No hint"),
    ]


def test_missing_file_falls_back(tmp_path):
    assert len(load_prompts(tmp_path / "nope.yaml")) >= 30


def test_pick_prompt_avoids_used():
    prompts = [Prompt("a"), Prompt("b"), Prompt("c")]
    rng = random.Random(0)
    for _ in range(10):
        assert pick_prompt(prompts, rng, exclude={"a", "b"}).text == "c"
    assert pick_prompt(prompts, rng, exclude={"a", "b", "c"}) in prompts
    with pytest.raises(ValueError):
        pick_prompt([], rng)

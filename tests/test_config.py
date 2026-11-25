"""Settings read from the environment."""

from gaminator.config import Settings


def settings(monkeypatch, **env):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    for key in (
        "MOSTLIKELY_PROMPTS_FILE",
        "MOSTLIKELY_VOTE_SECONDS",
        "FIFTYFIFTY_PROMPTS_FILE",
        "FIFTYFIFTY_WRITE_SECONDS",
        "FIFTYFIFTY_VOTE_SECONDS",
        "SPECTRUM_SCALES_FILE",
        "SPECTRUM_CLUE_SECONDS",
        "SPECTRUM_GUESS_SECONDS",
        "HEADLINES_QUESTIONS_FILE",
        "HEADLINES_ANSWER_SECONDS",
        "HEADLINES_WRITE_SECONDS",
        "HEADLINES_VOTE_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings.from_env()


def test_mostlikely_defaults(monkeypatch):
    s = settings(monkeypatch)
    assert s.mostlikely_prompts_file == "mostlikely_prompts.yaml"
    assert s.mostlikely_prompts_file != s.imposter_prompts_file
    assert s.mostlikely_vote_seconds == 120


def test_mostlikely_vote_seconds_override_and_clamp(monkeypatch):
    assert settings(monkeypatch, MOSTLIKELY_VOTE_SECONDS="30").mostlikely_vote_seconds == 30
    assert settings(monkeypatch, MOSTLIKELY_VOTE_SECONDS="5").mostlikely_vote_seconds == 10


def test_fiftyfifty_defaults(monkeypatch):
    s = settings(monkeypatch)
    assert s.fiftyfifty_prompts_file == "fiftyfifty_prompts.yaml"
    assert s.fiftyfifty_prompts_file not in (s.mostlikely_prompts_file, s.imposter_prompts_file)
    assert s.fiftyfifty_write_seconds == 120
    assert s.fiftyfifty_vote_seconds == 60


def test_fiftyfifty_seconds_override_and_clamp(monkeypatch):
    s = settings(
        monkeypatch,
        FIFTYFIFTY_PROMPTS_FILE="deck.yaml",
        FIFTYFIFTY_WRITE_SECONDS="45",
        FIFTYFIFTY_VOTE_SECONDS="20",
    )
    assert s.fiftyfifty_prompts_file == "deck.yaml"
    assert s.fiftyfifty_write_seconds == 45 and s.fiftyfifty_vote_seconds == 20
    low = settings(monkeypatch, FIFTYFIFTY_WRITE_SECONDS="5", FIFTYFIFTY_VOTE_SECONDS="5")
    assert low.fiftyfifty_write_seconds == 30 and low.fiftyfifty_vote_seconds == 10


def test_spectrum_defaults(monkeypatch):
    s = settings(monkeypatch)
    assert s.spectrum_scales_file == "spectrum_scales.yaml"
    assert s.spectrum_clue_seconds == 120
    assert s.spectrum_guess_seconds == 120


def test_spectrum_seconds_override_and_clamp(monkeypatch):
    s = settings(
        monkeypatch,
        SPECTRUM_SCALES_FILE="deck.yaml",
        SPECTRUM_CLUE_SECONDS="45",
        SPECTRUM_GUESS_SECONDS="25",
    )
    assert s.spectrum_scales_file == "deck.yaml"
    assert s.spectrum_clue_seconds == 45 and s.spectrum_guess_seconds == 25
    low = settings(monkeypatch, SPECTRUM_CLUE_SECONDS="5", SPECTRUM_GUESS_SECONDS="5")
    assert low.spectrum_clue_seconds == 30 and low.spectrum_guess_seconds == 20


def test_headlines_defaults(monkeypatch):
    s = settings(monkeypatch)
    assert s.headlines_questions_file == "headlines_questions.yaml"
    assert s.headlines_answer_seconds == 90
    assert s.headlines_write_seconds == 120
    assert s.headlines_vote_seconds == 60


def test_headlines_seconds_override_and_clamp(monkeypatch):
    s = settings(
        monkeypatch,
        HEADLINES_QUESTIONS_FILE="deck.yaml",
        HEADLINES_ANSWER_SECONDS="45",
        HEADLINES_WRITE_SECONDS="50",
        HEADLINES_VOTE_SECONDS="25",
    )
    assert s.headlines_questions_file == "deck.yaml"
    assert (s.headlines_answer_seconds, s.headlines_write_seconds) == (45, 50)
    assert s.headlines_vote_seconds == 25
    low = settings(
        monkeypatch,
        HEADLINES_ANSWER_SECONDS="5",
        HEADLINES_WRITE_SECONDS="5",
        HEADLINES_VOTE_SECONDS="5",
    )
    assert (low.headlines_answer_seconds, low.headlines_write_seconds) == (30, 30)
    assert low.headlines_vote_seconds == 20

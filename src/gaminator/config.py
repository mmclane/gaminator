import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    discord_token: str
    guild_ids: tuple[int, ...]
    database_path: str
    log_level: str
    # Assassin
    poison_words_file: str
    sweep_minutes: int
    # Imposter
    imposter_prompts_file: str
    # Most Likely To
    mostlikely_prompts_file: str
    mostlikely_vote_seconds: int
    # Fifty-Fifty
    fiftyfifty_prompts_file: str
    fiftyfifty_write_seconds: int
    fiftyfifty_vote_seconds: int
    spectrum_scales_file: str
    spectrum_clue_seconds: int
    spectrum_guess_seconds: int
    headlines_questions_file: str
    headlines_answer_seconds: int
    headlines_write_seconds: int
    headlines_vote_seconds: int
    # Testing: lower every game's minimum player count (None = each game's normal minimum)
    min_players: int | None

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("DISCORD_TOKEN")
        if not token:
            raise SystemExit("DISCORD_TOKEN is not set")
        raw = os.environ.get("GUILD_IDS") or os.environ.get("GUILD_ID") or ""
        guild_ids = tuple(int(x) for x in raw.replace(";", ",").split(",") if x.strip())
        return cls(
            discord_token=token,
            guild_ids=guild_ids,
            database_path=os.environ.get("DATABASE_PATH", "/data/gaminator.db"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            poison_words_file=os.environ.get("POISON_WORDS_FILE", "poison_words.yaml"),
            sweep_minutes=max(1, int(os.environ.get("SWEEP_MINUTES", "10"))),
            imposter_prompts_file=os.environ.get("IMPOSTER_PROMPTS_FILE", "prompts.yaml"),
            mostlikely_prompts_file=os.environ.get(
                "MOSTLIKELY_PROMPTS_FILE", "mostlikely_prompts.yaml"
            ),
            mostlikely_vote_seconds=max(10, int(os.environ.get("MOSTLIKELY_VOTE_SECONDS", "120"))),
            fiftyfifty_prompts_file=os.environ.get(
                "FIFTYFIFTY_PROMPTS_FILE", "fiftyfifty_prompts.yaml"
            ),
            fiftyfifty_write_seconds=max(
                30, int(os.environ.get("FIFTYFIFTY_WRITE_SECONDS", "120"))
            ),
            fiftyfifty_vote_seconds=max(10, int(os.environ.get("FIFTYFIFTY_VOTE_SECONDS", "60"))),
            spectrum_scales_file=os.environ.get("SPECTRUM_SCALES_FILE", "spectrum_scales.yaml"),
            spectrum_clue_seconds=max(30, int(os.environ.get("SPECTRUM_CLUE_SECONDS", "120"))),
            spectrum_guess_seconds=max(20, int(os.environ.get("SPECTRUM_GUESS_SECONDS", "120"))),
            headlines_questions_file=os.environ.get(
                "HEADLINES_QUESTIONS_FILE", "headlines_questions.yaml"
            ),
            headlines_answer_seconds=max(30, int(os.environ.get("HEADLINES_ANSWER_SECONDS", "90"))),
            headlines_write_seconds=max(30, int(os.environ.get("HEADLINES_WRITE_SECONDS", "120"))),
            headlines_vote_seconds=max(20, int(os.environ.get("HEADLINES_VOTE_SECONDS", "60"))),
            min_players=int(os.environ["MIN_PLAYERS"]) if os.environ.get("MIN_PLAYERS") else None,
        )

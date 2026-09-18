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
            min_players=int(os.environ["MIN_PLAYERS"]) if os.environ.get("MIN_PLAYERS") else None,
        )

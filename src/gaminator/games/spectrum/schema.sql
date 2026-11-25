CREATE TABLE IF NOT EXISTS spectrum_games (
  id INTEGER PRIMARY KEY,
  guild_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open','active','finished')),
  max_rounds INTEGER NOT NULL DEFAULT 1,
  channel_id INTEGER NOT NULL,
  lobby_message_id INTEGER,
  created_by INTEGER NOT NULL,
  host_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS spectrum_one_current_game_per_guild
  ON spectrum_games(guild_id) WHERE status != 'finished';

CREATE TABLE IF NOT EXISTS spectrum_players (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES spectrum_games(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  joined_at TEXT NOT NULL,
  left_at TEXT,
  UNIQUE(game_id, user_id)
);

CREATE TABLE IF NOT EXISTS spectrum_rounds (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES spectrum_games(id) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  closed_at TEXT,
  UNIQUE(game_id, number)
);

CREATE TABLE IF NOT EXISTS spectrum_turns (
  id INTEGER PRIMARY KEY,
  round_id INTEGER NOT NULL REFERENCES spectrum_rounds(id) ON DELETE CASCADE,
  giver_id INTEGER NOT NULL REFERENCES spectrum_players(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  left_end TEXT NOT NULL,
  right_end TEXT NOT NULL,
  target INTEGER NOT NULL CHECK (target BETWEEN 1 AND 100),
  started_at TEXT,
  clue_deadline_at TEXT,
  turn_message_id INTEGER,
  clue TEXT,
  clue_at TEXT,
  guess_deadline_at TEXT,
  guess_message_id INTEGER,
  eligible_guessers INTEGER,
  closed_at TEXT,
  outcome TEXT CHECK (outcome IN ('revealed','no_clue','skipped','abandoned')),
  UNIQUE(round_id, giver_id),
  UNIQUE(round_id, position)
);
CREATE INDEX IF NOT EXISTS spectrum_open_clues
  ON spectrum_turns(clue_deadline_at)
  WHERE started_at IS NOT NULL AND clue IS NULL AND closed_at IS NULL;
CREATE INDEX IF NOT EXISTS spectrum_open_guessing
  ON spectrum_turns(guess_deadline_at) WHERE clue IS NOT NULL AND closed_at IS NULL;

CREATE TABLE IF NOT EXISTS spectrum_turn_guessers (
  turn_id INTEGER NOT NULL REFERENCES spectrum_turns(id) ON DELETE CASCADE,
  player_id INTEGER NOT NULL REFERENCES spectrum_players(id) ON DELETE CASCADE,
  PRIMARY KEY (turn_id, player_id)
);

CREATE TABLE IF NOT EXISTS spectrum_guesses (
  turn_id INTEGER NOT NULL REFERENCES spectrum_turns(id) ON DELETE CASCADE,
  guesser_id INTEGER NOT NULL REFERENCES spectrum_players(id) ON DELETE CASCADE,
  value INTEGER NOT NULL CHECK (value BETWEEN 1 AND 100),
  made_at TEXT NOT NULL,
  PRIMARY KEY (turn_id, guesser_id)
);

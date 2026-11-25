CREATE TABLE IF NOT EXISTS headlines_games (
  id INTEGER PRIMARY KEY,
  guild_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open','active','finished')),
  max_rounds INTEGER NOT NULL DEFAULT 2,
  channel_id INTEGER NOT NULL,
  lobby_message_id INTEGER,
  created_by INTEGER NOT NULL,
  host_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS headlines_one_current_game_per_guild
  ON headlines_games(guild_id) WHERE status != 'finished';

CREATE TABLE IF NOT EXISTS headlines_players (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES headlines_games(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  joined_at TEXT NOT NULL,
  left_at TEXT,
  UNIQUE(game_id, user_id)
);

CREATE TABLE IF NOT EXISTS headlines_rounds (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES headlines_games(id) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  answer_deadline_at TEXT NOT NULL,
  answer_message_id INTEGER,
  answer_closed_at TEXT,
  write_deadline_at TEXT,
  write_message_id INTEGER,
  write_closed_at TEXT,
  reveal_active_at TEXT,
  vote_deadline_at TEXT,
  vote_message_id INTEGER,
  eligible_voters INTEGER,
  vote_closed_at TEXT,
  closed_at TEXT,
  outcome TEXT CHECK (outcome IN ('voted','no_answers','no_headlines','one_headline','abandoned'))
);
CREATE UNIQUE INDEX IF NOT EXISTS headlines_round_number
  ON headlines_rounds(game_id, number) WHERE outcome IS NOT 'abandoned';
CREATE INDEX IF NOT EXISTS headlines_open_answering
  ON headlines_rounds(answer_deadline_at)
  WHERE answer_closed_at IS NULL AND closed_at IS NULL;
CREATE INDEX IF NOT EXISTS headlines_open_writing
  ON headlines_rounds(write_deadline_at)
  WHERE write_deadline_at IS NOT NULL AND write_closed_at IS NULL AND closed_at IS NULL;
CREATE INDEX IF NOT EXISTS headlines_open_voting
  ON headlines_rounds(vote_deadline_at)
  WHERE vote_deadline_at IS NOT NULL AND vote_closed_at IS NULL AND closed_at IS NULL;

CREATE TABLE IF NOT EXISTS headlines_answers (
  id INTEGER PRIMARY KEY,
  round_id INTEGER NOT NULL REFERENCES headlines_rounds(id) ON DELETE CASCADE,
  player_id INTEGER NOT NULL REFERENCES headlines_players(id) ON DELETE CASCADE,
  question TEXT NOT NULL,
  answer TEXT,
  UNIQUE(round_id, player_id)
);

CREATE TABLE IF NOT EXISTS headlines_pairings (
  id INTEGER PRIMARY KEY,
  round_id INTEGER NOT NULL REFERENCES headlines_rounds(id) ON DELETE CASCADE,
  answer_id INTEGER NOT NULL UNIQUE REFERENCES headlines_answers(id) ON DELETE CASCADE,
  writer_id INTEGER NOT NULL REFERENCES headlines_players(id) ON DELETE CASCADE,
  headline TEXT,
  position INTEGER,
  shown_at TEXT,
  UNIQUE(round_id, writer_id)
);

CREATE TABLE IF NOT EXISTS headlines_votes (
  round_id INTEGER NOT NULL REFERENCES headlines_rounds(id) ON DELETE CASCADE,
  voter_id INTEGER NOT NULL REFERENCES headlines_players(id) ON DELETE CASCADE,
  pairing_id INTEGER NOT NULL REFERENCES headlines_pairings(id) ON DELETE CASCADE,
  cast_at TEXT NOT NULL,
  PRIMARY KEY (round_id, voter_id)
);

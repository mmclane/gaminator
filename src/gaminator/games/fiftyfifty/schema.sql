CREATE TABLE IF NOT EXISTS fiftyfifty_games (
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
CREATE UNIQUE INDEX IF NOT EXISTS fiftyfifty_one_current_game_per_guild
  ON fiftyfifty_games(guild_id) WHERE status != 'finished';

CREATE TABLE IF NOT EXISTS fiftyfifty_players (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES fiftyfifty_games(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  joined_at TEXT NOT NULL,
  left_at TEXT,
  UNIQUE(game_id, user_id)
);
CREATE INDEX IF NOT EXISTS fiftyfifty_players_by_user
  ON fiftyfifty_players(user_id);

CREATE TABLE IF NOT EXISTS fiftyfifty_rounds (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES fiftyfifty_games(id) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  write_deadline_at TEXT NOT NULL,
  write_channel_id INTEGER,
  write_message_id INTEGER,
  write_closed_at TEXT,
  closed_at TEXT,
  abandoned INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS fiftyfifty_round_number
  ON fiftyfifty_rounds(game_id, number) WHERE abandoned = 0;
CREATE INDEX IF NOT EXISTS fiftyfifty_open_writing
  ON fiftyfifty_rounds(write_deadline_at) WHERE write_closed_at IS NULL;

CREATE TABLE IF NOT EXISTS fiftyfifty_round_players (
  round_id INTEGER NOT NULL REFERENCES fiftyfifty_rounds(id) ON DELETE CASCADE,
  player_id INTEGER NOT NULL REFERENCES fiftyfifty_players(id) ON DELETE CASCADE,
  PRIMARY KEY (round_id, player_id)
);

CREATE TABLE IF NOT EXISTS fiftyfifty_questions (
  id INTEGER PRIMARY KEY,
  round_id INTEGER NOT NULL REFERENCES fiftyfifty_rounds(id) ON DELETE CASCADE,
  writer_id INTEGER NOT NULL REFERENCES fiftyfifty_players(id) ON DELETE CASCADE,
  option_a TEXT NOT NULL,
  option_b TEXT NOT NULL,
  topic TEXT,
  answer TEXT,
  answered_at TEXT,
  dm_prompted_at TEXT,
  position INTEGER,
  started_at TEXT,
  deadline_at TEXT,
  vote_channel_id INTEGER,
  vote_message_id INTEGER,
  closed_at TEXT,
  abandoned INTEGER NOT NULL DEFAULT 0,
  UNIQUE(round_id, writer_id)
);
CREATE INDEX IF NOT EXISTS fiftyfifty_open_questions
  ON fiftyfifty_questions(deadline_at) WHERE started_at IS NOT NULL AND closed_at IS NULL;

CREATE TABLE IF NOT EXISTS fiftyfifty_votes (
  question_id INTEGER NOT NULL REFERENCES fiftyfifty_questions(id) ON DELETE CASCADE,
  voter_id INTEGER NOT NULL REFERENCES fiftyfifty_players(id) ON DELETE CASCADE,
  choice TEXT NOT NULL CHECK (choice IN ('a','b')),
  cast_at TEXT NOT NULL,
  PRIMARY KEY (question_id, voter_id)
);

CREATE TABLE IF NOT EXISTS fiftyfifty_guesses (
  question_id INTEGER NOT NULL REFERENCES fiftyfifty_questions(id) ON DELETE CASCADE,
  voter_id INTEGER NOT NULL REFERENCES fiftyfifty_players(id) ON DELETE CASCADE,
  a_votes INTEGER NOT NULL CHECK (a_votes >= 0),
  made_at TEXT NOT NULL,
  PRIMARY KEY (question_id, voter_id)
);

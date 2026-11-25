CREATE TABLE IF NOT EXISTS mostlikely_games (
  id INTEGER PRIMARY KEY,
  guild_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open','active','finished')),
  max_rounds INTEGER NOT NULL DEFAULT 10,
  channel_id INTEGER NOT NULL,
  lobby_message_id INTEGER,
  round INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER NOT NULL,
  host_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS mostlikely_one_current_game_per_guild
  ON mostlikely_games(guild_id) WHERE status != 'finished';

CREATE TABLE IF NOT EXISTS mostlikely_players (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES mostlikely_games(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  joined_at TEXT NOT NULL,
  left_at TEXT,
  UNIQUE(game_id, user_id)
);

CREATE TABLE IF NOT EXISTS mostlikely_rounds (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES mostlikely_games(id) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  prompt TEXT NOT NULL,
  started_at TEXT NOT NULL,
  deadline_at TEXT NOT NULL,
  vote_channel_id INTEGER,
  vote_message_id INTEGER,
  closed_at TEXT,
  abandoned INTEGER NOT NULL DEFAULT 0,
  UNIQUE(game_id, number)
);
CREATE INDEX IF NOT EXISTS mostlikely_open_rounds
  ON mostlikely_rounds(deadline_at) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS mostlikely_round_players (
  round_id INTEGER NOT NULL REFERENCES mostlikely_rounds(id) ON DELETE CASCADE,
  player_id INTEGER NOT NULL REFERENCES mostlikely_players(id) ON DELETE CASCADE,
  PRIMARY KEY (round_id, player_id)
);

CREATE TABLE IF NOT EXISTS mostlikely_votes (
  round_id INTEGER NOT NULL REFERENCES mostlikely_rounds(id) ON DELETE CASCADE,
  voter_id INTEGER NOT NULL REFERENCES mostlikely_players(id) ON DELETE CASCADE,
  target_id INTEGER NOT NULL REFERENCES mostlikely_players(id) ON DELETE CASCADE,
  cast_at TEXT NOT NULL,
  PRIMARY KEY (round_id, voter_id)
);

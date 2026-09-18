-- Assassin. One unfinished game per server; every table is prefixed with the game key.
CREATE TABLE IF NOT EXISTS assassin_games (
  id INTEGER PRIMARY KEY,
  guild_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open','active','finished')),
  -- One reaction triggers every method (poison, trap, quick draw); the bot works out which.
  kill_emoji TEXT NOT NULL DEFAULT '☠️',
  shield_emoji TEXT NOT NULL DEFAULT '🛡️',
  bounty_hours INTEGER NOT NULL DEFAULT 48,
  bounty_player_id INTEGER,
  inactivity_hours INTEGER NOT NULL DEFAULT 24,
  max_wrong_reports INTEGER NOT NULL DEFAULT 3,
  reveal_killer INTEGER NOT NULL DEFAULT 1,
  winner_id INTEGER,
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS assassin_one_current_game_per_guild
  ON assassin_games(guild_id) WHERE status != 'finished';

CREATE TABLE IF NOT EXISTS assassin_players (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES assassin_games(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  alive INTEGER NOT NULL DEFAULT 1,
  target_id INTEGER REFERENCES assassin_players(id),
  poison_word TEXT,
  bait_word TEXT,
  last_kill_at TEXT,
  kills INTEGER NOT NULL DEFAULT 0,
  wrong_reports INTEGER NOT NULL DEFAULT 0,
  last_message_at TEXT,
  inactivity_warned INTEGER NOT NULL DEFAULT 0,
  eliminated_at TEXT,
  eliminated_by INTEGER REFERENCES assassin_players(id),
  cause TEXT,
  joined_at TEXT NOT NULL,
  UNIQUE(game_id, user_id)
);

-- Channels the game is played in. Empty means every channel the bot can read.
CREATE TABLE IF NOT EXISTS assassin_channels (
  game_id INTEGER NOT NULL REFERENCES assassin_games(id) ON DELETE CASCADE,
  channel_id INTEGER NOT NULL,
  PRIMARY KEY (game_id, channel_id)
);

CREATE TABLE IF NOT EXISTS assassin_events (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES assassin_games(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  actor_id INTEGER,
  victim_id INTEGER,
  detail TEXT,
  created_at TEXT NOT NULL
);

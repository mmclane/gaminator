-- Imposter. One unfinished game per server; every table is prefixed with the game key.
CREATE TABLE IF NOT EXISTS imposter_games (
  id INTEGER PRIMARY KEY,
  guild_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open','active','finished')),
  -- Within an active game: 'discussion' (answering the prompt) or 'voting'.
  phase TEXT NOT NULL DEFAULT 'signup' CHECK (phase IN ('signup','discussion','voting','over')),
  imposters INTEGER NOT NULL DEFAULT 1,
  -- Imposters win if any of them are still uncaught after this many rounds.
  max_rounds INTEGER NOT NULL DEFAULT 5,
  -- Whether imposters are told the prompt's clue.
  imposter_hint INTEGER NOT NULL DEFAULT 1,
  channel_id INTEGER,
  round INTEGER NOT NULL DEFAULT 0,
  next_prompt TEXT,
  next_hint TEXT,
  winner TEXT CHECK (winner IN ('crew','imposters')),
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS imposter_one_current_game_per_guild
  ON imposter_games(guild_id) WHERE status != 'finished';

CREATE TABLE IF NOT EXISTS imposter_players (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES imposter_games(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  alive INTEGER NOT NULL DEFAULT 1,
  imposter INTEGER NOT NULL DEFAULT 0,
  eliminated_round INTEGER,
  cause TEXT,
  joined_at TEXT NOT NULL,
  UNIQUE(game_id, user_id)
);

CREATE TABLE IF NOT EXISTS imposter_rounds (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES imposter_games(id) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  prompt TEXT NOT NULL,
  hint TEXT,
  started_at TEXT NOT NULL,
  vote_channel_id INTEGER,
  vote_message_id INTEGER,
  voting_opened_at TEXT,
  closed_at TEXT,
  ejected_id INTEGER REFERENCES imposter_players(id),
  -- 'imposter' or 'innocent' (someone was voted out), 'tie', 'nobody', 'no_votes'
  result TEXT,
  UNIQUE(game_id, number)
);

-- target_id NULL means the voter chose "nobody".
CREATE TABLE IF NOT EXISTS imposter_votes (
  round_id INTEGER NOT NULL REFERENCES imposter_rounds(id) ON DELETE CASCADE,
  voter_id INTEGER NOT NULL REFERENCES imposter_players(id) ON DELETE CASCADE,
  target_id INTEGER REFERENCES imposter_players(id),
  cast_at TEXT NOT NULL,
  PRIMARY KEY (round_id, voter_id)
);

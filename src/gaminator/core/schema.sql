-- Shared, per-server settings. Each game keeps its own tables (see games/<game>/schema.sql).
CREATE TABLE IF NOT EXISTS guild_settings (
  guild_id INTEGER PRIMARY KEY,
  admin_role_id INTEGER,
  announce_channel_id INTEGER
);

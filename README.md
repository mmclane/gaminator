# Gaminator (the Gameinator 3000)

One Discord bot that hosts several server games. Each game lives under its own slash-command
group, keeps its own tables, and can run **once at a time per server**: a server can have one
Assassin game and one Imposter game going at the same time, but never two of the same game.

| Game | Commands | State |
|---|---|---|
| **Assassin** | `/assassin …` (players) and `/assassin-admin …` (organizers) | Playable |
| **Imposter** | `/imposter …` (players) and `/imposter-admin …` (organizers) | Playable |

Server-wide settings shared by every game live under `/gaminator`.

## 1. Create the Discord application

1. Open https://discord.com/developers/applications and click **New Application**.
2. On the **Bot** tab, click **Reset Token** and copy the token. Leave **Public Bot** off.
3. Under **Privileged Gateway Intents**, turn on **Message Content Intent**. Assassin has to
   read messages to spot poison words. Leave the other two off.
4. On the **Installation** tab, enable **Guild Install** only.
5. On **OAuth2 > URL Generator**, tick the scopes `bot` and `applications.commands`.
6. Under **Bot Permissions**, tick: View Channels, Send Messages, Read Message History,
   Add Reactions, Manage Messages (optional; used to clean up failed kill reactions).
7. Open the generated URL and invite the bot to your server.

## 2. Run the bot

```sh
cp .env.example .env      # fill in DISCORD_TOKEN and GUILD_ID
make start                # docker compose up -d --build
make logs                 # follow the logs
make stop                 # clean shutdown; the database volume is kept
```

Other targets: `make restart`, `make rebuild`, `make status`, `make test`, `make lint`.
`docker` may be podman on your machine; compose behaves the same.

`GUILD_ID` is your server's ID (Developer Mode > right-click the server > Copy Server ID).
Setting it makes slash commands appear immediately. Several servers: `GUILD_ID=111,222`. Each
server has its own games and settings.

Data lives in the `botdata` volume at `/data/gaminator.db`. Back it up with:

```sh
docker compose cp bot:/data/gaminator.db ./backup.db
```

## 3. Server setup (once)

`/gaminator`, `/assassin-admin`, and `/imposter-admin` are hidden from members without
**Manage Server**. To let others host games, run `/gaminator admin-role role:@Game Hosts`;
organizer commands for every game then accept that role too. Then make those three command
groups *visible* to the role under **Server Settings > Integrations > Gaminator > Manage**
(Discord decides who can see a command; the bot decides who can run it).

- `/gaminator announce channel:#games` chooses where every game posts its announcements
  (eliminations, winners, and a pointer to new games created in other channels). An Assassin
  game can override this for its own announcements with `/assassin-admin settings
  announce_channel:#assassin`.
- `/gaminator status` shows which games are running right now.

## 4. Assassin

Assassin is the classic [Assassin game](https://en.wikipedia.org/wiki/Assassin_(game)). Everyone
who joins gets one target, a secret poison word, and a secret bait word by DM. Eliminate your
target and you inherit theirs. Last one standing wins.

Everything happens in the normal chat: kills are emoji reactions on real messages, so the game
rewards people who are around and paying attention. There is a single kill reaction (☠️ by
default) for all three methods; the bot checks the message and applies whichever fits, trying
poison first, then trap, then quick draw.

### How eliminations work

| Method | How | Who does it |
|---|---|---|
| **Poison word** ☠️ | Your target uses your poison word in a message. React to that message with the kill emoji. | Assassin |
| **Quick draw** 🔪 | React to your target's latest message with the kill emoji before anyone posts another message in that channel. | Assassin |
| **Trap** 🪤 | Work your secret bait word into a message. If your target *replies* to that message, react to their reply with the kill emoji. | Assassin |
| **Report** `/assassin report @user` | Name the person you think is hunting you. Right: they are arrested and eliminated. Wrong: strike one. Three wrong reports and you are out. | Target |
| **Inactivity** 💤 | Go 24 hours (configurable) without posting anywhere in the game's channels and you are eliminated. Players get a DM warning at 75% of the window. The check runs every 10 minutes; set `SWEEP_MINUTES` in `.env` to change that. | The bot |

**Bounty** 💰: if nobody is eliminated for 48 hours (configurable, 0 turns it off), the bot puts a
bounty on the living player who has gone longest without a kill. Until the next elimination,
*anyone* can quick-draw them, not just their hunter. The bounty targets passive assassins rather
than quiet posters, so it doesn't stack with the inactivity timeout. Set `bounty_hours` higher
than `inactivity_hours` if you want quiet players to be eliminated before bounties start.

**Shield** 🛡️: react to your *own* message with the shield emoji and that message can no longer
be used against you. It has to be there before the assassin reacts.

When a player is eliminated, the bot DMs them, DMs the hunter their new target and a fresh
poison word, and posts an announcement in the announce channel. By default the announcement
names the killer; turn that off with `/assassin-admin settings reveal_killer:False`.

**Mod channel** 🔒: set `/assassin-admin settings mod_channel:#assassin-mods` and organizers
get a private feed: every elimination with the killer always named (whatever `reveal_killer`
says), the poison or bait word involved, and who now hunts whom, plus bounties and the end of
the game. Make the channel visible only to organizers; the bot just needs to post there.

Failed attempts on your own target (wrong word, shielded, too slow) get you a private DM
explaining why, and the bot removes the reaction if it can. Reactions on anyone else's messages
are ignored silently.

### Poison words

The built-in list is `src/gaminator/games/assassin/poison_words.yaml` (about 135 everyday
words). To use your own, create a YAML file with a `words:` list and point `POISON_WORDS_FILE`
at it in `.env`. Multi-word phrases are fine. Matching is case-insensitive and whole-word:
`banana` matches `BANANA!` and `*banana*` but not `bananas`.

### Organizer runbook

Organizer commands live under `/assassin-admin`, hidden from everyone without **Manage Server**
unless a server admin has opened it to the role set with `/gaminator admin-role`.

1. `/assassin-admin create name:Autumn Assassin` opens sign-ups and posts an introduction (how
   to join, how the game works) in the channel where you ran it. Players use `/assassin join`.
2. `/assassin-admin settings` shows the defaults. Change any of them any time:
   `inactivity_hours`, `bounty_hours`, `max_wrong_reports`, `reveal_killer`, `kill_emoji`,
   `shield_emoji`. Custom server emoji work: paste them as you would in a message.
   `announce_channel` sends this game's public announcements (joins, eliminations, bounties,
   winner) to a channel of its own instead of the server-wide `/gaminator announce` channel;
   `mod_channel` adds the organizer feed described above. `clear_channel:announce|mod|both`
   unsets them.
3. Optional: `/assassin-admin channels action:add channel:#general` to limit the game to
   specific channels. With no channels listed, every channel the bot can read counts, threads
   included. `action:list`, `action:remove`, and `action:clear` manage the list.
4. `/assassin-admin start` builds the target chain and DMs everyone. Needs at least 3 players.
   The reply lists anyone whose DMs were closed; they can use `/assassin status` or
   `/assassin resend`.
5. Checking on the game: `/assassin-admin status` shows every living player, their target,
   poison word, kills, wrong reports, and when they last posted, plus who is out and why.
   `/assassin-admin player user:@someone` gives one player's details including who is hunting
   them. `/assassin-admin log` lists the recent events.
6. Intervening: `/assassin-admin execute user:@someone reason:...` eliminates a player "by the
   state" with a public announcement and the reason. `/assassin-admin remove user:@someone`
   drops a player quietly (announced as removed by an organizer). `/assassin-admin nudge`
   re-sends every assignment and `/assassin-admin broadcast message:...` DMs all living players.
7. The game ends itself when one player is left. `/assassin-admin end` stops it early.

### What players see

- `/assassin join` and `/assassin leave` while sign-ups are open (`/assassin leave` during a
  game counts as elimination).
- `/assassin rules` explains the game with the current emoji and limits.
- `/assassin status` shows your target, poison word, bait word, kills, wrong reports left, and
  your inactivity deadline.
- `/assassin report user:@someone` accuses your suspected assassin.
- `/assassin players` lists who is still alive.
- `/assassin resend` DMs your assignment again.

All replies are ephemeral, so nothing leaks in chat.

## 5. Imposter

Every round, all players get the same secret prompt by DM, except the imposters, who are told
only that they are the imposter, plus a vague clue. Prompts are questions to answer or small
things to do ("post one emoji that sums up your morning"). Players answer in the game channel,
try to spot who is bluffing, and vote. The game lasts a set number of rounds: catch every
imposter before they run out and the crew wins; otherwise the imposters do.

### Rules

- **Prompt**: crew members get the prompt; imposters get "you are the imposter" and, unless
  the `hint` setting is off, the prompt's clue (e.g. *It's about food*). With several
  imposters, they are told who the others are.
- **Vote**: the organizer opens a vote when everyone has answered. The bot posts a dropdown of
  living players plus a "No accusation" option for abstaining. Only living players can vote, once each, and they can
  change their vote until it closes. Votes are secret until then. The vote closes on its own
  once every living player has voted, or when the organizer closes it.
- **Result**: plurality wins; any tie means nobody is accused. An accused imposter is caught
  and out of the game. An accused innocent stays in: nobody innocent is ever eliminated, the
  crew has simply spent a round. The prompt is revealed after each vote, and the next round
  starts immediately.
- **Winning**: the crew wins when every imposter is caught. The imposters win if any of them
  are still uncaught after the last round (`rounds`, default 5).
- A game needs at least `2 × imposters + 1` players so the crew starts with a majority, and at
  most 24 players (the vote dropdown's limit).

### Prompts

The built-in list is `src/gaminator/games/imposter/prompts.yaml` (40 prompts with clues). To
use your own, create a YAML file with a `prompts:` list and point `IMPOSTER_PROMPTS_FILE` at
it in `.env`. Entries are either plain strings or `prompt:` / `hint:` pairs. Prompts are not
repeated within a game. Organizers can also set a one-off prompt with `/imposter-admin prompt`.

### Organizer runbook

Organizer commands live under `/imposter-admin`, hidden from everyone without **Manage Server**
unless a server admin has opened it to the role set with `/gaminator admin-role`.

1. `/imposter-admin create name:Friday Imposter imposters:2 rounds:6`, run in the channel the
   game will be played in. `imposters` and `rounds` are optional (defaults 1 and 5). It posts
   an introduction there (how to join, how the game works) and makes that the game channel.
   Players use `/imposter join`.
2. `/imposter-admin settings imposters:2 rounds:5 hint:False` shows or changes the settings. The
   imposter count can only change before the game starts; the round count can be raised
   mid-game.
3. `/imposter-admin start` picks the imposters, DMs everyone round 1, and posts the round
   announcement in the game channel. It can be run from anywhere. `/imposter-admin channel
   channel:#other` moves the game; the bot refuses channels it can't post in.
4. When everyone has answered: `/imposter-admin vote`. The bot posts the dropdown. It resolves
   itself once all living players have voted, or use `/imposter-admin close` to force it. The
   next round's prompts go out right after.
5. Optional: `/imposter-admin nudge` re-posts the round announcement and re-DMs everyone their
   current prompt (useful after moving the game channel). `/imposter-admin reroll` swaps the
   current round's prompt for a new one and re-DMs everyone. `/imposter-admin prompt text:... hint:...` queues a custom prompt for the next
   round; `clear:True` forgets it.
6. `/imposter-admin status` shows the imposters, who is in, the current prompt, and who hasn't
   voted yet. `/imposter-admin remove user:@someone` drops a player.
7. The game ends itself when a side wins. `/imposter-admin end` stops it early and reveals the
   imposters.

### What players see

- `/imposter join` and `/imposter leave` (leaving during a game removes you from it).
- `/imposter rules` explains the game with the current settings.
- `/imposter status` shows your role, this round's prompt or clue, and whether you've voted.
- `/imposter players` lists who is still in.
- `/imposter resend` DMs your prompt again.

## Hosting for free on Google Cloud

A Discord bot holds an outbound connection to Discord and receives no web traffic, so
"serverless" free tiers that sleep idle containers (SnapDeploy, Koyeb, Render) put it to sleep.
A plain VM has no idle concept. Google Cloud's Always Free tier gives one `e2-micro` VM in
`us-west1`, `us-central1`, or `us-east1` with a 30 GB standard disk, no expiry, and no charge
for its external IP. That's enough for this bot several times over.

1. Create a Google Cloud account and project (a card is required for the billing account; the
   VM stays free while it follows the rules above).
2. Push this project to a GitHub repository.
3. Open [Cloud Shell](https://shell.cloud.google.com), pick the project, and run
   `deploy/gcp/create-vm.sh` (paste it in, or clone the repo first). It creates the VM with the
   free-tier machine type, region, and disk.
4. SSH to the VM from Cloud Shell as the script's output shows, then run
   `deploy/gcp/setup-vm.sh <clone URL>`. It adds a swap file, installs Docker, clones the
   repo, asks for the Discord token and server ID to write `.env`, and starts the bot.
5. Updating later: on the VM, `bash deploy/gcp/update.sh` pulls and rebuilds.

The container restarts on its own after reboots, and the database lives on the VM's disk in
the `botdata` Docker volume. Back it up with the `docker compose cp` command in section 2.

## Testing with few people

Set `MIN_PLAYERS=2` in `.env` and restart. Every game then starts with two players (Imposter
still requires at least one crew member per imposter, so one imposter needs two players, two
imposters need three). Remove the line for real play.

## Development

```sh
uv sync --extra dev
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

### Layout

```
src/gaminator/
  bot.py, config.py, __main__.py    the bot, its settings, the entry point
  core/                             shared by every game
    db.py, repo.py, schema.sql      SQLite connection, repo base class, guild_settings table
    util.py                         errors, admin check, require_game/require_player, respond
    messaging.py                    display names, DMs, the announce channel
    registry.py                     GameSpec: how a game plugs in
    commands.py                     the /gaminator group
  games/
    __init__.py                     GAMES: the list of registered games
    assassin/                       one package per game
      __init__.py                   SPEC (key, title, extensions, make_context)
      schema.sql, repo.py           tables prefixed assassin_, all database access
      context.py                    what bot.games["assassin"] holds (repo, word list)
      commands/                     /assassin (player.py) and /assassin-admin (admin.py)
      events.py                     message and reaction listeners, the periodic sweep
      services/                     pure rules, chain building, word matching, Discord flow
    imposter/
      __init__.py, schema.sql, repo.py, context.py, commands/   same shape as assassin
      prompts.yaml, services/prompts.py   the prompt list and loader
      services/rules.py                   imposter choice, vote tally, win conditions
      services/game.py                    dealing prompts, opening/closing votes, ending
      vote.py                             the persistent vote dropdown
```

### Adding a game

1. Create `src/gaminator/games/<key>/` with a `schema.sql` whose tables are all prefixed
   `<key>_` and include a unique index on `guild_id` for unfinished games, so the game runs once
   per server.
2. Give it a repo (subclass `core.repo.BaseRepo`) with at least `get_current_game(guild_id)`
   returning rows that expose `name` and `status`.
3. Define its slash commands on an `app_commands.Group(name="<key>")` in an extension module
   with `setup`/`teardown` that add and remove the groups from `bot.tree`. Put organizer
   commands in a separate top-level `<key>-admin` group with `default_permissions` set to
   Manage Server, and still check `is_game_admin` inside them.
4. Export a `SPEC = GameSpec(...)` from the package and add it to `GAMES` in `games/__init__.py`.
   `GameSpec.migrate` is an optional hook for one-off table upgrades, run before the schema.
   Errors raised as `core.util.GameError` are reported to the user by the shared handler.

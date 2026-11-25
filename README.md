# Gaminator (the Gameinator 3000)

One Discord bot that hosts several server games. Each game has its own slash commands, keeps
its own tables, and can run **once at a time per server**: a server can have one Assassin game,
one Imposter game, one Most Likely To game, one Fifty-Fifty game, one Spectrum game and one
Headlines game going at the same time, but never two of the same game.

| Game | Commands | State |
|---|---|---|
| **Assassin** | `/assassin …` (players) and `/assassin-admin …` (organizers) | Playable |
| **Imposter** | `/imposter …` (players) and `/imposter-admin …` (organizers) | Playable |
| **Most Likely To** | `/gaminator mostlikely …` (anyone can host) | Playable |
| **Fifty-Fifty** | `/gaminator fiftyfifty …` (anyone can host) | Playable |
| **Spectrum** | `/gaminator spectrum …` (anyone can host) | Playable |
| **Headlines** | `/gaminator headlines …` (anyone can host) | Playable |

Server-wide settings shared by every game live under `/gaminator-admin`. `/gaminator` is for
everyone: `/gaminator games` lists the games members can start, `/gaminator mostlikely` holds
Most Likely To, `/gaminator fiftyfifty` holds Fifty-Fifty, `/gaminator spectrum` holds
Spectrum, and `/gaminator headlines` holds Headlines.

## 1. Create the Discord application

1. Open https://discord.com/developers/applications and click **New Application**.
2. On the **Bot** tab, click **Reset Token** and copy the token. Leave **Public Bot** off.
3. Under **Privileged Gateway Intents**, turn on **Message Content Intent**. Assassin has to
   read messages to spot poison words. Leave the other two off.
4. On the **Installation** tab, enable **Guild Install** only.
5. On **OAuth2 > URL Generator**, tick the scopes `bot` and `applications.commands`.
6. Under **Bot Permissions**, tick: View Channels, Send Messages, Embed Links (the game cards
   need it), Read Message History, Add Reactions, Manage Messages (optional; used to clean up
   failed kill reactions).
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

`/gaminator-admin`, `/assassin-admin`, and `/imposter-admin` are hidden from members without
**Manage Server**. To let others host games, run `/gaminator-admin admin-role role:@Game Hosts`;
organizer commands for every game then accept that role too. Then make those three command
groups *visible* to the role under **Server Settings > Integrations > Gaminator > Manage**
(Discord decides who can see a command; the bot decides who can run it).

- `/gaminator-admin announce channel:#games` chooses where every game posts its announcements
  (eliminations, winners, and a pointer to new games created in other channels). An Assassin
  game can override this for its own announcements with `/assassin-admin settings
  announce_channel:#assassin`.
- `/gaminator-admin status` shows which games are running right now.
- `/gaminator games` posts a public card of the games members can start themselves (today
  that is Most Likely To), with how to start one and whether one is open or running here.
  Anyone can run it. Assassin and Imposter are left off the card; set `listed=True` in their
  `SPEC` to show them.
- `/gaminator mostlikely` needs no setup: `/gaminator` is visible to every member, and any
  member can host a game. If your server has a permission override on `/gaminator` from before
  the settings moved to `/gaminator-admin`, remove it under **Server Settings > Integrations**
  or members won't reach the game.

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
unless a server admin has opened it to the role set with `/gaminator-admin admin-role`.

1. `/assassin-admin create name:Autumn Assassin` opens sign-ups and posts an introduction (how
   to join, how the game works) in the channel where you ran it. Players use `/assassin join`.
2. `/assassin-admin settings` shows the defaults. Change any of them any time:
   `inactivity_hours`, `bounty_hours`, `max_wrong_reports`, `reveal_killer`, `kill_emoji`,
   `shield_emoji`. Custom server emoji work: paste them as you would in a message.
   `announce_channel` sends this game's public announcements (joins, eliminations, bounties,
   winner) to a channel of its own instead of the server-wide `/gaminator-admin announce`
   channel;
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
unless a server admin has opened it to the role set with `/gaminator-admin admin-role`.

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

## 6. Most Likely To

The bot posts a prompt such as "Most likely to name their car", everyone votes for a player,
and the bot shows the tally. Any member can host a game; there is no organizer group.

### The lobby card

`/gaminator mostlikely create` posts a card in the channel where you ran it, and the game is
played there. The card shows the host, the players in join order, and how many more are needed
to start (3 by default). It has four buttons:

- **Join** and **Leave** do the same as `/gaminator mostlikely join` and `leave`.
- **Rules** shows you the rules privately.
- **Start** works for the host or a game admin and plays 10 prompts. For another number, run
  `/gaminator mostlikely start rounds:<1-50>`.

Once the game starts, Start disappears and the other buttons keep working. When the game ends,
the card shows it finished and the buttons are removed. The buttons survive a bot restart.

### How a prompt plays

Each prompt is one message with a menu of the players who were in when it was posted. Pick
anyone on the menu, yourself included, and change your vote as often as you like until the
prompt closes. The message shows how many votes are in and a countdown.

A prompt closes when everyone on it has voted or after 2 minutes, whichever comes first. The
bot then posts the totals. Votes are secret: nobody sees who voted for whom. A tie is shown as
a tie. After the last prompt the bot posts a recap of who won the most prompts.

A game ends early if nobody votes on 2 prompts in a row, so a forgotten game stops by itself.

### Joining and leaving

Players can join and leave at any time, up to 25 players.

- Join during a prompt and you are in from the next one.
- Leave during a prompt and the votes you cast, and the votes for you, still count. The prompt
  closes at once if everyone still in has voted.
- If the host leaves, the longest-joined player becomes the host. A lobby with nobody left is
  cancelled.
- A running game ends with the recap when fewer than 2 players are left.
- Leave and rejoin and you keep the prompts you won. The recap includes players who left.

### Leaderboard

`/gaminator mostlikely leaderboard` posts the all-time standings for the server: games won,
prompts won, and games played. It shows the top 10, plus your own row if you rank lower.
The bot also posts the top 10 in the game channel when a game ends.
`/gaminator mostlikely leaderboard member:@someone` shows one member's numbers and the last
five prompts they won.

- A prompt win is getting the most votes on a prompt. A game win is winning the most prompts
  in a game. A tie counts as a win for each tied player.
- Only finished games count. A game ended early still counts for the prompts that closed.
- A prompt nobody voted on has no winner, and a prompt that was open when the game ended is
  left out.
- You have played a game if you were on at least one of its prompts.

### Prompts and timing

The built-in prompts are in
[`src/gaminator/games/mostlikely/prompts.yaml`](src/gaminator/games/mostlikely/prompts.yaml),
written without the "Most likely to" lead-in. To use your own, set `MOSTLIKELY_PROMPTS_FILE` to
a YAML file with a `prompts:` list. A prompt is not repeated within a game.
`MOSTLIKELY_VOTE_SECONDS` changes the 2 minute vote time (minimum 10 seconds).

### Commands

| Command | Who | What it does |
|---|---|---|
| `/gaminator mostlikely create [name]` | anyone | Opens a game in this channel and posts the lobby card. You become the host. |
| `/gaminator mostlikely join` | anyone | Joins the game, before or during play. |
| `/gaminator mostlikely leave` | players | Leaves the game. |
| `/gaminator mostlikely start [rounds]` | host or admin | Starts the game with 10 prompts, or the number you give. |
| `/gaminator mostlikely end` | host or admin | Ends the game and posts the recap, or cancels a lobby. |
| `/gaminator mostlikely status` | anyone | Shows the players, the current prompt and your own vote, privately. |
| `/gaminator mostlikely rules` | anyone | Shows the rules privately. |
| `/gaminator mostlikely leaderboard [member]` | anyone | Posts the all-time standings. |

"Admin" means Manage Server or the role set with `/gaminator-admin admin-role`. Only one game
runs per server at a time, so a lobby nobody starts blocks new games until its host or an admin
ends it.

## 7. Fifty-Fifty

Each player writes their own would-you-rather question, both options, such as "Would you rather
only eat soup or only eat salad?". You aim for a question that splits the group as close to
50/50 as you can. The group then votes A or B on each question and guesses how the vote will
fall. The writer scores for an even split, and voters score for a good guess. Any member can
host a game; there is no organizer group.

### The lobby card

`/gaminator fiftyfifty create` posts a card in the channel where you ran it, and the game is
played there. The card shows the host, the players in join order, and how many more are needed
to start (3 by default). It has four buttons:

- **Join** and **Leave** do the same as `/gaminator fiftyfifty join` and `leave`.
- **Rules** shows you the rules privately.
- **Start** works for the host or a game admin and plays 1 round. For more, run
  `/gaminator fiftyfifty start rounds:<1-5>`.

Once the game starts, Start disappears and the other buttons keep working. When the game ends,
the card shows it finished and the buttons are removed. The buttons survive a bot restart.

### How a round plays

A round has two steps: everyone writes, then everyone votes.

**Writing.** The bot posts one round message with a **Write** button. Press it and a private
form opens with two text boxes, Option A and Option B (up to 80 characters each). The form
also shows a topic idea, such as "road trips". It is only a nudge, so ignore it if you have
something better. Every player gets a different topic, and nobody else sees your question yet.
You can press Write again to change your options until writing closes. Writing closes when
every player has written or after 2 minutes, whichever comes first. A form you opened before a
bot restart can still be submitted after it.

**Voting and guessing.** The written questions are then asked one at a time, in a random
order. Each one is a message with the two options and an **A** and a **B** button. When you
press one, the bot answers you privately with a menu: "How many will pick A?". Pick a number,
counting your own vote. You can change your vote and your guess until the question closes.
Votes are hidden until the reveal. The writer's name is hidden too, and the writer sits out
both the vote and the guess on their own question. A question closes when everyone else has
voted and guessed, or after 60 seconds.

**The reveal.** The bot posts "The votes are in...", waits 3 seconds, and then posts the split,
who picked A and who picked B, the writer and their points, and who scored for their guess.

A player who wrote no question has nothing asked. They still vote and guess on the others.
After the last round the bot posts a recap with the winner and the best split of the game. In
a game of several rounds it also posts the points after each round.

### Scoring

The writer scores up to 100 points per question. The most even split the number of votes
allows scores 100, so an odd number of voters is no handicap.

| Votes (A to B) | Points |
|---|---|
| 2 to 2 | 100 |
| 3 to 2 | 100 |
| 3 to 1 | 50 |
| 3 to 0 | 0 |

Voters score for guessing how many players pick A:

| Guess | Points |
|---|---|
| The exact number of A votes | 50 |
| One off, when 4 or more votes were cast | 20 |
| Anything else | 0 |

The guess is checked against the votes that were cast when the question closed. A question
with fewer than 2 votes scores 0 for the writer and for every guess. A player who wrote no
question scores nothing as a writer, and can still score with guesses. The most points over
all rounds wins. A tie is a win for each tied player.

### Answering by DM

If the form doesn't work for you, answer by DM instead:

- Press **Answer by DM** on the round message, or just send the bot a DM. The bot DMs you your
  topic idea and how to reply.
- Your next DM to the bot is your question. Put the two options on two lines, or split them
  with `|`, like `only eat soup | only eat salad`. Send another to change it.
- A reply the bot can't read as two options gets a short note about the format, and nothing
  is saved.
- A leading "would you rather", a leading "or" on the second option, and a trailing "?" are
  dropped, because the bot adds them itself. The form does the same.
- The form and DMs both work until writing closes, and the last one you use wins. After you
  save a question with the form, your next DM gets the instructions back and is not read as a
  question.
- If your DMs are closed, the button tells you so. Use Write instead.
- If you are writing in two servers at once, the reply says which game it went to.

The bot answers DMs only from players who have a question to write. What you type in a DM is
not logged, and neither are votes or guesses.

### Joining and leaving

Players can join and leave at any time, from 3 up to 12 players.

- Join during a round and you are in from the next one.
- Leave during a round and your question still gets asked and scores for you, and the votes
  and guesses you made still count. Writing or a question closes at once if everyone still in
  has finished.
- If the host leaves, the longest-joined player becomes the host. A lobby with nobody left is
  cancelled.
- A running game ends with the recap when fewer than 3 players are left.
- Leave and rejoin during the same round and you are back in it.

A game also ends early if nobody writes a question in a round, or if 2 questions in a row get
no votes, so a forgotten game stops by itself.

### Leaderboard

`/gaminator fiftyfifty leaderboard` posts the all-time standings for the server: games won,
points, and games played. Points are writer points plus guess points. It shows the top 10,
plus your own row if you rank lower. The bot also posts the top 10 in the game channel when a
game ends. `/gaminator fiftyfifty leaderboard member:@someone` shows one member's numbers, how
many questions they wrote, their average points per question, their guess points, and their
three best splits.

- Only finished games count. A game ended early still counts for the questions that closed.
- A question that was open when the game ended is left out.
- You have played a game if you were in a round where at least one question was asked. A
  game ended while everyone was still writing counts for nobody.
- Games played before players wrote both options still count. Their questions show with the
  filled-in blank in bold, and they have no guess points.

### Topics and timing

The built-in topic ideas are in
[`src/gaminator/games/fiftyfifty/prompts.yaml`](src/gaminator/games/fiftyfifty/prompts.yaml).
Each is a short piece of text:

```yaml
topics:
  - food
  - road trips
  - this server
```

A topic can be up to 40 characters. Entries that are longer, empty or not text are skipped
with a warning in the log. To use your own topics, set `FIFTYFIFTY_PROMPTS_FILE` to a YAML
file in this format. A file with no usable `topics:` list, such as one in the older `prompts:`
format, is ignored with a warning and the built-in topics are used. A topic is not repeated
within a game unless the list runs out. `FIFTYFIFTY_WRITE_SECONDS` changes the 2 minute writing
time (minimum 30 seconds), and `FIFTYFIFTY_VOTE_SECONDS` changes the 60 second time to vote
and guess (minimum 10 seconds).

### Commands

| Command | Who | What it does |
|---|---|---|
| `/gaminator fiftyfifty create [name]` | anyone | Opens a game in this channel and posts the lobby card. You become the host. |
| `/gaminator fiftyfifty join` | anyone | Joins the game, before or during play. |
| `/gaminator fiftyfifty leave` | players | Leaves the game. |
| `/gaminator fiftyfifty start [rounds]` | host or admin | Starts the game with 1 round, or up to 5. |
| `/gaminator fiftyfifty end` | host or admin | Ends the game and posts the recap, or cancels a lobby. |
| `/gaminator fiftyfifty status` | anyone | Shows the players, the current step, and your own question, vote and guess, privately. |
| `/gaminator fiftyfifty rules` | anyone | Shows the rules privately. |
| `/gaminator fiftyfifty leaderboard [member]` | anyone | Posts the all-time standings. |

"Admin" means Manage Server or the role set with `/gaminator-admin admin-role`. Only one game
runs per server at a time, so a lobby nobody starts blocks new games until its host or an admin
ends it.

## 8. Spectrum

Each turn the bot shows a scale with two opposite ends, such as "Tiny hiccup ↔ Total disaster",
and gives one player, the clue giver, a secret target from 1 to 100 on it. The clue giver
writes a short clue, everyone else talks it over and guesses the number, and everyone scores by
how close the guesses land. Any member can host a game; there is no organizer group.

### The lobby card

`/gaminator spectrum create` posts a card in the channel where you ran it, and the game is
played there. The card shows the host, the players in join order, and how many more are needed
to start (3 by default). It has four buttons:

- **Join** and **Leave** do the same as `/gaminator spectrum join` and `leave`.
- **Rules** shows you the rules privately.
- **Start** works for the host or a game admin and plays 1 round. For more, run
  `/gaminator spectrum start rounds:<1-5>`.

Once the game starts, Start disappears and the other buttons keep working. When the game ends,
the card shows it finished and the buttons are removed. The buttons survive a bot restart.

### How a turn plays

A round is one turn per player, in a random order. Every turn has a new scale and a new target.

**The clue.** The bot posts the scale and pings the clue giver. Nobody else is pinged, on this
message or any other. The turn message has two buttons, and both only work for the clue giver:

- **Show my target** shows them the secret number privately. It shows the same number every
  time, also after a bot restart.
- **Write clue** opens a private form that shows the target again and takes the clue.

A clue can be up to 80 characters and can't contain digits. A clue with a digit is refused and
shown back to the clue giver, who presses Write clue again. Number words and hints like "almost
all the way right" are not checked, so that is up to the group's sense of fair play. A clue is
final once it is in. If no clue comes within 2 minutes, the turn is skipped and the bot says so.

**Guessing.** The bot posts the clue with a **Guess** button. Talk it over in the channel, then
press Guess and enter a whole number from 1 (fully the left end) to 100 (fully the right end).
Guesses stay hidden until the reveal, and you can press Guess again to change yours until the
turn closes. The message shows how many guesses are in. The turn closes when everyone has
guessed or after 2 minutes, whichever comes first. The clue giver sits the guess out.

**The reveal.** The bot posts "The guesses are in. The target is...", waits 3 seconds, and then
posts the target and every guess by name, in order along the scale, with the points for each
player.

A form you opened before a bot restart can still be submitted after it. After the last round
the bot posts a recap with the winner and the closest guess of the game. In a game of several
rounds it also posts the points after each round.

### Scoring

A guess scores `100 - 3 × distance`, where distance is how far the guess is from the target,
and never less than 0:

| Guess | Points |
|---|---|
| Exactly on the target | 100 |
| 10 away | 70 |
| 34 or more away | 0 |

The clue giver scores the average of the guessers' points, rounded to the nearest whole number,
so a clue that lands everyone close pays best. Guesses of 91, 55 and 0 points give the clue
giver 49. A turn nobody guesses on scores nothing for anyone, and so does a turn with no clue.

The most points at the end wins. Players who tie at the top each win, and nobody wins a game
where nobody scored. Scores are worked out from the stored guesses each time, so the recap and
the leaderboard always agree. Targets, clues and guesses are not written to the log.

### Joining and leaving

Players can join and leave at any time, from 3 up to 12 players.

- Join during a round and you guess from the next clue. You also get a turn to give a clue, at
  the end of that round.
- Leave and your clues and guesses so far still count. A clue giver who leaves after giving
  the clue still scores for it, and guessing carries on. A clue giver who leaves before giving
  the clue has their turn skipped, and the bot says so.
- Guessing closes at once if everyone still in has guessed.
- If the host leaves, the longest-joined player becomes the host. A lobby with nobody left is
  cancelled.
- A running game ends with the recap when fewer than 3 players are left.
- Leave and rejoin during the same round and you keep your turn. You never get a second one in
  the same round.

A game also ends early after 2 turns in a row where a clue got no guesses, or after a whole
round in which nobody gave a clue, so a forgotten game stops by itself. One player missing
their clue does not count against the game. If the host or an admin ends the game while a clue
is open for guesses, the bot reveals that turn before the recap.

### Leaderboard

`/gaminator spectrum leaderboard` posts the all-time standings for the server: games won,
points, and games played. It shows the top 10, plus your own row if you rank lower. The bot
also posts the top 10 in the game channel when a game ends.
`/gaminator spectrum leaderboard member:@someone` shows the same three numbers for one member.

- Only finished games count. A game ended early still counts for the turns that were revealed.
- You have played a game if you gave the clue on, or could guess on, at least one revealed
  turn. A game ended during its first clue counts for nobody.

### Scales and timing

The built-in deck of 80 scales is in
[`src/gaminator/games/spectrum/scales.yaml`](src/gaminator/games/spectrum/scales.yaml). Each
scale has a left and a right end:

```yaml
scales:
  - {left: "Tiny hiccup", right: "Total disaster"}
  - {left: "Snack", right: "Feast"}
```

1 is fully the left end and 100 is fully the right end. An end can be up to 24 characters.
Entries with a missing, empty or longer end, or with two ends that are the same, are skipped
with a warning in the log. To use your own scales, set `SPECTRUM_SCALES_FILE` to a YAML file in
this format. A file that can't be read, or has no usable scales, is ignored and the built-in
deck is used. A scale is not repeated within a game unless the deck runs out.
`SPECTRUM_CLUE_SECONDS` changes the 2 minutes to write a clue (minimum 30 seconds), and
`SPECTRUM_GUESS_SECONDS` changes the 2 minutes to guess (minimum 20 seconds).

### Commands

| Command | Who | What it does |
|---|---|---|
| `/gaminator spectrum create [name]` | anyone | Opens a game in this channel and posts the lobby card. You become the host. |
| `/gaminator spectrum join` | anyone | Joins the game, before or during play. |
| `/gaminator spectrum leave` | players | Leaves the game. |
| `/gaminator spectrum start [rounds]` | host or admin | Starts the game with 1 round, or up to 5. |
| `/gaminator spectrum end` | host or admin | Ends the game and posts the recap, or cancels a lobby. |
| `/gaminator spectrum status` | anyone | Shows the players, the current turn, and your own guess, privately. It never shows the target. |
| `/gaminator spectrum rules` | anyone | Shows the rules privately. |
| `/gaminator spectrum leaderboard [member]` | anyone | Posts the all-time standings. |

"Admin" means Manage Server or the role set with `/gaminator-admin admin-role`. Only one game
runs per server at a time, so a lobby nobody starts blocks new games until its host or an admin
ends it.

## 9. Headlines

Each round everyone privately answers their own harmless question, such as "What do you put on
your toast?". Each answer is then handed to another player, who sees only the answer and writes
a news headline that makes it sound as bad as possible. The bot reads the headlines out with
their quotes, and the group votes for the best one. Any member can host a game; there is no
organizer group.

### The lobby card

`/gaminator headlines create` posts a card in the channel where you ran it, and the game is
played there. The card shows the host, the players in join order, and how many more are needed
to start (3 by default). It has four buttons:

- **Join** and **Leave** do the same as `/gaminator headlines join` and `leave`.
- **Rules** shows you the rules privately.
- **Start** works for the host or a game admin and plays 2 rounds. For 1 to 5 rounds, run
  `/gaminator headlines start rounds:<1-5>`.

Once the game starts, Start disappears and the other buttons keep working. When the game ends,
the card shows it finished and the buttons are removed. The buttons survive a bot restart.

### How a round plays

Nobody is pinged, on any message of this game.

**Answer.** The bot posts the round with an **Answer** button. Press it to get your own
question in a private form, and answer it in up to 100 characters. Every player gets a
different question, and nobody else ever sees yours. You can press Answer again to change your
answer until the step closes. The message shows how many answers are in. Answering closes when
everyone has answered or after 90 seconds, whichever comes first.

**Write headline.** The bot posts a **Write headline** button. Press it to see the one quote
you have been handed, and write a headline for it in up to 120 characters. You never get your
own answer, and you don't see the question or who said it. You can change your headline until
the step closes, which is when every headline is in or after 2 minutes. When two or more
players answered, their answers are passed round among them, so each of them writes one
headline. A player with no quote for this round is told so privately when they press the
button.

**The reveal.** The bot posts one headline at a time, a few seconds apart, in a random order.
Each one comes with its quote and the name of the player who said it. Who wrote the headline
stays hidden until the result. Links in answers and headlines show no preview. A round with
only one headline shows it and skips the vote, and scores nothing.

**The vote.** The bot posts a menu with every headline. Pick your favorite. You can't vote for
a headline you wrote, but you can vote for one that quotes you. Votes stay hidden until the
result, and you can pick again to change yours until the vote closes. The message shows how
many votes are in. The vote closes when everyone who can vote has voted or after 60 seconds.

**The result.** The bot posts every headline with its votes, who wrote it, who was quoted and
the points for both. While rounds remain it adds the running totals and starts the next round.

A form you opened before a bot restart can still be submitted after it, and so can a vote on a
menu posted before it. A reveal cut short by a restart carries on with the headlines not shown
yet, about half a minute later. After the last round the bot posts a recap with the winner and
the top headline of the game.

### Scoring

Each vote for a headline gives 100 points to the player who wrote it and 25 points to the
player it quotes. A headline with 4 votes gives its writer 400 points and the player quoted
100. A headline with no votes scores nothing.

The most points at the end wins. Players who tie at the top each win, and nobody wins a game
where nobody scored. Scores are worked out from the stored votes each time, so the recap and
the leaderboard always agree. Questions, answers, headlines and votes are not written to the
log.

### Joining and leaving

Players can join and leave at any time, from 3 up to 12 players.

- Join during a round and you are in from the next round. The buttons and the menu of the
  current round tell you so privately.
- Leave and your answers, headlines and votes so far still count. Your answer can still be
  handed out and shown with your name, and it still scores for you.
- A quote that is still unwritten when headline writing closes is dropped: it is not shown and
  not voted on. A writer who leaves and comes back before the step closes can still write it.
- A step closes at once if everyone still in has done their part.
- If the host leaves, the longest-joined player becomes the host. A lobby with nobody left is
  cancelled.
- A running game ends with the recap when fewer than 3 players are left.

A game also ends early after a round in which nobody answered, no headline came in or nobody
voted, so a forgotten game stops by itself. One player who is away does not count against the
game: two active players are enough to keep it going. If the host or an admin ends the game
during a vote, the bot posts that round's result before the recap. A round ended while it was
being answered, written or read out scores nothing, and the recap says so when its headlines
had already been written.

### Leaderboard

`/gaminator headlines leaderboard` posts the all-time standings for the server: games won,
points, and games played. It shows the top 10, plus your own row if you rank lower. The bot
also posts the top 10 in the game channel when a game ends.
`/gaminator headlines leaderboard member:@someone` shows the same three numbers for one member.

- Only finished games count. A game ended early still counts for the rounds that were voted on.
- You have played a game if you were in at least one round that reached its vote result. A
  game ended during its first round counts for nobody.

### Questions and timing

The built-in deck of 100 questions is in
[`src/gaminator/games/headlines/questions.yaml`](src/gaminator/games/headlines/questions.yaml):

```yaml
questions:
  - "What do you do first thing in the morning?"
  - "What's always in your fridge?"
```

A question is plain text of up to 100 characters. Keep questions harmless: the fun comes from
the headline. Entries that are empty, longer or not text are skipped with a warning in the log.
To use your own questions, set `HEADLINES_QUESTIONS_FILE` to a YAML file in this format. A file
that can't be read, or has no usable questions, is ignored and the built-in deck is used. A
question is not repeated within a game unless the deck runs out.
`HEADLINES_ANSWER_SECONDS` changes the 90 seconds to answer (minimum 30 seconds),
`HEADLINES_WRITE_SECONDS` changes the 2 minutes to write a headline (minimum 30 seconds), and
`HEADLINES_VOTE_SECONDS` changes the 60 seconds to vote (minimum 20 seconds).

### Commands

| Command | Who | What it does |
|---|---|---|
| `/gaminator headlines create [name]` | anyone | Opens a game in this channel and posts the lobby card. You become the host. |
| `/gaminator headlines join` | anyone | Joins the game, before or during play. |
| `/gaminator headlines leave` | players | Leaves the game. |
| `/gaminator headlines start [rounds]` | host or admin | Starts the game with 2 rounds, or 1 to 5. |
| `/gaminator headlines end` | host or admin | Ends the game and posts the recap, or cancels a lobby. |
| `/gaminator headlines status` | anyone | Shows the players, the current step and what you still owe, privately. It never shows a question, an answer, a headline or a vote. |
| `/gaminator headlines rules` | anyone | Shows the rules privately. |
| `/gaminator headlines leaderboard [member]` | anyone | Posts the all-time standings. |

"Admin" means Manage Server or the role set with `/gaminator-admin admin-role`. Only one game
runs per server at a time, so a lobby nobody starts blocks new games until its host or an admin
ends it.

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
imposters need three). Most Likely To starts with two and still ends when fewer than two are
left. Fifty-Fifty starts with two and ends when fewer than two are left, but each question then
has one voter and scores 0. Spectrum starts with two and ends when fewer than two are left, and
each turn then has one guesser. Headlines starts with two and ends when fewer than two are
left, and each player then writes the headline for the other's answer. Remove the line for real
play.

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
    commands.py                     the /gaminator and /gaminator-admin groups
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
    mostlikely/
      __init__.py, schema.sql, repo.py, context.py   same shape as imposter
      commands/                           the /gaminator mostlikely subgroup
      prompts.yaml, services/prompts.py   the prompt deck and loader
      services/rules.py                   tallies, recap, standings, vote and host checks
      services/game.py                    text, the lobby card, prompts, joins, leaves, ending
      vote.py, lobby.py                   the persistent vote dropdown and lobby buttons
      events.py                           the sweep that closes prompts at their deadline
    fiftyfifty/
      __init__.py, schema.sql, repo.py, context.py   same shape as mostlikely
      commands/                           the /gaminator fiftyfifty subgroup
      prompts.yaml, services/prompts.py   the topic deck and loader
      services/rules.py                   scoring, standings, write, vote and DM checks
      services/game.py                    text, the lobby card, rounds, questions, ending
      write.py                            the Write button and the fill-in form
      dm.py                               the Answer by DM button and DM replies
      vote.py, lobby.py                   the A and B vote buttons and the lobby buttons
      events.py                           the sweep, and the form and DM listeners
    spectrum/
      __init__.py, schema.sql, repo.py, context.py   same shape as fiftyfifty
      commands/                           the /gaminator spectrum subgroup
      scales.yaml, services/scales.py     the scale deck and loader
      services/rules.py                   clue and guess checks, scoring, standings
      services/game.py                    text, the lobby card, turns, reveals, ending
      turn.py                             the Show my target and Write clue buttons, both
                                          pop-ups and their submit handler
      guess.py                            the Guess button
      lobby.py                            the lobby buttons
      events.py                           the sweep, and the pop-up listener
    headlines/
      __init__.py, schema.sql, repo.py, context.py   same shape as spectrum
      commands/                           the /gaminator headlines subgroup
      questions.yaml, services/questions.py   the question deck and loader
      services/rules.py                   handing out quotes, step checks, scoring, standings
      services/game.py                    text, the lobby card, rounds, the reveal, ending
      forms.py                            the Answer and Write headline buttons, both pop-ups
                                          and their submit handler
      vote.py                             the vote menu
      lobby.py                            the lobby buttons
      events.py                           the sweep, and the pop-up listener
```

### Adding a game

1. Create `src/gaminator/games/<key>/` with a `schema.sql` whose tables are all prefixed
   `<key>_` and include a unique index on `guild_id` for unfinished games, so the game runs once
   per server.
2. Give it a repo (subclass `core.repo.BaseRepo`) with at least `get_current_game(guild_id)`
   returning rows that expose `name` and `status`.
3. Define its slash commands in an extension module with `setup`/`teardown`. There are two
   ways to place them:
   - Its own top-level groups, added to and removed from `bot.tree`: an
     `app_commands.Group(name="<key>")` for players and a separate `<key>-admin` group with
     `default_permissions` set to Manage Server for organizers. Still check `is_game_admin`
     inside the organizer commands.
   - A subgroup of the public `/gaminator` group, added with `gaminator.add_command(...)` in
     `setup` and removed in `teardown` (see `games/mostlikely/commands/__init__.py`). Discord
     allows nothing to nest below that subgroup, and it cannot be hidden by permission, so a
     game hosted by members checks host or admin inside the command. Import
     `core.commands` only from the command modules, never from the game's `__init__.py`.

   A pop-up form (modal) must be the first response to a button press, so that button cannot
   defer. Handle the submit in an `on_interaction` listener keyed by a fixed custom id, so a
   form opened before a restart can still be submitted (see `games/fiftyfifty/write.py`).
4. Export a `SPEC = GameSpec(...)` from the package and add it to `GAMES` in `games/__init__.py`.
   `emoji`, `summary`, `start_hint` and `players` are shown for the game on `/gaminator games`;
   pass `listed=False` to keep a game off that card.
   `GameSpec.migrate` is an optional hook for one-off table upgrades, run before the schema.
   Errors raised as `core.util.GameError` are reported to the user by the shared handler.

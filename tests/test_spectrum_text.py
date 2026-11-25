"""Spectrum text builders, the lobby card and its buttons."""

from gaminator.games.spectrum.lobby import LobbyButton, lobby_view
from gaminator.games.spectrum.services import game as flow
from gaminator.games.spectrum.services.game import GAP
from gaminator.games.spectrum.services.rules import NO_DIGITS, ScoredTurn, Standing

SCALE = "Tiny hiccup ↔ Total disaster"
WHEN = "2030-01-01T00:00:00+00:00"
STAMP = "<t:1893456000:R>"
GAME = {"name": "Friday", "status": "active", "max_rounds": 2, "started_at": WHEN}
OPEN = {"name": "Friday", "status": "open", "max_rounds": 1, "started_at": None}
NASTY = "*_~`|>#[x](y)" + "_" * 19


def turn(**changes):
    row = {
        "id": 5,
        "turn_id": 5,
        "position": 2,
        "turn_count": 4,
        "round_number": 1,
        "left_end": "Tiny hiccup",
        "right_end": "Total disaster",
        "target": 73,
        "clue": "rain at a picnic",
        "clue_deadline_at": WHEN,
        "guess_deadline_at": WHEN,
    }
    row.update(changes)
    return row


def scored(giver, target, guesses, left="Tiny hiccup", right="Total disaster"):
    return ScoredTurn(giver, left, right, target, "a clue", WHEN, tuple(guesses))


def test_rules_text():
    text = flow.rules_text(OPEN, 120, 90, 3)
    assert text.startswith("## 🌈 How Spectrum works\n• ")
    assert "secret number from 1 to 100" in text and "no digits" in text
    assert "after 90 seconds" in text and "within 2 minutes" in text
    assert "`100 - 3 × distance`" in text and "34 or more away" in text
    assert "the guessers' average" in text
    assert "A game is 1 round unless the host picks more" in text
    assert "`/gaminator spectrum start rounds:`" in text
    assert "guess from the next clue" in text and "end of the round" in text
    assert "2 turns in a row with no guesses" in text and "fewer than 3 players" in text
    running = flow.rules_text(GAME, 120, 120, 2)
    assert "This game runs 2 rounds." in running and "fewer than 2 players" in running
    assert "—" not in text


def test_turn_text():
    text = flow.turn_text(GAME, turn(), 11)
    assert text.split("\n") == [
        "_ _",
        "## 🌈 Turn 2 of 4 (round 1 of 2)",
        f"### {SCALE}",
        (
            "<@11>, you give the clue. Press **Show my target** to see the secret number, "
            "then **Write clue**."
        ),
        f"Clue due {STAMP}.",
    ]
    assert "73" not in text


def test_turn_done_text():
    head = ["_ _", "## 🌈 Turn 2 of 4 (round 1 of 2)", f"### {SCALE}"]
    cases = {
        "clue": "**Ann**'s clue: **rain at a picnic**",
        "no_clue": "No clue in time, so this turn is skipped.",
        "left": "**Ann** left, so this turn is skipped.",
        "failed": "I couldn't post the clue, so this turn is skipped.",
        "ended": "The game ended.",
    }
    for why, line in cases.items():
        text = flow.turn_done_text(GAME, turn(), "Ann", why)
        assert text.split("\n") == head + [line]
        assert "<@" not in text and "73" not in text
    assert "rain" not in flow.turn_done_text(GAME, turn(), "Ann", "failed")


def test_target_text():
    text = flow.target_text(turn())
    assert text.split("\n") == [
        "🎯 Your target is **73**.",
        f"### {SCALE}",
        "1 is fully Tiny hiccup, 100 is fully Total disaster.",
        (
            "Press **Write clue** on the turn message when you're ready. No digits allowed. "
            f"Clue due {STAMP}."
        ),
    ]
    escaped = flow.target_text(turn(left_end="*soft*", right_end="snake_case"))
    assert "### \\*soft\\* ↔ snake\\_case" in escaped
    assert "1 is fully \\*soft\\*, 100 is fully snake\\_case." in escaped


def test_guess_text():
    text = flow.guess_text(GAME, turn(), "Ann", 2, 4)
    assert text.split("\n") == [
        "_ _",
        f"## 🌈 {SCALE}",
        "**Ann**'s clue: **rain at a picnic**",
        (
            "Talk it over, then press **Guess** and enter a number from 1 (fully Tiny hiccup) to "
            "100 (fully Total disaster). Guesses stay hidden until the reveal, and you can change "
            "yours until the turn closes."
        ),
        f"2/4 guesses in. Closes {STAMP}.",
    ]
    assert "73" not in text
    closed = flow.guess_closed_text(GAME, turn(), "Ann")
    assert closed.split("\n") == text.split("\n")[:3] + ["Guessing is closed."]
    escaped = flow.guess_text(GAME, turn(clue="so *very* _wet_"), "A*n", 0, 1)
    assert "**A\\*n**'s clue: **so \\*very\\* \\_wet\\_**" in escaped
    assert flow.drumroll_text() == GAP + "🥁 The guesses are in. The target is..."


def test_reveal_text():
    names = {12: "Bo", 13: "Dee", 14: "Cy"}
    text = flow.reveal_text(turn(), "Ann", [(12, 70), (13, 88), (14, 12)], names)
    assert text.split("\n") == [
        "_ _",
        "## 🎯 The target was 73",
        f"{SCALE}. Clue from **Ann**: **rain at a picnic**",
        "`  1` Tiny hiccup",
        "` 12` **Cy**: 0",
        "` 70` **Bo**: 91",
        "` 73` 🎯 target",
        "` 88` **Dee**: 55",
        "`100` Total disaster",
        "**Ann** scores **49** for the clue, the guessers' average.",
    ]
    same = flow.reveal_text(turn(), "Ann", [(13, 73), (12, 73), (14, 100), (15, 1)], names)
    assert same.split("\n")[3:10] == [
        "`  1` Tiny hiccup",
        "`  1` **?**: 0",
        "` 73` 🎯 target",
        "` 73` **Bo**: 100",
        "` 73` **Dee**: 100",
        "`100` **Cy**: 19",
        "`100` Total disaster",
    ]
    empty = flow.reveal_text(turn(), "Ann", [], {})
    assert empty.split("\n")[3:] == [
        "`  1` Tiny hiccup",
        "` 73` 🎯 target",
        "`100` Total disaster",
        "Nobody guessed, so nobody scores.",
    ]
    escaped = flow.reveal_text(turn(clue="so *wet*"), "A_n", [(12, 70)], {12: "B*o"})
    assert "Clue from **A\\_n**: **so \\*wet\\***" in escaped and "**B\\*o**: 91" in escaped


def test_skip_lines():
    assert flow.no_clue_line("Ann") == (
        GAP + "⏭️ No clue from **Ann** in time, so this turn is skipped."
    )
    assert flow.skipped_line("Ann", "left") == GAP + "⏭️ **Ann** left, so their turn is skipped."
    assert flow.skipped_line("A*n", "alone") == (
        GAP + "⏭️ Nobody else is here to guess, so **A\\*n**'s turn is skipped."
    )


def test_round_and_recap_text():
    names = {11: "Ann", 12: "Bo", 13: "Cy"}
    text = flow.round_text(
        {**GAME, "max_rounds": 3}, 1, [(11, 140), (12, 91), (13, 0)], [(11, 140), (12, 91)], names
    )
    assert text.split("\n") == [
        "_ _",
        "🏁 **Round 1 of 3** is done.",
        "This round: Ann 140, Bo 91, Cy 0",
        "Totals: Ann 140, Bo 91",
        "Round 2 starts now.",
    ]
    close = (scored(11, 73, [(12, 72)]), 12, 72)
    recap = flow.recap_text(GAME, [(11, 240), (12, 180), (13, 40)], names, 1, close)
    assert recap.split("\n")[:5] == [
        "_ _",
        "🏁 Spectrum game **Friday** is over after 1 round.",
        "🏆 **Ann** wins with 240 points!",
        "Also: Bo 180, Cy 40",
        f"🎯 Closest guess: **Bo** said 72 with the target at 73 on {SCALE}.",
    ]
    tie = flow.recap_text(GAME, [(11, 90), (12, 90), (13, 1)], names, 2, None, "ended by Ann")
    assert "is over after 2 rounds (ended by Ann)." in tie
    assert "🏆 **Ann** and **Bo** tie with 90 points each!" in tie
    assert "Also: Cy 1" in tie and "Closest guess" not in tie
    nobody = flow.recap_text(GAME, [(11, 0), (12, 0)], names, 0, None)
    assert nobody.split("\n")[:3] == [
        "_ _",
        "🏁 Spectrum game **Friday** is over after 0 rounds.",
        "Nobody scored.",
    ]
    assert "wins with 1 point!" in flow.recap_text(GAME, [(11, 1)], names, 1, None)
    hint = "All-time standings: `/gaminator spectrum leaderboard`"
    for text in (recap, tie, nobody):
        assert text.split("\n")[-1] == hint and text.count(hint) == 1


def test_reply_texts():
    assert flow.clue_saved_text() == "Your clue is up."
    assert flow.clue_failed_text() == "I couldn't post your clue, so this turn is skipped."
    assert flow.guess_saved_text(42) == (
        "Your guess is **42**. You can change it until the turn closes."
    )
    refused = flow.refused_clue_text(NO_DIGITS, "about *7*")
    assert refused.split("\n") == [
        NO_DIGITS,
        "You wrote: about \\*7\\*",
        "Press **Write clue** to try again.",
    ]
    assert flow.refused_clue_text("Empty.", "  ").split("\n") == [
        "Empty.",
        "Press **Write clue** to try again.",
    ]
    assert flow.late_join_text() == (
        "You're in. You can guess from the next clue, and your turn to give a clue comes at "
        "the end of this round."
    )


def test_join_leave_host_lines():
    assert flow.join_line("A*n", OPEN, 2, 3) == (
        GAP + "🙋 **A\\*n** joined Spectrum game **Friday**. "
        "2 of at least 3 players signed up, 1 more needed to start."
    )
    assert flow.join_line("Ann", GAME, 4, 3) == GAP + "🙋 **Ann** joined."
    assert flow.leave_line("Ann", OPEN, 3, 3).startswith(GAP + "🚪 **Ann** left Spectrum game")
    assert flow.leave_line("Ann", GAME, 3, 3) == GAP + "🚪 **Ann** left. 3 players remain."
    assert flow.leave_line("Ann", GAME, 1, 3) == GAP + "🚪 **Ann** left. 1 player remains."
    assert flow.host_line("Ann") == GAP + "👑 **Ann** is now the host."
    assert flow.cancelled_text(OPEN, "ended by Ann") == (
        GAP + "🚫 Spectrum game **Friday** was cancelled (ended by Ann)."
    )


def test_channel_messages_start_with_gap_and_heading():
    names = {11: "Ann", 12: "Bo"}
    built = {
        "turn": flow.turn_text(GAME, turn(), 11),
        "turn_done": flow.turn_done_text(GAME, turn(), "Ann", "clue"),
        "guess": flow.guess_text(GAME, turn(), "Ann", 0, 2),
        "guess_closed": flow.guess_closed_text(GAME, turn(), "Ann"),
        "drumroll": flow.drumroll_text(),
        "reveal": flow.reveal_text(turn(), "Ann", [(12, 70)], names),
        "no_clue": flow.no_clue_line("Ann"),
        "skipped": flow.skipped_line("Ann", "left"),
        "round": flow.round_text(GAME, 1, [(11, 5)], [(11, 5)], names),
        "recap": flow.recap_text(GAME, [(11, 5)], names, 1, None),
        "leaderboard": flow.leaderboard_text([(1, Standing(11, 1, 5, 1))], names),
        "join": flow.join_line("Ann", GAME, 3, 3),
        "leave": flow.leave_line("Ann", GAME, 3, 3),
        "host": flow.host_line("Ann"),
        "cancelled": flow.cancelled_text(OPEN, "everyone left"),
    }
    assert GAP == "_ _\n"
    for kind, text in built.items():
        assert text.startswith(GAP), kind
        assert not text[len(GAP) :].startswith(("\n", " ")), kind
        assert text.count("_ _") == 1, kind
    for kind in ("turn", "turn_done", "guess", "guess_closed", "reveal", "leaderboard"):
        assert built[kind].split("\n")[1].startswith("## "), kind
    for kind in ("turn", "turn_done"):
        assert built[kind].split("\n")[2] == f"### {SCALE}", kind
    assert built["guess"].split("\n")[1] == f"## 🌈 {SCALE}"
    assert "### " not in built["reveal"]


def test_messages_fit_2000():
    users = list(range(11, 23))
    names = {uid: NASTY for uid in users}
    assert all(len(name) == 32 for name in names.values())
    long_turn = turn(clue="*" * 80, left_end="_" * 24, right_end="*" * 24, target=100)
    guesses = [(uid, 100) for uid in users[1:]]
    texts = [
        flow.turn_text(GAME, long_turn, 10**19),
        flow.turn_done_text(GAME, long_turn, NASTY, "clue"),
        flow.guess_text(GAME, long_turn, NASTY, 11, 11),
        flow.reveal_text(long_turn, NASTY, guesses, names),
        flow.round_text(GAME, 1, [(u, 1100) for u in users], [(u, 5500) for u in users], names),
        flow.recap_text(
            GAME,
            [(u, 5500) for u in users],
            names,
            5,
            (scored(11, 100, guesses, "_" * 24, "*" * 24), 12, 100),
            "2 turns in a row got no guesses",
        ),
        "\n".join(
            flow.status_lines(GAME, {"closed_at": None}, long_turn, NASTY, (11, 11), "guesser", 100)
        ),
    ]
    for text in texts:
        assert len(text) <= 2000, text[:60]
    reveal = texts[3]
    assert reveal.count("\\*") > 20 and "**" + "\\*" * 80 + "**" in reveal


def test_lobby_embed():
    names = [f"U{n}" for n in range(11, 23)]
    embed = flow.lobby_embed(OPEN, names[:2], "U11", 3, 120, 120)
    assert embed.title == "🌈 Friday: sign-ups open"
    assert "Host: **U11**" in embed.description
    assert "2 players. 1 more needed to start." in embed.description
    assert embed.description.endswith("1. U11\n2. U12")
    (field,) = embed.fields
    assert field.name == "How it works" and field.value == flow.how_it_works(OPEN, 120, 120, 3)
    full = flow.lobby_embed(OPEN, names, "U11", 3)
    assert "12 players. Enough to start!" in full.description
    running = flow.lobby_embed(GAME, names[:3], "U*11", 3)
    assert running.title == "🌈 Friday: in progress" and "Host: **U\\*11**" in running.description
    over = flow.lobby_embed({**GAME, "status": "finished"}, names[:3], "U11", 3)
    assert over.title == "🏁 Friday: finished" and "3 players at the end." in over.description
    never = flow.lobby_embed({**OPEN, "status": "finished"}, [], "U11", 3)
    assert never.title == "🏁 Friday: cancelled"
    for game in (OPEN, GAME, {**GAME, "max_rounds": 5}):
        for clue_s, guess_s in ((120, 120), (45, 25), (3600, 3600)):
            assert len(flow.how_it_works(game, clue_s, guess_s, 12)) < 1024


def test_lobby_view():
    view = lobby_view({"id": 7, "status": "open"})
    assert [c.item.label for c in view.children] == ["Join", "Leave", "Rules", "Start"]
    assert [c.item.custom_id for c in view.children] == [
        "spectrum:join:7",
        "spectrum:leave:7",
        "spectrum:rules:7",
        "spectrum:start:7",
    ]
    assert all(isinstance(c, LobbyButton) for c in view.children) and view.timeout is None
    running = lobby_view({"id": 7, "status": "active"})
    assert [c.item.label for c in running.children] == ["Join", "Leave", "Rules"]
    assert lobby_view({"id": 7, "status": "finished"}) is None


def test_leaderboard_text():
    names = {11: "Ann", 12: "B*o", 13: "Cy"}
    rows = [
        (1, Standing(11, 2, 640, 4)),
        (2, Standing(12, 1, 1, 1)),
        (14, Standing(13, 0, 0, 2)),
    ]
    assert flow.standing_text(rows[0][1]) == "2 games won, 640 points, 4 played"
    assert flow.standing_text(rows[1][1]) == "1 game won, 1 point, 1 played"
    assert flow.leaderboard_text(rows, names).split("\n") == [
        "_ _",
        "## 🏆 Spectrum: all-time standings",
        "`1.` **Ann**: 2 games won, 640 points, 4 played",
        "`2.` **B\\*o**: 1 game won, 1 point, 1 played",
        "",
        "`14.` **Cy**: 0 games won, 0 points, 2 played",
    ]
    assert flow.leaderboard_text([], {}) == (
        "No finished Spectrum games yet. Start one with `/gaminator spectrum create`."
    )
    full = [(n, Standing(n, 9, 99999, 99)) for n in range(1, 11)] + [(250, Standing(11, 0, 0, 1))]
    assert len(flow.leaderboard_text(full, dict.fromkeys(range(1, 12), NASTY))) <= 2000


def test_member_text():
    assert flow.member_text("A*n", Standing(11, 2, 640, 4)) == (
        "**A\\*n**: 2 games won, 640 points, 4 played"
    )
    assert flow.member_text("Ann", None) == "**Ann** hasn't played a finished game yet."

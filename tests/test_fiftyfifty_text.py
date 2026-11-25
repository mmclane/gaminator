"""Fifty-Fifty text builders."""

from gaminator.games.fiftyfifty.lobby import lobby_view
from gaminator.games.fiftyfifty.services.game import (
    dm_closed_text,
    dm_failed_text,
    dm_question_text,
    dm_saved_text,
    dm_sent_text,
    guess_line,
    guessed_text,
    host_line,
    join_line,
    leaderboard_text,
    leave_line,
    lobby_embed,
    member_text,
    place_text,
    recap_text,
    reveal_text,
    round_text,
    rules_text,
    saved_text,
    status_lines,
    title,
    vote_closed_text,
    vote_text,
    voted_text,
    write_closed_text,
    write_text,
)
from gaminator.games.fiftyfifty.services.rules import ScoredQuestion, Standing

OPEN = {"id": 7, "name": "Friday", "status": "open", "max_rounds": 1, "started_at": None}
ACTIVE = {"id": 7, "name": "Friday", "status": "active", "max_rounds": 3, "started_at": "x"}
FINISHED = {**ACTIVE, "status": "finished"}
CANCELLED = {**OPEN, "status": "finished"}
ROUND = {
    "id": 4,
    "number": 1,
    "write_deadline_at": "2026-01-01T00:02:00+00:00",
    "write_closed_at": None,
    "closed_at": None,
}
QUESTION = {
    "id": 9,
    "position": 2,
    "round_number": 1,
    "option_a": "eat cold *soup* every day",
    "option_b": "never eat pizza again",
    "answer": None,
    "deadline_at": "2026-01-01T00:02:00+00:00",
}
OLD_QUESTION = {**QUESTION, "option_a": "eat ____ every day", "answer": "cold *soup*"}
FILLED = "Would you rather eat cold \\*soup\\* every day or never eat pizza again?"
OLD_FILLED = "Would you rather eat **cold \\*soup\\*** every day or never eat pizza again?"
SOUP = "Would you rather eat soup every day or never eat pizza again?"
OLD_SOUP = "Would you rather eat **soup** every day or never eat pizza again?"
NAMES = {1: "Ann", 2: "Bo", 3: "Cy"}
CLOSED_AT = "2026-01-01T00:02:00+00:00"
HINT = "All-time standings: `/gaminator fiftyfifty leaderboard`"


def test_title():
    assert title(OPEN) == "Fifty-Fifty game **Friday**"


def test_rules_text():
    text = rules_text(ACTIVE, 120, 60, 3)
    assert text.startswith("## ⚖️ How Fifty-Fifty works\n• ")
    assert "everyone writes a would-you-rather, both options" in text
    assert "50/50" in text and "topic idea you can ignore" in text
    assert "everyone has written or after 2 minutes" in text
    assert "everyone has voted and guessed or after 1 minute." in text
    assert "but your own" in text and "guess how many will pick A" in text
    assert "Votes stay hidden until the reveal." in text and "secret" not in text
    assert "The writer scores up to 100" in text
    assert "Voters score 50 for guessing the exact A count" in text
    assert "20 for one off when 4 or more voted" in text
    assert "This game runs 3 rounds." in text
    assert "`/gaminator fiftyfifty join` and `leave`" in text
    assert "you're in from the next one" in text
    assert "nobody writes in a round" in text and "blank" not in text
    assert "2 questions in a row get no votes" in text
    assert "fewer than 3 players are left" in text
    lobby = rules_text(OPEN, 45, 30, 2)
    assert "A game is 1 round unless the host picks more" in lobby
    assert "after 45 seconds" in lobby and "after 30 seconds" in lobby
    assert "fewer than 2 players are left" in lobby
    assert "This game runs 1 round." in rules_text({**ACTIVE, "max_rounds": 1}, 120, 60, 3)


def test_write_text():
    text = write_text(ACTIVE, ROUND, 2, 5)
    gap, line, progress = text.split("\n")
    head = f"{gap}\n{line}"
    assert head == (
        "_ _\n✍️ **Round 1 of 3**: press **Write** to write your own would-you-rather, both "
        "options. Aim for a question this group splits right down the middle."
    )
    assert progress == "2/5 written. Closes <t:1767225720:R>."
    closed = write_closed_text(ACTIVE, ROUND, 4)
    assert closed == f"{head}\nWriting is closed: 4 questions to vote on."
    assert write_closed_text(ACTIVE, ROUND, 1).endswith("Writing is closed: 1 question to vote on.")
    assert write_closed_text(ACTIVE, ROUND, 0).endswith(
        "Writing is closed: nobody wrote a question."
    )
    assert write_closed_text(ACTIVE, ROUND, None).endswith("\nWriting is closed.")


def test_vote_text():
    text = vote_text(ACTIVE, QUESTION, 4, 3, 4, 2)
    assert text.split("\n") == [
        "_ _",
        "🗳️ **Question 2 of 4** (round 1)",
        "## Would you rather",
        "> 🅰️ eat cold \\*soup\\* every day",
        "> 🅱️ never eat pizza again",
        "",
        (
            "Pick A or B, then guess how many will pick A. Votes are hidden until the reveal, and "
            "you can change both until it closes."
        ),
        "3/4 voted, 2/4 guessed. Closes <t:1767225720:R>.",
    ]
    assert "secret" not in text
    assert "0/2 voted, 0/2 guessed." in vote_text(ACTIVE, QUESTION, 1, 0, 2)
    for name in NAMES.values():
        assert name not in text
    closed = vote_closed_text(ACTIVE, QUESTION, 4)
    assert closed.split("\n") == text.split("\n")[:6] + ["Voting is closed."]
    sneaky = {**QUESTION, "option_a": "# shout", "option_b": "> quote **bold**"}
    lines = vote_text(ACTIVE, sneaky, 1, 0, 2).split("\n")
    assert lines[3] == "> 🅰️ \\# shout" and lines[4] == "> 🅱️ \\> quote \\*\\*bold\\*\\*"


def test_old_question_still_renders():
    old = vote_text(ACTIVE, OLD_QUESTION, 4, 3, 4).split("\n")
    assert old[3] == "> 🅰️ eat **cold \\*soup\\*** every day"
    assert old[4] == "> 🅱️ never eat pizza again"
    whole = {**QUESTION, "option_a": "have a theme song", "option_b": "____", "answer": "nap"}
    assert "🅱️ **nap**" in vote_text(ACTIVE, whole, 1, 0, 2)
    assert reveal_text(OLD_QUESTION, ["Bo"], ["Cy"], "Ann").split("\n")[0] == (
        f"📊 **Question 2**: {OLD_FILLED}"
    )
    best = ScoredQuestion(1, "eat ____ every day", "never eat pizza again", "soup", 2, 2, "")
    recap = recap_text(ACTIVE, [(1, 100)], NAMES, 1, best)
    assert f"⚖️ Best split: {OLD_SOUP} (2 to 2, by Ann)" in recap
    assert f"• 100: {OLD_SOUP} (2 to 2)" in member_text("Ann", Standing(1, 1, 100, 1, 1), [best])


def test_vote_and_guess_replies():
    assert voted_text("a", None) == (
        "You picked **A**. Now guess how many players will pick A, counting yourself."
    )
    assert voted_text("b", 3) == (
        "You picked **B**. Your guess stands at **3** for A. "
        "You can change both until the question closes."
    )
    assert voted_text("a", 0).startswith("You picked **A**. Your guess stands at **0** for A.")
    assert guessed_text(2) == (
        "You guessed **2** for A. You can change it until the question closes."
    )


def test_reveal_text():
    odd = reveal_text(QUESTION, ["Bo", "Cy", "Di"], ["Ed", "Flo"], "Ann", [("Cy", 50), ("Ed", 20)])
    assert odd.split("\n") == [
        f"📊 **Question 2**: {FILLED}",
        "**A 3, B 2**: as even as 5 votes can be.",
        "🅰️ **Bo**, **Cy** and **Di**",
        "🅱️ **Ed** and **Flo**",
        "✍️ Written by **Ann**: **100** points.",
        "🎯 Guessed the split: **Cy** +50, **Ed** +20",
    ]
    even = reveal_text(QUESTION, ["Bo", "Cy"], ["Di", "Ed"], "Ann")
    assert "**A 2, B 2**: a 50/50 split." in even and "**100** points." in even
    assert even.endswith("\n🎯 Nobody guessed the split.")
    lopsided = reveal_text(QUESTION, ["Bo"], ["Cy", "Di", "Ed"], "Ann")
    assert "**A 1, B 3**: a 75/25 split." in lopsided and "**50** points." in lopsided
    unanimous = reveal_text(QUESTION, [], ["Bo", "Cy", "Di"], "Ann", [("Bo", 50)])
    assert "**A 0, B 3**: a 100/0 split." in unanimous and "**0** points." in unanimous
    assert "🅰️ nobody\n🅱️ **Bo**, **Cy** and **Di**" in unanimous
    assert unanimous.endswith("🎯 Guessed the split: **Bo** +50")
    none = reveal_text(QUESTION, [], [], "Ann")
    assert none.split("\n")[1:] == ["Nobody voted, so it scores 0.", "✍️ Written by **Ann**."]
    one = reveal_text(QUESTION, ["Bo"], [], "Ann")
    assert one.split("\n")[1:] == [
        "Only 1 vote, so it scores 0. A question needs 2 votes to score.",
        "🅰️ **Bo**",
        "🅱️ nobody",
        "✍️ Written by **Ann**.",
    ]
    escaped = reveal_text(QUESTION, ["B_b"], ["C*y"], "A_n", [("B_b", 50)])
    assert "**B_b**" not in escaped and "🅰️ **B\\_b**" in escaped
    assert "🅱️ **C\\*y**" in escaped and "Written by **A\\_n**" in escaped
    assert "**B\\_b** +50" in escaped


def test_guess_line():
    assert guess_line([]) == "🎯 Nobody guessed the split."
    assert guess_line([("Ann", 50)]) == "🎯 Guessed the split: **Ann** +50"
    scorers = [("Ann", 50), ("Bo", 50), ("Cy", 20)]
    assert guess_line(scorers, names=False) == "🎯 Guessed the split: 2 players +50, 1 player +20"
    assert guess_line([], names=False) == "🎯 Nobody guessed the split."


def test_reveal_fits_2000_with_long_names_and_options():
    name = "*_" * 16
    question = {**QUESTION, "option_a": "*" * 80, "option_b": "_" * 80}
    voters = [name] * 11
    scorers = [(name, 50)] * 5 + [(name, 20)] * 6
    text = reveal_text(question, voters[:6], voters[6:], name, scorers)
    assert len(text) <= 2000
    assert text.endswith("🎯 Guessed the split: 5 players +50, 6 players +20")
    assert len(reveal_text(question, voters, [], name, [(name, 50)] * 11)) <= 2000
    short = reveal_text(question, ["Bo"] * 6, ["Cy"] * 5, "Ann", [("Bo", 50)] * 11)
    assert "**Bo** +50" in short


def scored(writer, a, b, closed_at="t1"):
    return ScoredQuestion(
        writer, "eat soup every day", "never eat pizza again", None, a, b, closed_at
    )


def test_round_and_recap_text():
    text = round_text(
        ACTIVE, 1, [(1, 100), (2, 50), (3, 0)], [(1, 100), (2, 50), (3, 0)], NAMES, {3}
    )
    assert text.split("\n") == [
        "🏁 **Round 1 of 3** is done.",
        "This round: Ann 100, Bo 50, Cy 0 (no question)",
        "Totals: Ann 100, Bo 50, Cy 0",
        "Round 2 starts now.",
    ]
    later = round_text(ACTIVE, 2, [(2, 100), (1, 0)], [(2, 150), (1, 100)], NAMES, set())
    assert "This round: Bo 100, Ann 0\n" in later and "Totals: Bo 150, Ann 100" in later
    assert later.endswith("Round 3 starts now.")

    best = scored(1, 2, 2)
    recap = recap_text(ACTIVE, [(1, 167), (2, 100), (3, 0)], NAMES, 2, best)
    assert recap.split("\n") == [
        "🏁 Fifty-Fifty game **Friday** is over after 2 rounds.",
        "🏆 **Ann** wins with 167 points!",
        "Also: Bo 100, Cy 0",
        f"⚖️ Best split: {SOUP} (2 to 2, by Ann)",
        HINT,
    ]
    tied = recap_text(ACTIVE, [(1, 100), (2, 100)], NAMES, 1, best, "ended by Cy")
    assert "is over after 1 round (ended by Cy)." in tied
    assert "🏆 **Ann** and **Bo** tie with 100 points each!" in tied
    assert "Also" not in tied
    empty = recap_text(ACTIVE, [(1, 0), (2, 0)], NAMES, 1, scored(1, 2, 0), "too few players left")
    assert empty.split("\n") == [
        "🏁 Fifty-Fifty game **Friday** is over after 1 round (too few players left).",
        "Nobody scored.",
        HINT,
    ]
    assert recap_text(ACTIVE, [], NAMES, 0, None).endswith(
        f"after 0 rounds.\nNobody scored.\n{HINT}"
    )
    assert tied.endswith(f"\n{HINT}")
    unknown = recap_text(ACTIVE, [(9, 100)], NAMES, 1, None)
    assert "🏆 **?** wins with 100 points!" in unknown and "Best split" not in unknown


def test_saved_text():
    assert saved_text("eat cold *soup* every day", "never eat pizza again", False).split("\n") == [
        "Saved. Your question now reads:",
        FILLED,
        "You can change it until writing closes.",
    ]
    cut = saved_text("soup", "salad", True).split("\n")
    assert cut[1] == "Would you rather soup or salad?"
    assert cut[2] == "(Each option is cut to 80 characters.)"


def test_messages_fit_2000():
    names = {uid: ("*_" * 16) for uid in range(1, 13)}
    assert all(len(n) == 32 for n in names.values())
    points = [(uid, 500) for uid in names]
    zeros = [(uid, 0) for uid in names]
    best = ScoredQuestion(1, "*" * 80, "_" * 80, None, 6, 5, "t")
    game = {**ACTIVE, "name": "n" * 100, "max_rounds": 5}
    assert (
        len(recap_text(game, points, names, 5, best, "nobody voted on the last 2 questions")) < 2000
    )
    assert len(recap_text(game, [(1, 500), *zeros[1:]], names, 5, best, "x" * 40)) < 2000
    assert len(round_text(game, 4, zeros, points, names, set(names))) < 2000
    assert len(round_text(game, 4, points, points, names, set())) < 2000
    long_name = {uid: "a" * 80 for uid in names}
    assert "a" * 33 not in round_text(game, 4, points, points, long_name, set())


def test_lobby_embed():
    embed = lobby_embed(OPEN, ["Ann", "B_b"], "Ann", 3, 120, 60)
    assert embed.title == "⚖️ Friday: sign-ups open"
    assert (
        embed.description == "Host: **Ann**\n2 players. 1 more needed to start.\n\n1. Ann\n2. B\\_b"
    )
    assert embed.fields[0].name == "How it works"
    assert embed.fields[0].value == rules_text(OPEN, 120, 60, 3).split("\n", 1)[1]
    assert len(embed.fields[0].value) < 1024
    assert (
        "\n• Press **Write** for the form, or **Answer by DM** to reply by DM.\n"
    ) in embed.fields[0].value
    longest = lobby_embed({**ACTIVE, "max_rounds": 5}, ["Ann"], "Ann", 12, 3600, 3600)
    assert len(longest.fields[0].value) < 1024
    ready = lobby_embed(OPEN, ["Ann", "Bo", "Cy"], "Ann", 3)
    assert "3 players. Enough to start!" in ready.description
    running = lobby_embed(ACTIVE, ["Ann", "Bo", "Cy"], "Bo", 3)
    assert running.title == "⚖️ Friday: in progress"
    assert running.description.startswith("Host: **Bo**\n3 players.\n")
    assert "This game runs 3 rounds." in running.fields[0].value
    done = lobby_embed(FINISHED, ["Ann"], "Ann", 3)
    assert done.title == "🏁 Friday: finished" and "1 player at the end." in done.description
    assert lobby_embed(CANCELLED, [], "Ann", 3).title == "🏁 Friday: cancelled"
    full = lobby_embed(OPEN, ["*_" * 16] * 12, "*_" * 16, 3)
    assert len(full.description) < 4096


def test_lobby_view():
    labels = [c.item.label for c in lobby_view(OPEN).children]
    assert labels == ["Join", "Leave", "Rules", "Start"]
    ids = [c.item.custom_id for c in lobby_view(OPEN).children]
    assert ids == [f"fiftyfifty:{a}:7" for a in ("join", "leave", "rules", "start")]
    assert [c.item.label for c in lobby_view(ACTIVE).children] == ["Join", "Leave", "Rules"]
    assert lobby_view(FINISHED) is None


def test_join_leave_and_host_lines():
    assert join_line("Ann", OPEN, 2, 3, False) == (
        "🙋 **Ann** joined Fifty-Fifty game **Friday**. "
        "2 of at least 3 players signed up, 1 more needed to start."
    )
    assert (
        join_line("Ann", ACTIVE, 4, 3, True) == "🙋 **Ann** joined and is in from the next round."
    )
    assert join_line("A_n", ACTIVE, 4, 3, False) == "🙋 **A\\_n** joined."
    assert leave_line("Ann", OPEN, 1, 3).startswith("🚪 **Ann** left Fifty-Fifty game **Friday**.")
    assert leave_line("Ann", ACTIVE, 3, 3) == "🚪 **Ann** left. 3 players remain."
    assert leave_line("Ann", ACTIVE, 1, 3) == "🚪 **Ann** left. 1 player remains."
    assert host_line("Bo") == "👑 **Bo** is now the host."


def test_status_lines():
    mine = {
        "question_id": 5,
        "option_a": "",
        "option_b": "",
        "answer": None,
        "answered_at": None,
        "topic": "road_trips",
    }
    writing = status_lines(ACTIVE, ROUND, (1, 3), "in", mine=mine)
    assert writing == [
        "✍️ **Round 1 of 3**: writing. 1/3 written. Closes <t:1767225720:R>.",
        "You haven't written your question yet. Press **Write** on the round message.",
        "Topic idea: **road\\_trips**. Ignore it if you have something better.",
    ]
    assert len(status_lines(ACTIVE, ROUND, (1, 3), "in", mine={**mine, "topic": None})) == 2
    wrote = {**mine, "option_a": "eat *soup*", "option_b": "skip lunch", "answered_at": "t"}
    written = status_lines(ACTIVE, ROUND, (2, 3), "in", mine=wrote)
    assert written[1:] == ["Your question: Would you rather eat \\*soup\\* or skip lunch?"]
    old = {**mine, "option_a": "eat ____", "option_b": "skip lunch", "answer": "soup"}
    old_written = status_lines(ACTIVE, ROUND, (2, 3), "in", mine={**old, "answered_at": "t"})
    assert old_written[1:] == ["Your question: Would you rather eat **soup** or skip lunch?"]
    out = status_lines(ACTIVE, ROUND, (1, 3), "out")
    assert out[1:] == ["You're not in this game. Join with `/gaminator fiftyfifty join`."]
    assert status_lines(ACTIVE, ROUND, (1, 3), "next")[1:] == ["You're in from the next round."]

    voting_round = {**ROUND, "write_closed_at": "t"}
    between = status_lines(ACTIVE, voting_round, (0, 0), "in", mine=mine)
    assert between == ["✍️ **Round 1 of 3**: the next question is on its way."]
    voting = status_lines(ACTIVE, voting_round, (1, 2), "in", QUESTION, 4, mine)
    assert voting == [
        f"🗳️ **Question 2 of 4** (round 1): {FILLED}",
        "1/2 voted, 0/2 guessed. Closes <t:1767225720:R>.",
        "🗳️ You haven't voted on this question yet.",
    ]
    picked = status_lines(ACTIVE, voting_round, (2, 2), "in", QUESTION, 4, mine, "b", 1)
    assert picked[1] == "2/2 voted, 1/2 guessed. Closes <t:1767225720:R>."
    assert picked[-1] == "🗳️ You picked **B**, and you haven't guessed the split yet."
    guessed = status_lines(ACTIVE, voting_round, (2, 2), "in", QUESTION, 4, mine, "b", 2, 0)
    assert guessed[-1] == "🗳️ You picked **B** and guessed **0** for A."
    own = status_lines(ACTIVE, voting_round, (1, 2), "in", QUESTION, 4, {**mine, "question_id": 9})
    assert own[-1] == "You wrote this one, so you sit this vote out."
    assert status_lines(ACTIVE, voting_round, (1, 2), "next", QUESTION, 4)[-1] == (
        "You're in from the next round."
    )
    assert status_lines(ACTIVE, None, (0, 0), "in") == ["The next round is on its way."]
    done = {**voting_round, "closed_at": "t"}
    assert status_lines(ACTIVE, done, (0, 0), "in") == ["The next round is on its way."]


def test_dm_texts():
    reply = [
        "Reply here with both options, on two lines or split by `|`, like",
        "`eat only soup | eat only salad`",
        (
            "Each option can be up to 80 characters. You can send another reply to change them "
            "until writing closes."
        ),
    ]
    assert dm_question_text("road_trips", None).split("\n") == [
        "✍️ Write your Fifty-Fifty would-you-rather",
        "Topic idea: **road\\_trips**. Ignore it if you have something better.",
        *reply,
    ]
    assert dm_question_text(None, None).split("\n") == [
        "✍️ Write your Fifty-Fifty would-you-rather",
        *reply,
    ]
    assert place_text("Friday", "Board Game Club") == "**Friday** in **Board Game Club**"
    assert place_text("Friday", None) == "**Friday** in another server"
    assert place_text("Fri_day", "Club*") == "**Fri\\_day** in **Club\\***"
    place = place_text("Friday", "Board Game Club")
    assert dm_question_text("food", FILLED, place).split("\n") == [
        "✍️ Write your Fifty-Fifty would-you-rather for **Friday** in **Board Game Club**",
        "Topic idea: **food**. Ignore it if you have something better.",
        f"Your question so far: {FILLED}",
        *reply,
    ]
    assert dm_sent_text() == "Check your DMs."
    assert dm_failed_text() == "I couldn't DM you (your DMs may be closed). Use **Write** instead."
    assert dm_closed_text() == "Writing has closed for that round."
    saved = saved_text("soup", "salad", False)
    assert dm_saved_text(saved) == saved
    assert dm_saved_text(saved, place) == f"For {place}: {saved}"


def test_leaderboard_text():
    assert leaderboard_text([], {}) == (
        "No finished Fifty-Fifty games yet. Start one with `/gaminator fiftyfifty create`."
    )
    rows = [
        (1, Standing(1, 2, 340, 7, 4)),
        (2, Standing(2, 1, 100, 1, 1)),
        (12, Standing(3, 0, 1, 2, 2)),
    ]
    assert leaderboard_text(rows, {1: "Ann", 2: "B_o"}).split("\n") == [
        "🏆 **Fifty-Fifty: all-time standings**",
        "`1.` **Ann**: 2 games won, 340 points, 4 played",
        "`2.` **B\\_o**: 1 game won, 100 points, 1 played",
        "",
        "`12.` **?**: 0 games won, 1 point, 2 played",
    ]


def test_member_text():
    assert member_text("Ann", None, []) == "**Ann** hasn't played a finished game yet."
    best = [
        ScoredQuestion(1, "eat soup every day", "never eat pizza again", None, 2, 2, CLOSED_AT),
        ScoredQuestion(1, "nap", "r*n", None, 3, 1, ""),
    ]
    text = member_text("A_n", Standing(1, 2, 340, 7, 4), best)
    assert text.split("\n") == [
        "**A\\_n**: 2 games won, 340 points, 4 played",
        "7 questions written, 49 points on average",
        "Best splits:",
        f"• 100: {SOUP} (2 to 2, <t:1767225720:d>)",
        "• 50: Would you rather nap or r\\*n? (3 to 1)",
    ]
    idle = member_text("Bo", Standing(2, 0, 0, 0, 3), [])
    assert idle == "**Bo**: 0 games won, 0 points, 3 played\n0 questions written"
    one = member_text("Cy", Standing(3, 0, 1, 1, 1), [])
    assert one.split("\n")[1] == "1 question written, 1 point on average"
    assert member_text("Di", Standing(4, 0, 25, 2, 1), []).endswith("13 points on average")
    guesser = member_text("Ed", Standing(5, 1, 270, 2, 1, guess_points=70), [])
    assert guesser.split("\n") == [
        "**Ed**: 1 game won, 270 points, 1 played",
        "2 questions written, 100 points on average",
        "70 points from guessing splits",
    ]
    only_guesses = member_text("Flo", Standing(6, 0, 50, 0, 1, guess_points=50), [])
    assert only_guesses.split("\n")[1:] == ["0 questions written", "50 points from guessing splits"]

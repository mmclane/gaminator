"""Headlines text builders."""

import re

import discord

from gaminator.games.headlines.lobby import lobby_view
from gaminator.games.headlines.services import game as flow
from gaminator.games.headlines.services.rules import (
    DEFAULT_ROUNDS,
    MAX_ROUNDS,
    NO_BALLOT,
    OWES_ANSWER,
    OWES_HEADLINE,
    OWES_VOTE,
    ScoredPairing,
    Standing,
)

ANN, BO, CY, DEE = 1, 2, 3, 4
NAMES = {ANN: "Ann", BO: "Bo", CY: "Cy", DEE: "Dee"}
HEADLINE = "Local woman's shocking kitchen confession"
ANSWER = "lick the spoon"
GAME = {"id": 1, "name": "Friday", "status": "active", "max_rounds": 2, "started_at": "x"}
OPEN = {**GAME, "status": "open", "started_at": None}
ROUND = {
    "id": 7,
    "number": 1,
    "answer_deadline_at": "2026-11-25T12:00:00+00:00",
    "write_deadline_at": "2026-11-25T12:02:00+00:00",
    "vote_deadline_at": "2026-11-25T12:03:00+00:00",
    "closed_at": None,
}
STAMP = re.compile(r"<t:[0-9]+:R>")
NASTY = "# *bold* _it_ `code` > ~~x~~ ||y||"
NASTY_NAME = "_*Ann*_ `#>`"
esc = discord.utils.escape_markdown


def row(position, votes, writer, quoted, headline=HEADLINE, answer=ANSWER, round_number=1):
    return ScoredPairing(round_number, position, writer, quoted, headline, answer, votes)


RESULT_ROWS = [
    row(1, 1, CY, DEE, "Area man finally explains the noise"),
    row(2, 0, ANN, BO, "Experts baffled by neighbor's routine"),
    row(3, 4, BO, ANN),
]
TOTALS = [(BO, 400), (ANN, 100), (CY, 100), (DEE, 25)]


def test_rules_text():
    text = flow.rules_text(OPEN, 90, 120, 60, 3)
    assert text.startswith("## 📰 How Headlines works\n")
    assert text.count("\n• ") == 8
    assert "100 points" in text and "25" in text
    assert "90 seconds" in text and "2 minutes" in text and "1 minute" in text
    assert DEFAULT_ROUNDS == MAX_ROUNDS == 5
    assert "A game is 5 rounds unless the host picks fewer with" in text
    assert "picks more" not in text
    assert "• Step 1: " in text and "• Step 2: " in text and "• Step 3: " in text
    assert "Names stay hidden until the results." in text
    assert "who said it" not in text and "menu" not in text
    assert "fewer than 3 players" in text
    assert "from the next round" in text
    running = flow.rules_text({**GAME, "max_rounds": 4}, 90, 120, 60, 2)
    assert "This game runs 4 rounds." in running and "fewer than 2 players" in running
    assert "This game runs 1 round." in flow.rules_text({**GAME, "max_rounds": 1})


def test_answer_text():
    text = flow.answer_text(GAME, ROUND, 0, 5)
    lines = text.split("\n")
    assert flow.ANSWER_STEP == "step 1 of 3, answer a question"
    assert lines[:2] == ["_ _", "## 📰 Round 1 of 2: step 1 of 3, answer a question"]
    assert lines[2].startswith("Press **Answer**. ") and "(up to 100 characters)" in lines[2]
    assert "only you can see" in lines[2]
    assert lines[3] == (
        "Next, another player gets your answer as a quote, without the question, and writes a "
        "news headline that makes it as funny as possible."
    )
    assert STAMP.sub("T", lines[4]) == "0/5 answered. Closes T."
    assert len(lines) == 5
    assert flow.answer_text(GAME, ROUND, 3, 5).split("\n")[4].startswith("3/5 answered.")
    done = flow.answer_closed_text(GAME, ROUND, "done", 5)
    assert done.split("\n")[1:] == [
        "## 📰 Round 1 of 2: step 1 of 3, answer a question",
        "Answering is closed: 5 answers in.",
    ]
    assert flow.answer_closed_text(GAME, ROUND, "done", 1).endswith("1 answer in.")
    assert flow.answer_closed_text(GAME, ROUND, "ended").endswith("\nThe game ended.")
    skipped = flow.answer_closed_text(GAME, ROUND, "skipped")
    assert skipped.endswith("\nThis round was skipped: I couldn't post the next step.")


def test_write_text():
    text = flow.write_text(GAME, ROUND, 0, 4)
    lines = text.split("\n")
    assert flow.WRITE_STEP == "step 2 of 3, write a headline"
    assert lines[:2] == ["_ _", "## 📰 Round 1 of 2: step 2 of 3, write a headline"]
    assert lines[2].startswith("Press **Write headline**. ")
    assert "(up to 120 characters)" in lines[2]
    assert "without the question or their name" in lines[2]
    assert (
        lines[3]
        == flow.WRITE_EXAMPLE
        == ('Example: for the quote "I just wing it", try "Surgeon Explains Her Method".')
    )
    assert STAMP.sub("T", lines[4]) == "0/4 headlines in. Closes T."
    assert len(lines) == 5
    done = flow.write_closed_text(GAME, ROUND, "done", 4)
    assert done.split("\n")[1] == "## 📰 Round 1 of 2: step 2 of 3, write a headline"
    assert done.endswith("\nHeadlines are closed: 4 to read.")
    assert flow.write_closed_text(GAME, ROUND, "ended").endswith("\nThe game ended.")
    skipped = flow.write_closed_text(GAME, ROUND, "skipped")
    assert skipped.endswith("\nThis round was skipped: I couldn't post the next step.")


def test_empty_round_lines():
    assert flow.empty_round_line(GAME, ROUND, "no_answers") == (
        "_ _\n⏭️ Not enough answers came in, so round 1 is over."
    )
    assert flow.empty_round_line(GAME, ROUND, "no_headlines") == (
        "_ _\n⏭️ No headlines came in, so round 1 is over."
    )
    assert flow.empty_round_line(GAME, ROUND, "one_headline") == (
        "_ _\n⏭️ A vote needs at least two headlines, so this one is just for fun."
    )


def test_reveal_intro():
    explained = (
        "A headline is written by one player about another player's answer to a different question."
    )
    assert flow.reveal_intro(GAME, ROUND, 4) == (
        "_ _\n## 📰 Round 1 of 2: the headlines\n"
        f"4 stories tonight. {explained} "
        "The vote opens after the last one, and names come out with the results."
    )
    assert flow.reveal_intro(GAME, ROUND, 1) == (
        f"_ _\n## 📰 Round 1 of 2: the headlines\n1 story tonight. {explained}"
    )


def test_pairing_text():
    text = flow.pairing_text(3, 5, HEADLINE, ANSWER)
    assert text == f'_ _\n📰 **3 of 5**\n## {HEADLINE}\n> "{ANSWER}"'
    assert "(" not in text and "<@" not in text
    assert not hasattr(flow, "pairing_text_for")


def test_vote_text():
    text = flow.vote_text(GAME, ROUND, 0, 5)
    lines = text.split("\n")
    assert flow.VOTE_STEP == "step 3 of 3, vote"
    assert lines[:2] == ["_ _", "## 📰 Round 1 of 2: step 3 of 3, vote"]
    assert lines[2] == (
        "Press **Vote** and pick the funniest headline and quote. Your own headline isn't on "
        "your list. Votes and names stay hidden until the results, and you can change your "
        "vote until it closes."
    )
    assert "menu" not in text
    assert STAMP.sub("T", lines[3]) == "0/5 voted. Closes T."
    assert len(lines) == 4
    closed = flow.vote_closed_text(GAME, ROUND)
    assert closed.split("\n")[1:] == [
        "## 📰 Round 1 of 2: step 3 of 3, vote",
        "Voting is closed.",
    ]
    assert flow.BALLOT_TEXT == (
        "Pick the funniest headline and quote. Your own headline isn't on this list."
    )
    assert NO_BALLOT == "There's nothing for you to vote on this round: the only headline is yours."


def test_vote_options_capped():
    option = flow.vote_option(42, 3, HEADLINE, ANSWER)
    assert (option.label, option.value, option.description) == (f"3. {HEADLINE}", "42", ANSWER)
    long = flow.vote_option(7, 12, "h" * 120, "a" * 101)
    assert len(long.label) == 100 and long.label == "12. " + "h" * 95 + "…"
    assert len(long.description) == 100 and long.description == "a" * 99 + "…"
    exact = flow.vote_option(7, 1, "h" * 97, "a" * 100)
    assert exact.label == "1. " + "h" * 97 and exact.description == "a" * 100
    raw = flow.vote_option(7, 1, NASTY, NASTY)
    assert raw.label == f"1. {NASTY}" and raw.description == NASTY


def test_result_messages():
    (text,) = flow.result_messages(GAME, ROUND, RESULT_ROWS, NAMES, TOTALS)
    assert text.split("\n") == [
        "_ _",
        "## 🏆 Round 1 of 2: the results",
        f"`3.` {HEADLINE} (4 votes): headline by **Bo** +400, quote from **Ann** +100",
        (
            "`1.` Area man finally explains the noise (1 vote): "
            "headline by **Cy** +100, quote from **Dee** +25"
        ),
        (
            "`2.` Experts baffled by neighbor's routine (0 votes): "
            "headline by **Ann** +0, quote from **Bo** +0"
        ),
        "Totals: Bo 400, Ann 100, Cy 100, Dee 25",
        "Round 2 starts now.",
    ]
    (last,) = flow.result_messages(GAME, ROUND, RESULT_ROWS, NAMES)
    assert "Totals" not in last and "starts now" not in last
    assert last.count("\n") == 4
    long = flow.result_messages(GAME, ROUND, [row(1, 0, BO, ANN, "h" * 120)], NAMES)[0]
    assert "`1.` " + "h" * 40 + "… (0 votes)" in long


def test_result_messages_are_packed():
    names = {n: f"Player number {n} with a long name" for n in range(1, 13)}
    rows = [row(n, 12 - n, n, n % 12 + 1, "*" * 120, "*" * 100) for n in range(1, 13)]
    totals = [(n, 100 * n) for n in range(1, 13)]
    parts = flow.result_messages(GAME, ROUND, rows, names, totals)
    assert len(parts) > 1
    assert all(part.startswith("_ _\n") and len(part) <= flow.MESSAGE_BUDGET for part in parts)
    assert parts[0].split("\n")[1] == "## 🏆 Round 1 of 2: the results"
    assert all("## " not in part for part in parts[1:])
    lines = [line for part in parts for line in part.split("\n") if line != "_ _"]
    assert len(lines) == 1 + 12 + 2
    assert [line[:5] for line in lines[1:13]] == [f"`{n}.` "[:5] for n in range(1, 13)]
    assert lines[-2].startswith("Totals: ") and lines[-1] == "Round 2 starts now."


def test_recap_text():
    points = [(BO, 500), (ANN, 125), (CY, 0)]
    top = row(3, 4, BO, ANN)
    text = flow.recap_text(GAME, points, NAMES, 2, top)
    assert text.split("\n") == [
        "_ _",
        "🏁 Headlines game **Friday** is over after 2 rounds.",
        "🏆 **Bo** wins with 500 points!",
        "Also: Ann 125, Cy 0",
        f'📰 Top headline: "{HEADLINE}" by **Bo**, quoting **Ann** (4 votes)',
        "All-time standings: `/gaminator headlines leaderboard`",
    ]
    unvoted = flow.recap_text(GAME, points, NAMES, 1, top, "ended by Ann", unvoted_round=2)
    lines = unvoted.split("\n")
    assert lines[1] == "🏁 Headlines game **Friday** is over after 1 round (ended by Ann)."
    assert lines[4] == "Round 2's headlines were not voted on, so they score nothing."
    assert lines[5].startswith("📰 Top headline:")
    tie = flow.recap_text(GAME, [(ANN, 100), (BO, 100), (CY, 25)], NAMES, 2, None)
    assert "🏆 **Ann** and **Bo** tie with 100 points each!" in tie
    assert "Also: Cy 25" in tie and "Top headline" not in tie
    nobody = flow.recap_text(GAME, [(ANN, 0), (BO, 0)], NAMES, 0, None, "too few players left", 1)
    assert nobody.split("\n")[1:] == [
        "🏁 Headlines game **Friday** is over after 0 rounds (too few players left).",
        "Nobody scored.",
        "Round 1's headlines were not voted on, so they score nothing.",
        "All-time standings: `/gaminator headlines leaderboard`",
    ]


def test_popup_blocks_and_saved_replies():
    assert flow.question_block("How do you nap?") == (
        "**Your question**\nHow do you nap?\n"
        "Only you see this. Answer honestly in a few words. Another player gets your answer "
        "as a quote, without the question, and writes a headline about it."
    )
    assert flow.quote_block(ANSWER) == (
        f'**Your quote**\n> "{ANSWER}"\n'
        "Someone said this about something harmless. Write a news headline that makes it "
        'as funny as possible. Example: for the quote "I just wing it", try '
        '"Surgeon Explains Her Method".'
    )
    assert flow.answer_saved_text(ANSWER) == (
        f'Saved. Your answer: "{ANSWER}". You can change it until answering closes.'
    )
    assert flow.headline_saved_text(HEADLINE) == (
        f"Saved. Your headline: **{HEADLINE}**. You can change it until headlines close."
    )
    assert flow.vote_saved_text(3) == (
        "Your vote is on headline **3**. You can change it until voting closes."
    )


def test_status_lines():
    def lines(*args):
        return [STAMP.sub("T", line) for line in flow.status_lines(GAME, ROUND, *args)]

    stamp = ROUND["answer_deadline_at"]
    assert lines("answering", (3, 5), stamp, OWES_ANSWER) == [
        "Round 1 of 2: answering. 3/5 answered. Closes T.",
        OWES_ANSWER,
    ]
    assert lines("writing", (1, 4), stamp, OWES_HEADLINE) == [
        "Round 1 of 2: writing headlines. 1/4 headlines in. Closes T.",
        OWES_HEADLINE,
    ]
    assert lines("voting", (2, 5), stamp, OWES_VOTE) == [
        "Round 1 of 2: voting. 2/5 voted. Closes T.",
        OWES_VOTE,
    ]
    assert lines("voting", (2, 5), stamp) == ["Round 1 of 2: voting. 2/5 voted. Closes T."]
    assert lines("reveal") == ["Round 1 of 2: the headlines are being read out."]
    assert lines(None) == ["The next round is on its way."]
    assert flow.status_lines(GAME, None, None) == ["The next round is on its way."]


def test_join_leave_and_host_lines():
    assert flow.join_line("Ann", OPEN, 2, 3) == (
        "_ _\n🙋 **Ann** joined Headlines game **Friday**. "
        "2 of at least 3 players signed up, 1 more needed to start."
    )
    assert flow.join_line("Ann", GAME, 4, 3) == "_ _\n🙋 **Ann** joined."
    assert flow.join_line("Ann", GAME, 4, 3, mid_round=True) == (
        "_ _\n🙋 **Ann** joined and is in from the next round."
    )
    assert flow.leave_line("Ann", OPEN, 3, 3).startswith("_ _\n🚪 **Ann** left Headlines game")
    assert flow.leave_line("Ann", GAME, 3, 3) == "_ _\n🚪 **Ann** left. 3 players remain."
    assert flow.leave_line("Ann", GAME, 1, 3) == "_ _\n🚪 **Ann** left. 1 player remains."
    assert flow.host_line("Bo") == "_ _\n👑 **Bo** is now the host."
    assert flow.cancelled_text(OPEN, "everyone left") == (
        "_ _\n🚫 Headlines game **Friday** was cancelled (everyone left)."
    )


def channel_messages(headline=HEADLINE, answer=ANSWER, name="Ann"):
    """Every kind of message the game posts in the channel, built with the given user text."""
    names = {ANN: name, BO: "Bo"}
    rows = [row(1, 2, BO, ANN, headline, answer), row(2, 0, ANN, BO, headline, answer)]
    points = [(BO, 200), (ANN, 50)]
    return {
        "answer": flow.answer_text(GAME, ROUND, 0, 5),
        "answer closed": flow.answer_closed_text(GAME, ROUND, "done", 5),
        "write": flow.write_text(GAME, ROUND, 0, 4),
        "write closed": flow.write_closed_text(GAME, ROUND, "done", 4),
        "empty": flow.empty_round_line(GAME, ROUND, "no_answers"),
        "intro": flow.reveal_intro(GAME, ROUND, 4),
        "pairing": flow.pairing_text(1, 2, headline, answer),
        "vote": flow.vote_text(GAME, ROUND, 0, 5),
        "vote closed": flow.vote_closed_text(GAME, ROUND),
        "result": "\n".join(flow.result_messages(GAME, ROUND, rows, names, points)),
        "recap": flow.recap_text(GAME, points, names, 1, rows[0], "ended by " + flow.safe(name)),
        "join": flow.join_line(name, GAME, 3, 3),
        "leave": flow.leave_line(name, GAME, 3, 3),
        "host": flow.host_line(name),
        "cancelled": flow.cancelled_text(OPEN, "everyone left"),
        "standings": flow.leaderboard_text([(1, Standing(ANN, 1, 50, 1))], names),
    }


def test_user_text_escaped():
    built = channel_messages(NASTY, NASTY, NASTY_NAME)
    for key in ("pairing", "result", "recap"):
        assert NASTY not in built[key], key
    assert esc(NASTY) in built["pairing"]
    assert built["pairing"].count(esc(NASTY)) == 2
    assert esc(NASTY) in built["recap"] and esc(NASTY[:40]) in built["result"]
    for key in ("result", "recap", "join", "leave", "host", "standings"):
        assert NASTY_NAME not in built[key], key
        assert esc(NASTY_NAME) in built[key], key
    for text in (
        flow.question_block(NASTY),
        flow.quote_block(NASTY),
        flow.answer_saved_text(NASTY),
        flow.headline_saved_text(NASTY),
        flow.member_text(NASTY_NAME, None),
        flow.member_text(NASTY_NAME, Standing(ANN, 1, 50, 1)),
        flow.title({**GAME, "name": NASTY}),
    ):
        assert NASTY not in text and NASTY_NAME not in text
        assert esc(NASTY) in text or esc(NASTY_NAME) in text
    embed = flow.lobby_embed({**OPEN, "name": NASTY}, [NASTY_NAME], NASTY_NAME, 3)
    assert NASTY not in embed.title and NASTY_NAME not in embed.description


def test_names_only_in_results_and_roster_lines():
    built = channel_messages(name="Zebedee")
    hidden = ("answer", "answer closed", "write", "write closed", "empty", "intro", "pairing")
    for key in (*hidden, "vote", "vote closed"):
        assert "Zebedee" not in built[key] and "Bo" not in built[key], key
        assert "<@" not in built[key], key
    for key in ("result", "recap", "join", "leave", "host", "standings"):
        assert "Zebedee" in built[key], key
    assert "headline by **Bo**" in built["result"] and "quote from **Zebedee**" in built["result"]


def test_channel_messages_start_with_gap():
    for key, text in channel_messages().items():
        assert text.startswith(flow.GAP), key
    assert flow.GAP == "_ _\n"


def test_step_messages_have_heading():
    built = channel_messages()
    for key in ("answer", "answer closed", "write", "write closed", "intro", "vote", "result"):
        assert built[key].split("\n")[1].startswith("## "), key
    assert built["pairing"].split("\n")[2].startswith("## ")
    for key in ("empty", "recap", "join", "leave", "host", "cancelled"):
        assert "## " not in built[key], key


def test_messages_fit_2000():
    name = "_" * 32
    names = {n: name for n in range(1, 13)}
    headline, answer = "*" * 120, "*" * 100
    rows = [row(n, 11, n, n % 12 + 1, headline, answer) for n in range(1, 13)]
    points = [(n, 1375) for n in range(1, 13)]
    assert len(flow.pairing_text(12, 12, headline, answer)) <= 470
    last_game, last_round = {**GAME, "max_rounds": 5}, {**ROUND, "number": 5}
    assert len(flow.answer_text(last_game, last_round, 12, 12)) <= 400
    assert len(flow.write_text(last_game, last_round, 12, 12)) <= 400
    assert len(flow.vote_text(last_game, last_round, 12, 12)) <= 400
    assert len(flow.reveal_intro(last_game, last_round, 12)) <= 400
    assert len(flow.question_block("*" * 100)) <= 400
    assert len(flow.quote_block(answer)) <= 450
    assert len(flow.BALLOT_TEXT) <= 2000 and len(NO_BALLOT) <= 2000
    assert len(flow.rules_text(OPEN, 3601, 3601, 3601, 12)) <= 2000
    built = channel_messages(headline, answer, name)
    assert all(len(text) <= 2000 for key, text in built.items() if key != "result"), built
    parts = flow.result_messages(GAME, ROUND, rows, names, points)
    assert 1 < len(parts) <= 3 and all(len(part) <= 1900 for part in parts)
    assert sum(part.count("headline by") for part in parts) == 12
    for part in parts:
        for line in part.split("\n")[1:]:
            assert line.startswith(("## ", "`", "Totals: ", "Round "))
    game = {**GAME, "name": "*" * 100}
    recap = flow.recap_text(game, points, names, 5, rows[0], "ended by " + flow.safe(name), 5)
    assert len(recap) <= 2000
    standing_rows = [(n, Standing(n, 99, 99999, 99)) for n in range(1, 12)]
    assert len(flow.leaderboard_text(standing_rows, names)) <= 2000


def test_lobby_embed():
    embed = flow.lobby_embed(OPEN, ["Ann", "Bo"], "Ann", 3, 90, 120, 60)
    assert embed.title == "📰 Friday: sign-ups open"
    assert embed.description == "Host: **Ann**\n2 players. 1 more needed to start.\n\n1. Ann\n2. Bo"
    (field,) = embed.fields
    assert field.name == "How it works" and field.value == flow.how_it_works(OPEN, 90, 120, 60, 3)
    assert len(field.value) < 1024
    assert len(flow.how_it_works({**GAME, "max_rounds": 5}, 3600, 3601, 3602, 12)) < 1024
    slow = (86399, 86399, 86399, 12)
    for game in (OPEN, {**GAME, "max_rounds": 5}, {**GAME, "max_rounds": 1}):
        assert len(flow.how_it_works(game, *slow)) <= 1024, game
        assert len(flow.lobby_embed(game, ["Ann"], "Ann", 12, *slow[:3]).fields[0].value) <= 1024
    assert "A game is 5 rounds unless the host picks fewer" in field.value
    ready = flow.lobby_embed(OPEN, ["Ann", "Bo", "Cy"], "Ann", 3)
    assert "Enough to start!" in ready.description
    running = flow.lobby_embed(GAME, ["Ann", "Bo", "Cy"], "Ann", 3)
    assert running.title == "📰 Friday: in progress" and "3 players." in running.description
    done = flow.lobby_embed({**GAME, "status": "finished"}, ["Ann"], "Ann", 3)
    assert done.title == "🏁 Friday: finished" and "1 player at the end." in done.description
    cancelled = flow.lobby_embed({**OPEN, "status": "finished"}, [], "Ann", 3)
    assert cancelled.title == "🏁 Friday: cancelled"


def test_lobby_view():
    labels = [c.item.label for c in lobby_view(OPEN).children]
    assert labels == ["Join", "Leave", "Rules", "Start"]
    ids = [c.item.custom_id for c in lobby_view(OPEN).children]
    assert ids == [f"headlines:{a}:1" for a in ("join", "leave", "rules", "start")]
    assert [c.item.label for c in lobby_view(GAME).children] == ["Join", "Leave", "Rules"]
    assert lobby_view({**GAME, "status": "finished"}) is None
    assert lobby_view(OPEN).timeout is None


def test_leaderboard_text():
    rows = [(1, Standing(ANN, 2, 1250, 4)), (2, Standing(BO, 1, 300, 1))]
    assert flow.leaderboard_text(rows, NAMES).split("\n") == [
        "_ _",
        "## 🏆 Headlines: all-time standings",
        "`1.` **Ann**: 2 games won, 1250 points, 4 played",
        "`2.` **Bo**: 1 game won, 300 points, 1 played",
    ]
    lower = flow.leaderboard_text([*rows, (14, Standing(CY, 0, 0, 1))], NAMES).split("\n")
    assert lower[-2:] == ["", "`14.` **Cy**: 0 games won, 0 points, 1 played"]
    assert flow.leaderboard_text([], NAMES) == (
        "No finished Headlines games yet. Start one with `/gaminator headlines create`."
    )


def test_member_text():
    assert flow.member_text("Ann", Standing(ANN, 2, 1250, 4)) == (
        "**Ann**: 2 games won, 1250 points, 4 played"
    )
    assert flow.member_text("Ann", None) == "**Ann** hasn't played a finished game yet."

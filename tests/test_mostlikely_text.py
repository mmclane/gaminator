"""Most Likely To text builders."""

from gaminator.games.mostlikely.lobby import lobby_view
from gaminator.games.mostlikely.services.game import (
    closed_text,
    host_line,
    join_line,
    leaderboard_text,
    leave_line,
    lobby_embed,
    member_text,
    recap_text,
    reveal_text,
    rules_text,
    title,
    vote_text,
)
from gaminator.games.mostlikely.services.rules import ClosedPrompt, Standing, recap, tally

OPEN = {"id": 7, "name": "Friday", "status": "open", "max_rounds": 10, "started_at": None}
ACTIVE = {"id": 7, "name": "Friday", "status": "active", "max_rounds": 6, "started_at": "x"}
FINISHED = {**ACTIVE, "status": "finished"}
CANCELLED = {**OPEN, "status": "finished"}
ROUND = {"number": 3, "prompt": "name their car", "deadline_at": "2026-01-01T00:02:00+00:00"}
NAMES = {1: "Ann", 2: "Bob", 3: "Cy"}


def test_title():
    assert title(OPEN) == "Most Likely To game **Friday**"


def test_rules_text():
    text = rules_text(ACTIVE, 120)
    assert "This game runs 6 prompts." in text
    assert "after 2 minutes" in text
    assert "only the totals are shown" in text
    assert "nobody votes on 2 prompts in a row" in text
    assert "/gaminator mostlikely join" in text
    assert "10 prompts unless the host picks another number" in rules_text(OPEN, 30)
    assert "after 30 seconds" in rules_text(OPEN, 30)
    assert "after 1 minute." in rules_text(OPEN, 60)


def test_vote_text():
    text = vote_text(ACTIVE, ROUND, 3, 6)
    assert "_ _\n🤔 **Prompt 3 of 6**\n## Most likely to name their car\n" in text
    assert "3/6 votes in." in text
    assert "Closes <t:1767225720:R>." in text
    assert closed_text(ACTIVE, ROUND).endswith("Voting is closed.")


def test_reveal_text():
    single = reveal_text(ACTIVE, ROUND, tally([1, 1, 2]), NAMES)
    assert "Most likely to name their car" in single
    assert "It's **Ann** with 2 votes." in single
    assert single.endswith("Results: Ann 2, Bob 1")
    assert "Cy" not in single
    one = reveal_text(ACTIVE, ROUND, tally([2]), NAMES)
    assert "**Bob** with 1 vote." in one
    tie = reveal_text(ACTIVE, ROUND, tally([1, 2]), NAMES)
    assert "a tie between **Ann** and **Bob** with 1 vote each." in tie
    three = reveal_text(ACTIVE, ROUND, tally([3, 1, 2]), NAMES)
    assert "a tie between **Ann**, **Bob** and **Cy**" in three
    assert reveal_text(ACTIVE, ROUND, tally([]), NAMES).endswith("Nobody voted.")


def test_recap_text():
    wins = recap([[1, 1], [1], [2]])
    text = recap_text(ACTIVE, wins, NAMES, 3)
    assert text.startswith("🏁 Most Likely To game **Friday** is over after 3 prompts.")
    assert "🏆 **Ann** wins with 2 prompts!" in text
    assert "\nAlso: Bob 1\n" in text
    assert text.endswith("All-time standings: `/gaminator mostlikely leaderboard`")
    tied = recap_text(ACTIVE, recap([[1], [2]]), NAMES, 2, "ended by Cy")
    assert "is over after 2 prompts (ended by Cy)." in tied
    assert "🏆 **Ann** and **Bob** tie with 1 prompt each!" in tied
    assert "Also" not in tied
    empty = recap_text(ACTIVE, [], NAMES, 1, "too few players left")
    assert "after 1 prompt (too few players left)." in empty
    assert "\nNobody won a prompt.\n" in empty
    assert empty.endswith("All-time standings: `/gaminator mostlikely leaderboard`")


def test_join_leave_host_lines():
    lobby = join_line("Ann", OPEN, 1, 3, False)
    assert lobby.startswith("🙋 **Ann** joined Most Likely To game **Friday**.")
    assert "2 more needed to start" in lobby
    assert join_line("Ann", ACTIVE, 4, 3, True) == (
        "🙋 **Ann** joined and is in from the next prompt."
    )
    assert join_line("Ann", ACTIVE, 4, 3, False) == "🙋 **Ann** joined."
    assert "Enough to start" in leave_line("Bob", OPEN, 3, 3)
    assert leave_line("Bob", ACTIVE, 2, 3) == "🚪 **Bob** left. 2 players remain."
    assert leave_line("Bob", ACTIVE, 1, 3) == "🚪 **Bob** left. 1 player remains."
    assert host_line("Cy") == "👑 **Cy** is now the host."


def test_lobby_embed():
    card = lobby_embed(OPEN, ["Ann"], "Ann", 3)
    assert card.title == "🤔 Friday: sign-ups open"
    assert card.description == "Host: **Ann**\n1 player. 2 more needed to start.\n\n1. Ann"
    ready = lobby_embed(OPEN, ["Ann", "Bob", "Cy"], "Ann", 3)
    assert "3 players. Enough to start!" in ready.description
    assert ready.description.endswith("1. Ann\n2. Bob\n3. Cy")
    (field,) = ready.fields
    assert field.name == "How it works" and "only the totals are shown" in field.value
    assert len(field.value) < 1024
    running = lobby_embed(ACTIVE, ["Bob", "Cy"], "Bob", 3)
    assert running.title == "🤔 Friday: in progress"
    assert running.description == "Host: **Bob**\n2 players.\n\n1. Bob\n2. Cy"
    assert "This game runs 6 prompts." in running.fields[0].value
    assert lobby_embed(FINISHED, ["Bob"], "Bob", 3).title == "🏁 Friday: finished"
    assert lobby_embed(CANCELLED, [], "Ann", 3).title == "🏁 Friday: cancelled"
    assert lobby_embed(CANCELLED, [], "Ann", 3).description == "0 players at the end."


def test_lobby_embed_fits_25_awkward_names():
    names = [("*_" * 40) for _ in range(25)]
    card = lobby_embed(OPEN, names, names[0], 3)
    assert len(card.description) < 4096
    assert "\\*\\_" in card.description
    assert all(len(line) <= 4 + 2 * 32 for line in card.description.split("\n")[3:])


async def test_lobby_view():
    def labels(view):
        return [child.item.label for child in view.children]

    assert labels(lobby_view(OPEN)) == ["Join", "Leave", "Rules", "Start"]
    assert labels(lobby_view(ACTIVE)) == ["Join", "Leave", "Rules"]
    assert lobby_view(FINISHED) is None
    assert lobby_view(OPEN).children[0].item.custom_id == "mostlikely:join:7"


def test_leaderboard_text():
    rows = [(1, Standing(1, 3, 12, 5)), (2, Standing(2, 1, 1, 1)), (14, Standing(3, 0, 0, 2))]
    lines = leaderboard_text(rows, NAMES).split("\n")
    assert lines[0] == "🏆 **Most Likely To: all-time standings**"
    assert lines[1] == "`1.` **Ann**: 3 games won, 12 prompts won, 5 played"
    assert lines[2] == "`2.` **Bob**: 1 game won, 1 prompt won, 1 played"
    assert lines[3] == ""
    assert lines[4] == "`14.` **Cy**: 0 games won, 0 prompts won, 2 played"
    assert "" not in leaderboard_text(rows[:2], NAMES).split("\n")
    assert leaderboard_text([], {}) == (
        "No finished Most Likely To games yet. Start one with `/gaminator mostlikely create`."
    )


def test_member_text():
    recent = [ClosedPrompt("name their car", "2026-01-01T00:02:00+00:00", (1,))]
    text = member_text("Ann", Standing(1, 3, 12, 5), recent)
    assert text == (
        "**Ann**: 3 games won, 12 prompts won, 5 played\n"
        "Recent prompt wins:\n"
        "• Most likely to name their car (<t:1767225720:d>)"
    )
    assert member_text("Bob", Standing(2, 0, 0, 1), []) == (
        "**Bob**: 0 games won, 0 prompts won, 1 played"
    )
    assert member_text("Cy", None, []) == "**Cy** hasn't played a finished game yet."

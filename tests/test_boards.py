import pytest

from job_mcp import boards
from job_mcp.boards import Board, BoardError, detect, fetch_board, guesses, parse_board

pytestmark = pytest.mark.anyio


async def test_greenhouse():
    jobs = await fetch_board(Board("greenhouse", "acme"), "Acme")
    first = jobs[0]
    assert first.id == "greenhouse:acme:101"
    assert first.company == "Acme"
    assert first.title == "Software Engineer, New Grad"
    assert first.level == "new_grad"
    assert first.locations == ("Bangalore",)
    assert first.department == "University"  # Without the number in front.
    assert first.posted == "2026-10-20"
    assert first.salary == "$100,000 - $120,000"  # Found in the description.
    # The description was escaped twice; it comes out as plain text, without the CSS.
    assert first.description == ("## About the role\nBuild payments APIs with Python and Go.\n\n"
                                 "- Design services\n- Ship code\n\n"
                                 "The pay range is $100,000 - $120,000 per year.")
    senior = jobs[2]
    assert (senior.workplace, senior.level, senior.department) == ("remote", "senior", None)
    assert senior.posted == "2026-09-01"  # No first_published: falls back to updated_at.


async def test_lever():
    intern, scientist = await fetch_board(Board("lever", "leverco"), "LeverCo")
    assert intern.id == "lever:leverco:lv-1"
    assert intern.locations == ("London", "Manchester")
    assert (intern.level, intern.employment_type, intern.workplace) == ("intern", "Intern", "hybrid")
    assert intern.posted == "2026-10-01"
    assert intern.salary == "GBP 3,000 - 3,500 per month"
    assert intern.description == ("Join us for the summer.\n\n## What you'll do\n- Write Rust\n"
                                  "- Review code\n\nWe sponsor visas.")
    assert (scientist.level, scientist.workplace, scientist.department) == ("staff", "remote", "Data")


async def test_ashby():
    designer, manager = await fetch_board(Board("ashby", "ashco"), "AshCo")  # Hidden job skipped.
    assert (designer.workplace, designer.employment_type, designer.salary) == (
        "remote", "Full-time", "$150K - $180K")
    assert manager.locations == ("San Francisco", "Seattle")
    assert (manager.level, manager.workplace, manager.department) == ("manager", "onsite", "Payments")
    assert manager.description == "Lead a team."  # From the HTML, as there's no plain text.


async def test_fetch_board_errors():
    with pytest.raises(BoardError, match="no Lever board called 'nope'"):
        await fetch_board(Board("lever", "nope"), "Nope")
    with pytest.raises(BoardError, match=r"busy \(500\)"):
        await fetch_board(Board("lever", "downco"), "DownCo")


async def test_fetch_board_retries_when_busy(fake_boards):
    assert await fetch_board(Board("greenhouse", "busyco"), "BusyCo") == []
    assert len(fake_boards.requests) == 2


async def test_fetch_board_reports_a_strange_answer(fake_boards):
    fake_boards[("ashby", "ashco")] = {"jobs": [{"title": "No id"}]}
    with pytest.raises(BoardError, match="format we don't understand"):
        await fetch_board(Board("ashby", "ashco"), "AshCo")


@pytest.mark.parametrize("text, expected", [
    ("https://jobs.lever.co/spotify", Board("lever", "spotify")),
    ("jobs.lever.co/spotify/2a55d6c9-c564/apply", Board("lever", "spotify")),
    ("https://boards.greenhouse.io/Stripe", Board("greenhouse", "stripe")),
    ("https://job-boards.greenhouse.io/vercel/jobs/6181759004", Board("greenhouse", "vercel")),
    ("https://boards-api.greenhouse.io/v1/boards/stripe/jobs", Board("greenhouse", "stripe")),
    ("https://jobs.ashbyhq.com/openai", Board("ashby", "openai")),
    ("ashby:openai", Board("ashby", "openai")),
    ("Lever: cred", Board("lever", "cred")),
    ("Stripe", None),
    ("https://careers.example.com/jobs", None),
])
def test_parse_board(text, expected):
    assert parse_board(text) == expected


def test_guesses():
    assert guesses("Scale AI") == ["scaleai", "scale-ai"]
    assert guesses("Stripe") == ["stripe"]
    assert guesses("!!!") == []


async def test_detect_picks_the_board_with_most_jobs():
    found = await detect("AshCo", "AshCo")
    assert found.board == Board("ashby", "ashco")  # Not the old one-job Greenhouse board.
    assert [job.title for job in found.jobs] == ["Product Designer", "Engineering Manager, Payments"]


async def test_detect_not_found():
    with pytest.raises(BoardError, match="couldn't find 'Nowhere Inc' on Greenhouse, Lever or "
                                         r"Ashby \(tried nowhereinc, nowhere-inc\)"):
        await detect("Nowhere Inc", "Nowhere Inc")


async def test_detect_reports_broken_boards():
    with pytest.raises(BoardError, match=r"couldn't check every job board for 'DownCo'.*busy \(500\)"):
        await detect("DownCo", "DownCo")


def test_every_source_has_a_parser_and_url():
    assert set(boards.PARSERS) == set(boards.API_URLS) == set(boards.SOURCES)

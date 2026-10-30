"""Tests that talk to the server through a real MCP client, the same way Claude does."""

from datetime import UTC, datetime, timedelta

import pytest
from mcp import Client

from job_mcp import server
from job_mcp.boards import Board
from job_mcp.server import mcp

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def temporary_data(monkeypatch, tmp_path):
    """Keep the jobs database in a temporary folder, not the real one, and use a small starter
    list made of the fake boards."""
    monkeypatch.setattr(server, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(server, "STARTER", {"Acme": Board("greenhouse", "acme"),
                                            "LeverCo": Board("lever", "leverco")})


def text_of(result) -> str:
    return "\n".join(block.text for block in result.content)


async def call(tool: str, args: dict | None = None):
    async with Client(mcp) as client:
        return await client.call_tool(tool, args or {})


async def text(tool: str, args: dict | None = None) -> str:
    result = await call(tool, args)
    assert not result.is_error, text_of(result)
    return text_of(result)


async def test_lists_all_tools():
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == {
            "add_companies", "list_companies", "remove_company", "refresh_jobs", "search_jobs",
            "get_job",
        }


async def test_add_companies():
    reply = await text("add_companies", {"companies": [
        "AshCo", "https://jobs.lever.co/leverco", "Nowhere", "acme", "ashco"]})
    assert reply.splitlines() == [
        "Now watching 3 more companies, with 8 open jobs between them.",
        "- AshCo: Ashby board 'ashco', 2 open jobs",
        "- LeverCo: Lever board 'leverco', 2 open jobs",  # Named as in the starter list.
        "- Nowhere: couldn't find 'Nowhere' on Greenhouse, Lever or Ashby (tried nowhere). If it "
        "has a careers page on one of them, pass its URL (like https://jobs.lever.co/<name>); "
        "otherwise it uses a job board we can't read yet.",
        "- Acme: Greenhouse board 'acme', 4 open jobs",
        "- ashco: already watched (as ashby:ashco)",
    ]
    again = await text("add_companies", {"companies": ["ACME"], "starter_list": True})
    assert again.splitlines()[1:] == ["- ACME: already watched", "- LeverCo: already watched"]


async def test_add_companies_needs_some():
    result = await call("add_companies", {})
    assert result.is_error
    assert "give some companies" in text_of(result)


async def test_list_and_remove_companies():
    assert "Not watching any companies yet" in await text("list_companies")
    await text("add_companies", {"starter_list": True})
    listing = await text("list_companies")
    assert listing.startswith("Watching 2 companies, with 6 open jobs:\n- Acme (greenhouse:acme): 4 open")
    assert listing.endswith("By level: intern 1, new_grad 2, mid 1, senior 1, staff 1")
    assert await text("remove_company", {"company": "acme"}) == (
        "Stopped watching Acme and removed its 4 open jobs.")
    assert "Watching 1 companies" in await text("list_companies")
    result = await call("remove_company", {"company": "Acme"})
    assert result.is_error


async def test_search_jobs():
    await text("add_companies", {"starter_list": True})
    reply = await text("search_jobs", {"query": "software engineer", "locations": ["India"]})
    # The new grad role (posted for Bangalore and Dublin) shows once; the procurement job, which
    # only mentions software engineers, comes last.
    assert reply.split("\n\n") == [
        "2 jobs match, shown as 2 roles. get_job reads a job's full description.",
        "1. **Software Engineer, New Grad** · Acme\n"
        "   new_grad · Bangalore · posted 2026-10-20 · $100,000 - $120,000\n"
        "   id: greenhouse:acme:101 · https://acme.example/jobs/101",
        "2. **Procurement Specialist** · Acme\n"
        "   mid · Bengaluru, India · posted 2026-10-25\n"
        "   id: greenhouse:acme:104 · https://acme.example/jobs/104",
    ]
    grads = await text("search_jobs", {"levels": ["new_grad", "intern"]})
    assert "Bangalore; Dublin" in grads
    assert "(the same role has 1 more posting for other places)" in grads
    assert "GBP 3,000 - 3,500 per month" in grads


async def test_search_jobs_without_matches():
    await text("add_companies", {"starter_list": True})
    assert (await text("search_jobs", {"query": "astronaut"})).startswith("No jobs match")


async def test_search_jobs_errors():
    result = await call("search_jobs", {"query": "x"})
    assert result.is_error and "add_companies first" in text_of(result)
    await text("add_companies", {"starter_list": True})
    for args, message in [({"limit": 0}, "limit must be between"),
                          ({"posted_within_days": -1}, "must not be negative"),
                          ({"companies": ["Acme", "Initech"]}, "not watching Initech"),
                          ({"levels": ["wizard"]}, "wizard")]:
        result = await call("search_jobs", args)
        assert result.is_error and message in text_of(result), args


async def test_search_jobs_refreshes_stale_boards(fake_boards, monkeypatch):
    await text("add_companies", {"starter_list": True})
    fake_boards[("greenhouse", "acme")]["jobs"].pop(0)  # The Bangalore new grad job closes.
    fake_boards[("greenhouse", "acme")]["jobs"].append({
        "id": 105, "title": "Security Engineer", "absolute_url": "https://acme.example/jobs/105",
        "location": {"name": "Pune"}, "content": "", "departments": []})

    # Just added, so nothing is refreshed yet.
    assert "Security Engineer" not in await text("search_jobs")
    # Six hours later, the boards are refreshed first.
    later = datetime.now(UTC) + server.STALE_AFTER + timedelta(minutes=1)
    monkeypatch.setattr(server, "_stale", lambda company: datetime.fromisoformat(company.refreshed) < later)
    reply = await text("search_jobs", {"locations": ["India"]})
    assert reply.startswith("Refreshed 2 boards first (1 new jobs).")
    assert "Security Engineer" in reply and "New Grad" not in reply
    closed = await text("search_jobs", {"query": "new grad", "include_closed": True})
    assert "CLOSED: gone from the board since" in closed


async def test_refresh_jobs(fake_boards):
    await text("add_companies", {"starter_list": True, "companies": ["DownCo"]})
    fake_boards[("lever", "leverco")].append({
        "id": "lv-3", "text": "Platform Engineer", "hostedUrl": "https://jobs.lever.co/leverco/lv-3",
        "categories": {"location": "London"}})
    reply = await text("refresh_jobs")
    assert reply.splitlines() == [
        "Refreshed 2 of 2 companies: 1 new jobs, 0 closed.",
        "- LeverCo: 1 new (Platform Engineer)",
        "search_jobs with new_within_days=1 lists the new jobs.",
    ]
    assert "Refreshed 1 of 1 companies: 0 new jobs" in await text("refresh_jobs", {"companies": ["acme"]})
    result = await call("refresh_jobs", {"companies": ["Initech"]})
    assert result.is_error and "not watching 'Initech'" in text_of(result)


async def test_refresh_jobs_reports_broken_boards(fake_boards):
    await text("add_companies", {"starter_list": True})
    del fake_boards[("lever", "leverco")]
    reply = await text("refresh_jobs")
    assert "Refreshed 1 of 2 companies" in reply
    assert "Couldn't refresh:\n- LeverCo: there's no Lever board called 'leverco'" in reply
    assert "last refresh failed: there's no Lever board" in await text("list_companies")
    # Its jobs are still searchable.
    assert "Staff Data Scientist" in await text("search_jobs", {"query": "data scientist"})


async def test_get_job():
    await text("add_companies", {"starter_list": True})
    reply = await text("get_job", {"job_id": "lever:leverco:lv-1"})
    assert reply.startswith(
        "# Software Engineering Intern\nCompany: LeverCo\nLevel: intern\nType: Intern\n"
        "Places: London; Manchester\nWorkplace: hybrid\nDepartment: Engineering\n"
        "Salary: GBP 3,000 - 3,500 per month\nPosted: 2026-10-01\nFirst seen: ")
    assert reply.endswith("Apply: https://jobs.lever.co/leverco/lv-1\n\nJoin us for the summer.\n\n"
                          "## What you'll do\n- Write Rust\n- Review code\n\nWe sponsor visas.")


async def test_get_job_in_parts():
    await text("add_companies", {"starter_list": True})
    first = await text("get_job", {"job_id": "lever:leverco:lv-1", "max_chars": 10})
    assert first.endswith("\n\nJoin us fo\n\n[78 more characters: call get_job again with start_index=10]")
    rest = await text("get_job", {"job_id": "lever:leverco:lv-1", "start_index": 10})
    assert rest.endswith("\n\nr the summer.\n\n## What you'll do\n- Write Rust\n- Review code\n\nWe sponsor visas.")
    result = await call("get_job", {"job_id": "nope"})
    assert result.is_error and "no job with id 'nope'" in text_of(result)

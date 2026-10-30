"""Job MCP server.

Gives an AI assistant (like Claude) tools to find jobs: watch companies' public job boards
(Greenhouse, Lever, Ashby), keep their jobs in a local database that stays up to date, and search
them by words, place, level (intern, new grad...) and freshness.
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from job_mcp import boards
from job_mcp.boards import Board, BoardError, Job
from job_mcp.starter import STARTER
from job_mcp.store import Company, Listing, Store, StoreError, Update

# The database lives in the repo's data/ folder (override with JOB_DATA_DIR). It's your own job
# search: back it up, don't commit it.
REPO = Path(__file__).resolve().parents[2]  # src/job_mcp/server.py -> the repo.
DATA_DIR = Path(os.environ.get("JOB_DATA_DIR", REPO / "data")).resolve()

# search_jobs first refreshes boards not fetched for this long, so results are never stale.
STALE_AFTER = timedelta(hours=6)
# How many boards are fetched at the same time.
CONCURRENT_BOARDS = 6

# search_jobs shows this many results by default, and at most MAX_LIMIT.
DEFAULT_LIMIT = 15
MAX_LIMIT = 50
# get_job returns at most this many characters of a description by default.
DEFAULT_MAX_CHARS = 12_000

Level = Literal["intern", "new_grad", "junior", "mid", "senior", "staff", "manager"]
Progress = Callable[[float, float | None, str | None], Awaitable[None]]

# httpx logs every request; keep that noise out of the server's logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = MCPServer(
    name="jobs",
    instructions=(
        "Tools for finding jobs on companies' own job boards (Greenhouse, Lever and Ashby). The "
        "user watches a list of companies; their open jobs are kept in a local database. "
        "search_jobs searches them by words, places, level (intern, new_grad, junior, mid, "
        "senior, staff, manager), remote, and how recently they were posted or first seen; it "
        "refreshes stale boards itself. get_job reads a job's full description. add_companies "
        "watches more companies by name or careers-page URL (starter_list=True adds about 40 "
        "tech companies); list_companies shows the watch list. refresh_jobs fetches every board "
        "now and reports what's new. When showing jobs, include the company, title, place and "
        "the apply link."
    ),
)


def _store() -> Store:
    return Store(DATA_DIR / "jobs.db")


def _stale(company: Company) -> bool:
    if not company.refreshed:
        return True
    return datetime.now(UTC) - datetime.fromisoformat(company.refreshed) > STALE_AFTER


async def _gather_limited(tasks: list[Awaitable], progress: Progress | None, label: str) -> list:
    """Run tasks CONCURRENT_BOARDS at a time, reporting progress as each finishes. Returns their
    results in order (exceptions included, not raised)."""
    limit = asyncio.Semaphore(CONCURRENT_BOARDS)
    done = 0

    async def run(task: Awaitable):
        nonlocal done
        async with limit:
            try:
                return await task
            except Exception as error:  # Reported with the results, not raised.
                return error
            finally:
                done += 1
                if progress:
                    await progress(done, len(tasks), f"{label} {done} of {len(tasks)}")

    return await asyncio.gather(*(run(task) for task in tasks))


async def _refresh(store: Store, companies: list[Company],
                   progress: Progress | None) -> dict[str, Update | str]:
    """Fetch the companies' boards and save their jobs. Returns, per company, what changed or
    why it failed."""
    results = await _gather_limited(
        [boards.fetch_board(company.board, company.name) for company in companies],
        progress, "Refreshed")
    changes: dict[str, Update | str] = {}
    for company, jobs in zip(companies, results):
        if isinstance(jobs, BoardError):
            store.refresh_failed(company.name, str(jobs))
            changes[company.name] = str(jobs)
        elif isinstance(jobs, Exception):
            raise jobs
        else:
            changes[company.name] = store.update(company.name, jobs)
    return changes


def _failures(changes: dict[str, Update | str]) -> list[str]:
    return [f"- {name}: {error}" for name, error in changes.items() if isinstance(error, str)]


# --- The watch list ---------------------------------------------------------------------------

async def _find(name: str) -> tuple[str, Board, list[Job]]:
    """A company's (display name, board, jobs), from its name, careers-page URL or starter entry."""
    if board := boards.parse_board(name):
        # Named as in the starter list if it's there; else after its board ("cred" -> "Cred").
        display = next((starter for starter, known in STARTER.items() if known == board),
                       board.name[:1].upper() + board.name[1:])
        return display, board, await boards.fetch_board(board, display)
    for starter_name, board in STARTER.items():
        if starter_name.lower() == name.lower():
            return starter_name, board, await boards.fetch_board(board, starter_name)
    found = await boards.detect(name, name)
    return name, found.board, found.jobs


@mcp.tool()
async def add_companies(ctx: Context, companies: list[str] | None = None,
                        starter_list: bool = False) -> str:
    """Watch companies' job boards: their open jobs are fetched now and kept up to date, ready
    for search_jobs. A company can be given by name ("Stripe": its board is found on Greenhouse,
    Lever or Ashby automatically), by careers-page URL ("https://jobs.lever.co/spotify"), or as
    "source:board" ("ashby:openai").

    Args:
        companies: Companies to watch, e.g. ["Stripe", "Notion", "https://jobs.lever.co/cred"].
        starter_list: Also add a starter list of about 40 tech companies (Stripe, OpenAI,
            Anthropic, Databricks, Figma, Notion, Spotify, Groww, CRED, Meesho...).
    """
    names = [" ".join(name.split()) for name in companies or [] if name.strip()]
    if starter_list:
        names += [name for name in STARTER if name.lower() not in {n.lower() for n in names}]
    if not names:
        raise ToolError("give some companies, or starter_list=True")

    store = _store()
    watched = {company.name.lower() for company in store.companies()}
    watched_boards = {company.board for company in store.companies()}
    lines, todo = [], []
    for name in names:
        if name.lower() in watched:
            lines.append(f"- {name}: already watched")
        else:
            todo.append(name)
    results = await _gather_limited([_find(name) for name in todo], ctx.report_progress, "Checked")

    added = jobs_added = 0
    for name, result in zip(todo, results):
        if isinstance(result, BoardError):
            lines.append(f"- {name}: {result}")
            continue
        if isinstance(result, Exception):
            raise result
        display, board, jobs = result
        if board in watched_boards:
            lines.append(f"- {name}: already watched (as {board.source}:{board.name})")
            continue
        try:
            store.add_company(display, board)
        except StoreError as error:
            lines.append(f"- {name}: {error}")
            continue
        watched_boards.add(board)
        update = store.update(display, jobs)
        added += 1
        jobs_added += update.open
        lines.append(f"- {display}: {boards.SOURCE_NAMES[board.source]} board "
                     f"'{board.name}', {update.open} open jobs")
    heading = (f"Now watching {added} more compan{'y' if added == 1 else 'ies'}, "
               f"with {jobs_added:,} open jobs between them.")
    return "\n".join([heading, *lines])


@mcp.tool()
def list_companies() -> str:
    """The companies being watched: their job board, how many jobs are open, and when they were
    last refreshed (and why, if refreshing failed)."""
    store = _store()
    companies = store.companies()
    if not companies:
        return ("Not watching any companies yet. add_companies adds them (starter_list=True adds "
                "about 40 tech companies).")
    lines = [f"Watching {len(companies)} companies, with "
             f"{sum(c.open_jobs for c in companies):,} open jobs:"]
    for company in companies:
        line = (f"- {company.name} ({company.board.source}:{company.board.name}): "
                f"{company.open_jobs} open · refreshed {company.refreshed or 'never'}")
        if company.error:
            line += f" · last refresh failed: {company.error}"
        lines.append(line)
    counts = store.counts()
    lines.append("By level: " + ", ".join(f"{level} {counts[level]:,}"
                                          for level in Level.__args__ if counts.get(level)))
    return "\n".join(lines)


@mcp.tool()
def remove_company(company: str) -> str:
    """Stop watching a company, and forget its jobs.

    Args:
        company: The company's name, as list_companies shows it.
    """
    try:
        removed = _store().remove_company(company)
    except StoreError as error:
        raise ToolError(str(error)) from error
    return f"Stopped watching {removed.name} and removed its {removed.open_jobs} open jobs."


@mcp.tool()
async def refresh_jobs(ctx: Context, companies: list[str] | None = None) -> str:
    """Fetch the latest jobs from every watched board now (search_jobs already refreshes boards
    older than 6 hours by itself). Reports how many jobs are new and how many have closed.

    Args:
        companies: Only refresh these companies (default: all of them).
    """
    store = _store()
    try:
        chosen = ([store.company(name) for name in companies] if companies
                  else store.companies())
    except StoreError as error:
        raise ToolError(str(error)) from error
    if not chosen:
        raise ToolError("not watching any companies yet; add_companies adds them")
    changes = await _refresh(store, chosen, ctx.report_progress)
    updates = {name: u for name, u in changes.items() if isinstance(u, Update)}
    new = sum(len(u.new) for u in updates.values())
    closed = sum(u.closed for u in updates.values())
    lines = [f"Refreshed {len(updates)} of {len(chosen)} companies: {new:,} new jobs, "
             f"{closed:,} closed."]
    for name, update in sorted(updates.items(), key=lambda item: -len(item[1].new)):
        if update.new:
            titles = ", ".join(job.title for job in update.new[:3])
            more = f" and {len(update.new) - 3} more" if len(update.new) > 3 else ""
            lines.append(f"- {name}: {len(update.new)} new ({titles}{more})")
    if failures := _failures(changes):
        lines += ["Couldn't refresh:", *failures]
    if new:
        lines.append("search_jobs with new_within_days=1 lists the new jobs.")
    return "\n".join(lines)


# --- Searching --------------------------------------------------------------------------------

def _describe(number: int, listing: Listing) -> str:
    job = listing.job.job
    places = listing.locations
    where = "; ".join(places[:3]) + (f" (+{len(places) - 3} more places)" if len(places) > 3 else "")
    facts = [job.level, job.employment_type, where or None, job.workplace,
             f"posted {job.posted}" if job.posted else None, job.salary]
    line = (f"{number}. **{job.title}** · {job.company}\n"
            f"   {' · '.join(fact for fact in facts if fact)}\n"
            f"   id: {job.id} · {job.url}")
    if listing.also:
        line += f"\n   (the same role has {len(listing.also)} more posting{'s' * (len(listing.also) != 1)} for other places)"
    if listing.job.closed:
        line += f"\n   CLOSED: gone from the board since {listing.job.closed[:10]}"
    return line


@mcp.tool()
async def search_jobs(
    ctx: Context,
    query: str = "",
    locations: list[str] | None = None,
    remote_only: bool = False,
    levels: list[Level] | None = None,
    companies: list[str] | None = None,
    posted_within_days: int | None = None,
    new_within_days: int | None = None,
    sort: Literal["relevance", "newest"] = "relevance",
    include_closed: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> str:
    """Search the open jobs at every watched company. Words are matched in the title (which
    counts most), department, places and description, in any word form ("engineer" also finds
    "engineering"); every word must match. Put quotes around a phrase ("machine learning").
    The same role posted for several cities is shown once. Boards not refreshed in the last 6
    hours are refreshed first.

    Args:
        query: Words to search for, e.g. "backend python" or '"machine learning" infrastructure'.
            Leave empty to list jobs by the other filters alone.
        locations: Places, any of which may match, e.g. ["India"], ["Bengaluru", "Remote"],
            ["London", "Dublin"]. "Bay Area" matches San Francisco, Palo Alto, Mountain
            View and the rest. India, the UK, Canada, Germany and Australia also match
            their main tech cities (many boards write just "Bangalore"); for other countries,
            add the cities too.
        remote_only: Only jobs marked remote (combine with locations for "remote in India").
        levels: Only these levels. Worked out from titles: intern (internships, co-ops),
            new_grad (graduate, early career), junior, mid (anything else), senior, staff
            (staff, principal), manager (engineering managers, directors).
        companies: Only these companies (names as list_companies shows them).
        posted_within_days: Only jobs the company posted in the last this many days.
        new_within_days: Only jobs that appeared on the board in the last this many days, as
            seen by our refreshes (catches reposted jobs that keep an old posting date).
        sort: "relevance" (best match for the query first) or "newest" (latest posted first).
        include_closed: Also show jobs that have been taken off their board.
        limit: Most results to show (up to 50).
    """
    if not 1 <= limit <= MAX_LIMIT:
        raise ToolError(f"limit must be between 1 and {MAX_LIMIT}")
    for name, days in (("posted_within_days", posted_within_days),
                       ("new_within_days", new_within_days)):
        if days is not None and days < 0:
            raise ToolError(f"{name} must not be negative")
    store = _store()
    watched = store.companies()
    if not watched:
        raise ToolError("not watching any companies yet: call add_companies first "
                        "(starter_list=True adds about 40 tech companies)")
    notes = []
    if stale := [company for company in watched if _stale(company)]:
        changes = await _refresh(store, stale, ctx.report_progress)
        new = sum(len(u.new) for u in changes.values() if isinstance(u, Update))
        notes.append(f"Refreshed {len(stale)} boards first ({new:,} new jobs).")
        if failures := _failures(changes):
            notes += ["Couldn't refresh (their older jobs are still searched):", *failures]
    if companies:
        known = {company.name.lower() for company in watched}
        if unknown := [name for name in companies if name.strip().lower() not in known]:
            raise ToolError(f"not watching {', '.join(unknown)} (add_companies adds them)")

    listings, total = store.search(
        query, places=locations, remote_only=remote_only, levels=levels, companies=companies,
        posted_within_days=posted_within_days, new_within_days=new_within_days,
        include_closed=include_closed, sort=sort, limit=limit)
    if not listings:
        return "\n".join([*notes, f"No jobs match, out of {len(watched)} companies' boards. Try "
                          "fewer words, more places (cities as well as countries), or no level."])
    shown = sum(1 + len(listing.also) for listing in listings)
    heading = f"{total:,} job{'s' * (total != 1)} match"
    heading += (f"; showing {len(listings)} roles ({shown} postings)" if total > shown
                else f", shown as {len(listings)} roles")
    heading += ". get_job reads a job's full description."
    return "\n\n".join([*notes, heading,
                        *(_describe(n, listing) for n, listing in enumerate(listings, 1))])


@mcp.tool()
def get_job(job_id: str, start_index: int = 0, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """A job's full details and description, by the id search_jobs gives.

    Args:
        job_id: The job's id, e.g. "greenhouse:stripe:8172487".
        start_index: Where to start in the description (to read on, when it's long).
        max_chars: Most characters of the description to return.
    """
    if start_index < 0 or max_chars < 1:
        raise ToolError("start_index must not be negative, and max_chars must be positive")
    try:
        stored = _store().get(job_id)
    except StoreError as error:
        raise ToolError(str(error)) from error
    job = stored.job
    facts = [
        ("Company", job.company), ("Level", job.level), ("Type", job.employment_type),
        ("Places", "; ".join(job.locations)), ("Workplace", job.workplace),
        ("Department", job.department), ("Salary", job.salary), ("Posted", job.posted),
        ("First seen", stored.first_seen[:10]),
        ("Closed", f"gone from the board since {stored.closed[:10]}" if stored.closed else None),
        ("Apply", job.url),
    ]
    text = job.description[start_index:start_index + max_chars]
    end = start_index + len(text)
    lines = [f"# {job.title}", *(f"{label}: {value}" for label, value in facts if value), "", text]
    if end < len(job.description):
        lines.append(f"\n[{len(job.description) - end:,} more characters: call get_job again "
                     f"with start_index={end}]")
    return "\n".join(lines)

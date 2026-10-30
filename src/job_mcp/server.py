"""Job MCP server.

Gives an AI assistant (like Claude) tools to find and apply for jobs: watch companies' public job
boards (Greenhouse, Lever, Ashby), keep their jobs in a local database that stays up to date,
search them, rank them by how well they fit your profile, write a tailored resume and cover
letter for each (checked against your profile, so nothing is made up), prep for interviews, and
track your applications.
"""

import asyncio
import json
import logging
import os
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from job_mcp import boards, documents, fit, guides, profile, skills
from job_mcp.boards import Board, BoardError, Job
from job_mcp.profile import Profile, Resume
from job_mcp.starter import STARTER
from job_mcp.store import Company, Listing, Store, StoreError, StoredJob, Update, same_role
from job_mcp.tracker import DONE, STATUS_NAMES, STATUSES, Status, Tracker, TrackerError, local_date

# Everything lives in the repo's data/ folder (override with JOB_DATA_DIR): the jobs database,
# your profile, and the documents made for each application. It's your own job search: back it
# up, don't commit it.
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
        "now and reports what's new. "
        "The user has a profile (import_resume, save_profile, get_profile): match_jobs ranks "
        "jobs by fit for them and hides ones they can't apply to; explain_fit shows why. To "
        "apply: tailor_resume then save_resume makes a one-page resume PDF for a job, and "
        "write_cover_letter then save_cover_letter makes a cover letter; both are checked "
        "against the profile so nothing is made up. prep_interview prepares for an interview. "
        "track_application and list_applications keep track of where each application is. "
        "When showing jobs, include the company, title, place and the apply link."
    ),
)


def _store() -> Store:
    return Store(DATA_DIR / "jobs.db")


def _tracker() -> Tracker:
    return Tracker(DATA_DIR / "jobs.db")


def _profile_path() -> Path:
    return DATA_DIR / "profile.json"


def _profile() -> Profile:
    if not (found := profile.load(_profile_path())):
        raise ToolError("there's no profile yet: call import_resume with the user's resume, then "
                        "save_profile")
    return found


def _job(job_id: str) -> StoredJob:
    try:
        return _store().get(job_id)
    except StoreError as error:
        raise ToolError(str(error)) from error


def _folder(job: Job) -> Path:
    """Where the documents for a job go: data/applications/<company>-<title>-<id>/."""
    slug = re.sub(r"[^a-z0-9]+", "-", f"{job.company} {job.title}".lower()).strip("-")[:60]
    return DATA_DIR / "applications" / f"{slug}-{job.id.rsplit(':', 1)[-1][-6:]}"


def _file_name(name: str, kind: str) -> str:
    """"Jal Master", "Resume" -> "Jal_Master_Resume.pdf": what a recruiter sees when they
    download it."""
    return "_".join(re.findall(r"\w+", name) + [kind]) + ".pdf"


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


async def _refresh_stale(store: Store, watched: list[Company], progress: Progress | None) -> list[str]:
    """Refresh the boards not fetched for a while, before searching them. Returns notes about
    what happened, to show above the results."""
    if not watched:
        raise ToolError("not watching any companies yet: call add_companies first "
                        "(starter_list=True adds about 40 tech companies)")
    notes = []
    if stale := [company for company in watched if _stale(company)]:
        changes = await _refresh(store, stale, progress)
        new = sum(len(u.new) for u in changes.values() if isinstance(u, Update))
        notes.append(f"Refreshed {len(stale)} boards first ({new:,} new jobs).")
        if failures := _failures(changes):
            notes += ["Couldn't refresh (their older jobs are still searched):", *failures]
    return notes


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
    notes = await _refresh_stale(store, watched, ctx.report_progress)
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


# --- Your profile -----------------------------------------------------------------------------

@mcp.tool()
def import_resume(path: str) -> str:
    """Read the user's resume (a PDF, .txt or .md file) to build their profile from: returns its
    text, with instructions. Follow up with save_profile.

    Args:
        path: The resume's full path, e.g. "C:\\Users\\me\\Downloads\\resume.pdf".
    """
    file = Path(path.strip().strip('"')).expanduser()
    if not file.is_file():
        raise ToolError(f"there's no file at {file}")
    if file.suffix.lower() == ".pdf":
        text = documents.pdf_text(file)
    elif file.suffix.lower() in (".txt", ".md"):
        text = file.read_text(encoding="utf-8", errors="replace")
    else:
        raise ToolError("the resume must be a PDF, .txt or .md file (save a Word file as PDF first)")
    if not text.strip():
        raise ToolError("the PDF has no text in it (it may be a scanned image); paste the text instead")
    existing = profile.load(_profile_path())
    note = ("There's already a profile; save_profile replaces it, so merge anything new into "
            "the current one (get_profile shows it)." if existing else "There's no profile yet.")
    return f"# The resume's text\n\n{text.strip()}\n\n{note}\n\n{guides.IMPORT}"


@mcp.tool()
def save_profile(profile_data: Profile) -> str:
    """Save the user's master profile (replacing the old one): every fact a resume or cover
    letter may use, and what they're looking for. It may hold more projects and bullets than fit
    on one page; tailoring picks the best for each job.

    Args:
        profile_data: The whole profile.
    """
    if not profile_data.name.strip() or not profile_data.email.strip():
        raise ToolError("the profile needs at least a name and an email")
    profile.save(_profile_path(), profile_data)
    _, warnings = profile.check_resume(profile_data, profile_data)
    shown = sorted(profile.skills_of(profile_data))
    prefs = profile_data.preferences
    lines = [
        f"Saved the profile of {profile_data.name}: {len(profile_data.projects)} projects, "
        f"{len(profile_data.experience)} jobs, {len(shown)} skills found ({', '.join(shown)}).",
        f"Looking for: {', '.join(prefs.levels)} roles ({', '.join(prefs.roles)}) in "
        f"{', '.join(prefs.places) or 'any place'}"
        + (f", {', '.join(prefs.seasons)}" if prefs.seasons else "")
        + f"; graduating {prefs.graduation or '(not set)'}, {prefs.degree} degree.",
    ]
    if warnings:
        lines += ["Bullets worth improving:", *(f"- {w}" for w in warnings)]
    lines.append("match_jobs now ranks jobs for this profile.")
    return "\n".join(lines)


@mcp.tool()
def get_profile() -> str:
    """The user's master profile, as JSON."""
    return _profile().model_dump_json(indent=2)


# --- Matching ---------------------------------------------------------------------------------

def _reason(blocker: str) -> str:
    """A blocker, shortened to group similar ones: "based in Vienna, Austria, outside United
    States" -> "outside United States"."""
    if "outside " in blocker:
        return "outside " + blocker.split("outside ", 1)[1]
    return re.sub(r"\s*\(.*\)$", "", blocker)


@mcp.tool()
async def match_jobs(
    ctx: Context, query: str = "", limit: int = DEFAULT_LIMIT,
    companies: list[str] | None = None, new_within_days: int | None = None,
    include_ineligible: bool = False,
) -> str:
    """The jobs that fit the user best, ranked by a fit score (0-100) from their profile: the
    required and nice-to-have skills they have, the kind of role, and how fresh the posting is.
    Searches their preferred places and levels, and hides jobs they can't apply to (for other
    degrees, other seasons, outside their countries, other kinds of role), saying how many.
    Boards not refreshed in the last 6 hours are refreshed first.

    Args:
        query: Optional words to narrow it down, e.g. "backend" or "machine learning".
        limit: Most roles to show (up to 50).
        companies: Only these companies.
        new_within_days: Only jobs that appeared in the last this many days.
        include_ineligible: Also show the hidden jobs, with why they don't fit.
    """
    if not 1 <= limit <= MAX_LIMIT:
        raise ToolError(f"limit must be between 1 and {MAX_LIMIT}")
    me = _profile()
    prefs = me.preferences
    store = _store()
    notes = await _refresh_stale(store, store.companies(), ctx.report_progress)
    found, _ = store.find(query, places=prefs.places or None, levels=prefs.levels or None,
                          companies=companies, new_within_days=new_within_days)
    assessed = [(stored, fit.assess(stored.job, me)) for stored in found]
    shown = sorted(((s, f) for s, f in assessed if include_ineligible or f.eligible),
                   key=lambda item: -item[1].score)
    hidden = Counter(_reason(f.blockers[0]) for _, f in assessed if not f.eligible)

    roles: dict[tuple, tuple[StoredJob, fit.Fit, list[StoredJob]]] = {}
    for stored, assessment in shown:
        key = same_role(stored.job)
        if key in roles:
            roles[key][2].append(stored)
        elif len(roles) < limit:
            roles[key] = (stored, assessment, [])

    statuses = _tracker().statuses()
    looking = ", ".join(filter(None, [", ".join(prefs.levels), " or ".join(prefs.places),
                                      ", ".join(prefs.seasons)]))
    eligible = sum(1 for _, f in assessed if f.eligible)
    heading = f"{eligible} job{'s' * (eligible != 1)} fit your profile ({looking})"
    if query:
        heading += f" and match {query!r}"
    heading += f"; the best {len(roles)} roles:" if roles else "."
    if hidden and not include_ineligible:
        heading += (f"\nHidden {sum(hidden.values())} you can't apply to: "
                    + ", ".join(f"{count} {reason}" for reason, count in hidden.most_common(6))
                    + " (include_ineligible=True shows them).")
    sections = [*notes, heading]
    for number, (stored, assessment, also) in enumerate(roles.values(), 1):
        job = stored.job
        places = list(dict.fromkeys(p for s in [stored, *also] for p in s.job.locations))
        where = "; ".join(places[:3]) + (f" (+{len(places) - 3} more)" if len(places) > 3 else "")
        facts = " · ".join(filter(None, [where, job.workplace,
                                         f"posted {job.posted}" if job.posted else None, job.salary]))
        lines = [f"{number}. **{job.title}** · {job.company} · fit {assessment.score}/100",
                 f"   {facts}", f"   Why: {assessment.summary()}"]
        lines += [f"   Can't apply: {b}" for b in assessment.blockers]
        lines += [f"   Check: {w}" for w in assessment.warnings]
        if status := statuses.get(job.id):
            lines.append(f"   Tracked: {STATUS_NAMES[status]}")
        lines.append(f"   id: {job.id} · {job.url}")
        sections.append("\n".join(lines))
    if roles:
        sections.append("explain_fit shows a job's full fit; tailor_resume and write_cover_letter "
                        "prepare an application.")
    return "\n\n".join(sections)


def _skills_line(label: str, have: list[str], missing: list[str]) -> str:
    if not have and not missing:
        return f"{label}: none named"
    parts = []
    if have:
        parts.append(f"you have {', '.join(have)}")
    if missing:
        parts.append(f"missing {', '.join(missing)}")
    return f"{label}: {'; '.join(parts)}"


@mcp.tool()
def explain_fit(job_id: str) -> str:
    """Why a job does or doesn't fit the user: whether they can apply, the required and
    nice-to-have skills they have and miss, and which of their projects show what it wants.

    Args:
        job_id: The job's id, from match_jobs or search_jobs.
    """
    stored = _job(job_id)
    job = stored.job
    assessment = fit.assess(job, _profile())
    lines = [f"# {job.title} at {job.company}: fit {assessment.score}/100",
             f"Kind of role: {assessment.kind} · level: {job.level} · {'; '.join(job.locations)}"]
    if assessment.blockers:
        lines.append("Can't apply: " + "; ".join(assessment.blockers))
    else:
        lines.append("You can apply.")
    lines += [f"Check before applying: {w}" for w in assessment.warnings]
    lines += [_skills_line("Required skills", assessment.matched_required, assessment.missing_required),
              _skills_line("Nice to have", assessment.matched_preferred, assessment.missing_preferred)]
    best = [f"{name} ({', '.join(shared)})" for name, shared in assessment.projects if shared]
    lines.append("Projects that show what it wants: " + ("; ".join(best) if best else "none directly"))
    if stored.closed:
        lines.append(f"Note: this job was taken off the board on {stored.closed[:10]}.")
    if application := _tracker().find(job.id):
        lines.append(f"Tracked: {STATUS_NAMES[application.status]}")
    lines.append("Score: required skills you have (55 points), nice-to-haves (15), kind of role "
                 "(20), freshness (10), minus 5 per thing to check.")
    return "\n".join(lines)


# --- Applying ---------------------------------------------------------------------------------

def _asks_for(job: Job, chars: int) -> str:
    """The part of a job description about the candidate (its must-haves and nice-to-haves),
    or its opening if those can't be found."""
    parts = skills.sections(job.description)
    text = "\n\n".join(filter(None, [parts["required"], parts["preferred"] and
                                     "Nice to have:\n" + parts["preferred"]]))
    return (text or job.description)[:chars]


def _fit_block(job: Job, me: Profile) -> str:
    assessment = fit.assess(job, me)
    lines = [f"Fit {assessment.score}/100: {assessment.summary()}"]
    if assessment.matched_preferred or assessment.missing_preferred:
        lines.append(_skills_line("Nice to have", assessment.matched_preferred,
                                  assessment.missing_preferred))
    if assessment.blockers:
        lines.append("Warning, this job may not be open to the user: " + "; ".join(assessment.blockers))
    lines += [f"Check: {w}" for w in assessment.warnings]
    lines.append("Projects, most relevant first: " + "; ".join(
        f"{name} ({', '.join(shared) or 'no shared skills'})" for name, shared in assessment.projects))
    return "\n".join(lines)


def _profile_json(me: Profile) -> str:
    return json.dumps(me.model_dump(exclude={"preferences"}), indent=1, ensure_ascii=False)


@mcp.tool()
def tailor_resume(job_id: str) -> str:
    """Everything needed to write a one-page resume tailored to a job: the fit, what the job
    asks for, the user's master profile, and how to write it. Write it, then call save_resume.

    Args:
        job_id: The job's id, from match_jobs or search_jobs.
    """
    job = _job(job_id).job
    me = _profile()
    return (f"# Tailor a resume: {job.title} at {job.company}\njob_id: {job.id}\n\n"
            f"## Fit\n{_fit_block(job, me)}\n\n"
            f"## What the job asks for\n{_asks_for(job, 4000)}\n\n"
            f"## The master profile\n```json\n{_profile_json(me)}\n```\n"
            "(extra_facts can inform wording, but have no place of their own on the resume.)\n\n"
            f"{guides.TAILOR}")


@mcp.tool()
def save_resume(resume: Resume, job_id: str | None = None) -> str:
    """Save a resume as a one-page PDF, after checking it only uses facts from the profile
    (anything made up is rejected, with what to fix). For a job (job_id), it's saved with that
    job's application, checked for the job's keywords as an applicant tracking system would
    read them, and the job is tracked. Without a job_id, it's the user's general resume.

    Args:
        resume: The resume to save.
        job_id: The job it's tailored for, if any.
    """
    me = _profile()
    stored = _job(job_id) if job_id else None
    errors, warnings = profile.check_resume(resume, me)
    if errors:
        raise ToolError("Not saved: the resume has facts that aren't in the profile:\n"
                        + "\n".join(f"- {e}" for e in errors)
                        + "\nUse only the profile's facts, then call save_resume again.")
    folder = _folder(stored.job) if stored else DATA_DIR / "resume"
    path = folder / _file_name(me.name, "Resume")
    pages, scale = documents.render_resume(resume, path)
    if pages > 1:
        path.unlink()
        raise ToolError(f"Not saved: it runs to {pages} pages even set at 90% size. Cut the "
                        "weakest bullets (or a project) and call save_resume again.")
    (folder / "resume.json").write_text(resume.model_dump_json(indent=2), encoding="utf-8")

    lines = [f"Saved a one-page resume: {path}"
             + (f" (set at {scale:.0%} size to fit)" if scale < 1 else "")]
    if stored:
        required, _ = skills.requirements(stored.job.description)
        required -= skills.TOO_GENERAL
        on_page = skills.find(documents.pdf_text(path))
        left_out = sorted((required & profile.skills_of(me)) - on_page)
        line = (f"ATS check (the PDF's text, as a screening system reads it): shows "
                f"{len(required & on_page)} of the job's {len(required)} required skills")
        if left_out:
            line += f"; you have but didn't show: {', '.join(left_out)}"
        lines.append(line + ".")
        _tracker().attach(stored.job, resume=str(path))
        lines.append(f"Tracking {stored.job.company} · {stored.job.title} "
                     f"({STATUS_NAMES[_tracker().get(stored.job.id).status]}).")
    lines += [f"Style: {w}" for w in warnings]
    return "\n".join(lines)


def _about(job: Job, chars: int = 1500) -> str:
    """What a job description says about the company and role (not its requirements)."""
    return skills.sections(job.description)["other"][:chars]


@mcp.tool()
def write_cover_letter(job_id: str) -> str:
    """Everything needed to write a cover letter for a job: what the company and role are about,
    what it asks for, the fit, the user's profile, and how to write it. Write the body, then
    call save_cover_letter.

    Args:
        job_id: The job's id, from match_jobs or search_jobs.
    """
    job = _job(job_id).job
    me = _profile()
    prefs = me.preferences
    return (f"# Cover letter: {job.title} at {job.company}\njob_id: {job.id}\n"
            f"Places: {'; '.join(job.locations)} · the user wants: "
            f"{', '.join(prefs.seasons) or ', '.join(prefs.levels)}\n\n"
            f"## About the company and role (from the posting)\n{_about(job)}\n\n"
            f"## What the job asks for\n{_asks_for(job, 2500)}\n\n"
            f"## Fit\n{_fit_block(job, me)}\n\n"
            f"## The user's profile\n```json\n{_profile_json(me)}\n```\n\n"
            f"{guides.COVER_LETTER}")


@mcp.tool()
def save_cover_letter(job_id: str, paragraphs: list[str], greeting: str = "Dear Hiring Team,",
                      closing: str = "Sincerely,") -> str:
    """Save a cover letter for a job as a PDF (with the same header as the resume) and as plain
    text (for pasting into application forms), after checking it.

    Args:
        job_id: The job it's for.
        paragraphs: The letter's body, one string per paragraph (3-4 paragraphs, 250-350 words).
        greeting: E.g. "Dear Hiring Team," or "Dear Ms. Rivera," if the posting names someone.
        closing: E.g. "Sincerely," or "Best regards,".
    """
    job = _job(job_id).job
    me = _profile()
    paragraphs = [" ".join(p.split()) for p in paragraphs if p.strip()]
    words = sum(len(p.split()) for p in paragraphs)
    errors = []
    if not 2 <= len(paragraphs) <= 6:
        errors.append(f"it has {len(paragraphs)} paragraphs; write 3-4")
    if not 120 <= words <= 450:
        errors.append(f"it's {words} words; aim for 250-350")
    body = "\n".join(paragraphs)
    if placeholders := re.findall(r"\[[^\]]*\]|\{[^}]*\}|\bXXX?\b", body):
        errors.append(f"fill in the placeholders: {', '.join(placeholders)}")
    allowed = "\n".join([profile.text_of(me), job.description, job.title, *me.preferences.seasons,
                         me.preferences.graduation, *(s.dates for s in me.education)])
    if made_up := sorted(profile.numbers_in(body) - profile.numbers_in(allowed)):
        errors.append(f"numbers that aren't in the profile or the posting: {', '.join(made_up)}")
    if errors:
        raise ToolError("Not saved:\n" + "\n".join(f"- {e}" for e in errors)
                        + "\nFix these and call save_cover_letter again.")

    folder = _folder(job)
    path = folder / _file_name(me.name, "Cover_Letter")
    today = date.today()
    pages = documents.render_cover_letter(me, job.company, paragraphs, greeting, closing,
                                          f"{today:%B} {today.day}, {today.year}", path)
    (folder / "cover_letter.txt").write_text(
        "\n\n".join([greeting, *paragraphs, f"{closing}\n{me.name}"]) + "\n", encoding="utf-8")
    _tracker().attach(job, cover_letter=str(path))
    lines = [f"Saved the cover letter ({words} words): {path}",
             f"Plain text for application forms: {folder / 'cover_letter.txt'}"]
    if pages > 1:
        lines.append(f"Style: it runs to {pages} pages; a cover letter should fit on one.")
    if not 200 <= words <= 400:
        lines.append(f"Style: {words} words; 250-350 reads best.")
    lowered = body.lower()
    lines += [f"Style: avoid the cliché {c!r}" for c in guides.CLICHES if c in lowered]
    return "\n".join(lines)


@mcp.tool()
def prep_interview(job_id: str) -> str:
    """Everything needed to prepare the user for an interview for a job: what it asks for, the
    fit and gaps, their projects and experience, and how to build a prep sheet (and run a mock
    interview).

    Args:
        job_id: The job's id.
    """
    job = _job(job_id).job
    me = _profile()
    application = _tracker().find(job.id)
    resume_note = ""
    if application and application.resume:
        resume_json = Path(application.resume).parent / "resume.json"
        if resume_json.exists():
            resume_note = (f"\n\n## The resume they sent\n```json\n"
                           f"{resume_json.read_text(encoding='utf-8')}\n```")
    return (f"# Interview prep: {job.title} at {job.company}\njob_id: {job.id}\n\n"
            f"## About the company and role\n{_about(job, 1200)}\n\n"
            f"## What the job asks for\n{_asks_for(job, 3000)}\n\n"
            f"## Fit\n{_fit_block(job, me)}\n\n"
            f"## The user's profile\n```json\n{_profile_json(me)}\n```{resume_note}\n\n"
            f"{guides.INTERVIEW_PREP}")


# --- Tracking applications --------------------------------------------------------------------

@mcp.tool()
def track_application(job_id: str, status: Status, note: str = "",
                      deadline: str | None = None) -> str:
    """Track an application: start tracking a job, or move it along (interested, applied, oa
    for an online assessment, interview, offer, rejected, withdrawn), with a note for its
    history.

    Args:
        job_id: The job's id.
        status: Where the application is now.
        note: What happened, e.g. "Applied with tailored resume" or "OA: 2 LeetCode mediums".
        deadline: A date to remember, e.g. when an online assessment is due ("2026-11-15").
    """
    tracker = _tracker()
    try:
        job = _store().get(job_id).job
    except StoreError:
        # The company may have been removed from the watch list; its applications stay.
        if not (known := tracker.find(job_id.strip())):
            raise ToolError(f"no job with id '{job_id}'") from None
        job = Job(id=known.job_id, company=known.company, title=known.title, url=known.url,
                  locations=(), workplace=None, level="", employment_type=None, department=None,
                  posted=None, salary=None, description="")
    try:
        application = tracker.track(job, status, note, deadline)
    except TrackerError as error:
        raise ToolError(str(error)) from error
    lines = [f"{job.company} · {job.title}: {STATUS_NAMES[application.status]}"
             + (f" (deadline {application.deadline})" if application.deadline else "")]
    lines += ["History:", *(f"- {local_date(e.at)} {STATUS_NAMES[e.status]}" + (f": {e.note}" if e.note else "")
                           for e in application.events)]
    return "\n".join(lines)


# After this many days with no news on an application, suggest following up.
FOLLOW_UP_DAYS = 21


@mcp.tool()
def list_applications(statuses: list[Status] | None = None) -> str:
    """The user's applications, grouped by where they are, with what needs doing: deadlines
    coming up, applications with no news for weeks, jobs taken off their board, documents made.

    Args:
        statuses: Only these statuses (default: all).
    """
    applications = _tracker().all(statuses)
    if not applications:
        return ("No applications tracked yet. save_resume and save_cover_letter track a job "
                "automatically; track_application tracks one directly.")
    store = _store()
    today = date.today()
    counts = Counter(a.status for a in applications)
    sections = ["Pipeline: " + ", ".join(f"{counts[s]} {STATUS_NAMES[s].lower()}"
                                         for s in STATUSES if counts[s])]
    for status in STATUSES:
        group = [a for a in applications if a.status == status]
        if not group:
            continue
        lines = [f"## {STATUS_NAMES[status]} ({len(group)})"]
        for a in group:
            days = a.days_since_update(today)
            docs = [name for name, path in (("resume", a.resume), ("cover letter", a.cover_letter)) if path]
            lines.append(f"- **{a.title}** · {a.company} · since {local_date(a.updated)} ({days} days)"
                         + (f" · made: {', '.join(docs)}" if docs else ""))
            todo = []
            if a.deadline:
                left = (date.fromisoformat(a.deadline) - today).days
                todo.append(f"deadline {a.deadline} " + (f"in {left} days" if left >= 0 else "has passed"))
            if status == "applied" and days >= FOLLOW_UP_DAYS:
                todo.append(f"no news in {days} days: consider a polite follow-up")
            if status == "interested" and docs:
                todo.append("documents ready: apply, then mark it applied")
            if status not in DONE:
                try:
                    if closed := store.get(a.job_id).closed:
                        todo.append(f"taken off the board on {closed[:10]}")
                except StoreError:
                    pass
            if todo:
                lines.append("  " + "; ".join(todo))
            lines.append(f"  id: {a.job_id} · {a.url}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)

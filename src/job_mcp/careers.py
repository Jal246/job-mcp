"""Reading early-career jobs from big companies' own career sites: Workday (NVIDIA, Salesforce,
Adobe, Intel, Cisco...), Amazon and Microsoft.

These sites have no official API, but their careers pages load jobs as JSON from public
endpoints, the same data anyone visiting the page sees. They're built for browsing, not bulk
reading: they hand out 10-20 jobs at a time, and descriptions take one more request per job.
So unlike the job-board APIs in boards.py, they're read politely and selectively:

- Only early-career jobs (internships, new grad, university roles) are read, found by searching
  the site for a few words and keeping the jobs whose titles say so.
- A job already in the database isn't fetched again; only new jobs' descriptions are.

fetch() reads any board, from these sites or the job-board APIs.

This module knows nothing about MCP, so it can be used and tested as plain Python.
"""

import asyncio
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

from job_mcp import boards, classify, net
from job_mcp.boards import Board, BoardError, Job

SITES = ("workday", "amazon", "microsoft")

# Titles of early-career jobs.
EARLY_CAREER = re.compile(
    r"\b(intern|interns|internship|internships|co-?op|new grad(uate)?s?|university|graduate|"
    r"early career|entry[- ]level|campus|apprentice(ship)?|student)\b", re.IGNORECASE)
# What to search each site for; the results are filtered by EARLY_CAREER.
SEARCHES = ("intern", "new grad", "university graduate")
# Stop paging through one search after this many pages, or after a page with no early-career
# jobs (results are sorted by relevance, so the rest won't have any either).
MAX_PAGES = 12
# Job descriptions fetched at the same time from one site.
CONCURRENT_DETAILS = 4


def early_career(title: str) -> bool:
    return bool(EARLY_CAREER.search(title))


async def _gather(tasks: list) -> list:
    """Run tasks CONCURRENT_DETAILS at a time; their results in order."""
    limit = asyncio.Semaphore(CONCURRENT_DETAILS)

    async def run(task):
        async with limit:
            return await task

    return await asyncio.gather(*(run(task) for task in tasks))


async def _paged(search, page_size: int) -> list[dict]:
    """Every early-career item from all SEARCHES, without repeats. search(text, offset) returns
    (items on that page, total results, a function giving an item's title, one giving its key)."""
    found: dict[str, dict] = {}
    for text in SEARCHES:
        for page in range(MAX_PAGES):
            items, total, title_of, key_of = await search(text, page * page_size)
            wanted = [item for item in items if early_career(title_of(item))]
            for item in wanted:
                found.setdefault(key_of(item), item)
            if not wanted or (page + 1) * page_size >= total:
                break
    return list(found.values())


# --- Workday ----------------------------------------------------------------------------------
# A Workday site is <tenant>.<wdN>.myworkdayjobs.com/<site>; its board name is "<tenant>.<wdN>/<site>"
# (NVIDIA's is "nvidia.wd5/NVIDIAExternalCareerSite").

WORKDAY_PAGE = 20


def _workday_parts(board: Board) -> tuple[str, str, str]:
    host, _, site = board.name.partition("/")
    tenant, _, server = host.partition(".")
    if not (tenant and server and site):
        raise BoardError(f"'{board.name}' isn't a Workday site (expected tenant.wdN/site)")
    return tenant, server, site


def _workday_api(board: Board) -> str:
    tenant, server, site = _workday_parts(board)
    return f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"


def _workday_id(board: Board, path: str) -> str:
    """A job's id, from its path ("/job/Santa-Clara/Software-Intern_JR2027176" -> its req id)."""
    return f"workday:{board.name}:{path.rsplit('_', 1)[-1]}"


def parse_workday_job(data: dict, board: Board, company: str, path: str) -> Job:
    info = data["jobPostingInfo"]
    tenant, server, site = _workday_parts(board)
    locations = [info.get("location"), *(info.get("additionalLocations") or [])]
    return boards.make_job(
        board, company, path.rsplit("_", 1)[-1], info.get("title", ""),
        info.get("externalUrl") or f"https://{tenant}.{server}.myworkdayjobs.com/{site}{path}",
        locations, classify.workplace(info.get("remoteType"), info.get("location")),
        info.get("timeType"), None, info.get("startDate"), None,
        classify.html_to_text(info.get("jobDescription") or ""))


async def fetch_workday(board: Board, company: str, known: Mapping[str, Job]) -> list[Job]:
    api = _workday_api(board)

    async def search(text: str, offset: int):
        data = await net.post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": WORKDAY_PAGE,
                                                   "offset": offset, "searchText": text})
        return (data.get("jobPostings") or [], data.get("total") or 0,
                lambda item: item.get("title", ""), lambda item: item["externalPath"])

    async def detail(path: str) -> Job:
        return parse_workday_job(await net.get_json(api + path), board, company, path)

    listed = await _paged(search, WORKDAY_PAGE)
    paths = [item["externalPath"] for item in listed]
    new = [path for path in paths if _workday_id(board, path) not in known]
    fetched = dict(zip(new, await _gather([detail(path) for path in new])))
    return [fetched.get(path) or known[_workday_id(board, path)] for path in paths]


# --- Amazon -----------------------------------------------------------------------------------
# amazon.jobs/en/search.json: whole jobs in each page (description and qualifications too).

AMAZON_SEARCH = "https://www.amazon.jobs/en/search.json"
AMAZON_PAGE = 100


def _amazon_date(text: str | None) -> str | None:
    """"October 30, 2026" -> "2026-10-30"."""
    try:
        return datetime.strptime(text or "", "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def parse_amazon_job(item: dict, board: Board, company: str) -> Job:
    # Qualifications come apart from the description, as "basic" (must-haves) and "preferred".
    parts = [classify.html_to_text(item.get("description") or "")]
    for heading, key in (("Basic qualifications", "basic_qualifications"),
                         ("Preferred qualifications", "preferred_qualifications")):
        if text := classify.html_to_text(item.get(key) or ""):
            parts.append(f"## {heading}\n" + "\n".join(
                line if line.startswith("- ") else f"- {line}" for line in text.splitlines() if line))
    intern = item.get("is_intern") or "intern" in (item.get("title") or "").lower()
    return boards.make_job(
        board, company, item.get("id_icims") or item["id"], item.get("title", ""),
        "https://www.amazon.jobs" + (item.get("job_path") or ""),
        [item.get("normalized_location") or item.get("location")], None,
        "Intern" if intern else item.get("job_schedule_type"), item.get("job_category"),
        _amazon_date(item.get("posted_date")), None, "\n\n".join(parts))


async def fetch_amazon(board: Board, company: str, known: Mapping[str, Job]) -> list[Job]:
    async def search(text: str, offset: int):
        data = await net.get_json(f"{AMAZON_SEARCH}?base_query={quote(text)}&result_limit="
                                  f"{AMAZON_PAGE}&offset={offset}&sort=recent")
        return (data.get("jobs") or [], data.get("hits") or 0,
                lambda item: item.get("title", ""), lambda item: str(item.get("id_icims") or item["id"]))

    return [parse_amazon_job(item, board, company) for item in await _paged(search, AMAZON_PAGE)]


# --- Microsoft --------------------------------------------------------------------------------
# apply.careers.microsoft.com: search pages of 10, and a details request for the description.

MICROSOFT_API = "https://apply.careers.microsoft.com/api/pcsx"
MICROSOFT_PAGE = 10


def _one(value) -> str | None:
    """A field that's sometimes a string and sometimes a list of them, as one string."""
    if isinstance(value, list):
        return ", ".join(map(str, value)) or None
    return value


def parse_microsoft_job(data: dict, board: Board, company: str) -> Job:
    info = data["data"]
    posted = info.get("postedTs") or info.get("creationTs")
    url = info.get("publicUrl") or f"https://apply.careers.microsoft.com{info.get('positionUrl', '')}"
    return boards.make_job(
        board, company, info.get("displayJobId") or info["id"], info.get("name", ""), url,
        info.get("standardizedLocations") or info.get("locations") or [],
        classify.workplace(_one(info.get("workLocationOption"))),
        _one(info.get("efcustomTextEmploymentType")), _one(info.get("department")),
        datetime.fromtimestamp(int(posted), UTC).date().isoformat() if posted else None, None,
        classify.html_to_text(info.get("jobDescription") or ""))


async def fetch_microsoft(board: Board, company: str, known: Mapping[str, Job]) -> list[Job]:
    async def search(text: str, offset: int):
        data = await net.get_json(f"{MICROSOFT_API}/search?domain=microsoft.com&query="
                                  f"{quote(text)}&start={offset}")
        data = data.get("data") or {}
        return (data.get("positions") or [], data.get("count") or 0,
                lambda item: item.get("name", ""), lambda item: str(item["id"]))

    async def detail(position: dict) -> Job:
        data = await net.get_json(f"{MICROSOFT_API}/position_details?position_id="
                                  f"{position['id']}&domain=microsoft.com&hl=en")
        return parse_microsoft_job(data, board, company)

    def job_id(position: dict) -> str:
        return f"microsoft:{board.name}:{position.get('displayJobId') or position['id']}"

    listed = await _paged(search, MICROSOFT_PAGE)
    new = [position for position in listed if job_id(position) not in known]
    fetched = {job_id(p): job for p, job in zip(new, await _gather([detail(p) for p in new]))}
    return [fetched.get(job_id(p)) or known[job_id(p)] for p in listed]


# --- Any board --------------------------------------------------------------------------------

FETCHERS = {"workday": fetch_workday, "amazon": fetch_amazon, "microsoft": fetch_microsoft}


async def fetch(board: Board, company: str, known: Mapping[str, Job] | None = None) -> list[Job]:
    """All the jobs a board has (early-career ones, for career sites). known: the jobs already
    saved for it, by id, so career sites only fetch what's new. Raises BoardError."""
    if board.source not in FETCHERS:
        return await boards.fetch_board(board, company)
    try:
        jobs = await FETCHERS[board.source](board, company, known or {})
    except net.NotFound:
        raise BoardError(f"there's no {boards.SOURCE_NAMES[board.source]} site called "
                         f"'{board.name}'") from None
    except net.NetError as error:
        if board.source == "workday" and error.status_code == 422:
            # Workday's answer for a site name it doesn't know.
            raise BoardError(f"there's no Workday site called '{board.name}' (copy the careers "
                             "page's address, like https://nvidia.wd5.myworkdayjobs.com/"
                             "NVIDIAExternalCareerSite)") from None
        raise BoardError(str(error)) from None
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise BoardError(f"{boards.SOURCE_NAMES[board.source]} answered in a format we don't "
                         f"understand ({error!r})") from None
    # A known job keeps its saved details but takes the company name it's watched under.
    return [job if job.company == company else replace(job, company=company) for job in jobs]


def parse_site(text: str) -> Board | None:
    """The career site a URL or "source:name" points at: a Workday careers page
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"), "workday:nvidia.wd5/
    NVIDIAExternalCareerSite", "amazon" or "microsoft" (as names or URLs). None otherwise."""
    text = text.strip()
    lowered = text.lower()
    if lowered in ("amazon", "amazon:amazon") or "amazon.jobs" in lowered:
        return Board("amazon", "amazon")
    if lowered in ("microsoft", "microsoft:microsoft") or "careers.microsoft.com" in lowered:
        return Board("microsoft", "microsoft")
    if lowered.startswith("workday:"):
        return Board("workday", text.split(":", 1)[1].strip())
    parts = urlsplit(text if "://" in text else "https://" + text)
    host = (parts.hostname or "").lower()
    if host.endswith(".myworkdayjobs.com"):
        tenant, server = host.split(".")[:2]
        path = [p for p in parts.path.split("/") if p and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        if path and path[0] not in ("wday", "job"):
            return Board("workday", f"{tenant}.{server}/{path[0]}")
    return None

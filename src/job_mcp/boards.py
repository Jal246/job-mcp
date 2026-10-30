"""Reading jobs from the public job-board APIs that thousands of companies post their jobs on:
Greenhouse, Lever and Ashby.

These APIs are free, need no key, and are meant to be read: companies use them to show jobs on
their own careers pages. Each one answers in its own format, so each has a parse_ function that
turns its answer into the same Job shape.

A company's board is found by its "board name" (Stripe's is "stripe": boards.greenhouse.io/stripe).
detect() finds a company's board by trying its name on all three.

This module knows nothing about MCP, so it can be used and tested as plain Python.
"""

import asyncio
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from job_mcp import classify, net

Source = Literal["greenhouse", "lever", "ashby"]
SOURCES: tuple[Source, ...] = ("greenhouse", "lever", "ashby")
SOURCE_NAMES = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
                # Big companies' own career sites, read by careers.py.
                "workday": "Workday", "amazon": "Amazon Jobs", "microsoft": "Microsoft Careers"}

API_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}?includeCompensation=true",
}
# The careers-page addresses people paste, and which source each belongs to.
BOARD_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "boards-api.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "api.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "api.ashbyhq.com": "ashby",
}


class BoardError(Exception):
    """A board that couldn't be found or read, with a message the user can read."""


@dataclass(frozen=True)
class Job:
    id: str  # "<source>:<board>:<the board's own id>", unique across all boards.
    company: str
    title: str
    url: str  # The posting, where you apply.
    locations: tuple[str, ...]
    workplace: str | None  # "remote", "hybrid", "onsite", or None if the board doesn't say.
    level: str  # One of classify.LEVELS.
    employment_type: str | None  # "Full-time", "Intern", "Contract"... when the board says.
    department: str | None
    posted: str | None  # YYYY-MM-DD.
    salary: str | None  # As the board or description writes it, e.g. "$87,069 - $124,385".
    description: str  # Plain text.


@dataclass(frozen=True)
class Board:
    source: Source
    name: str  # The board's name on that source, e.g. "stripe".


def _date(value: str | int | None) -> str | None:
    """An ISO timestamp ("2026-09-03T13:30:34-04:00") or milliseconds since 1970, as YYYY-MM-DD."""
    if isinstance(value, int):
        return datetime.fromtimestamp(value / 1000, UTC).date().isoformat()
    return value[:10] if value else None


def _split_locations(text: str | None) -> list[str]:
    """"San Francisco, CA; New York, NY" -> ["San Francisco, CA", "New York, NY"]."""
    return [part.strip() for part in re.split(r"\s*[;|]\s*|\s+or\s+", text or "") if part.strip()]


def _unique(items) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in items if item))


def make_job(board: Board, company: str, native_id, title: str, url: str, locations,
         workplace: str | None, employment_type: str | None, department: str | None,
         posted: str | None, salary: str | None, description: str) -> Job:
    """A Job, with the parts every source works out the same way filled in."""
    title = " ".join(title.split())
    return Job(
        id=f"{board.source}:{board.name}:{native_id}",
        company=company,
        title=title,
        url=url,
        locations=_unique(locations),
        workplace=workplace,
        level=classify.level(title, employment_type),
        employment_type=employment_type,
        department=department,
        posted=posted,
        salary=salary or classify.salary_in(description),
        description=description,
    )


# --- Greenhouse -------------------------------------------------------------------------------

def parse_greenhouse(data: dict, board: Board, company: str) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        # The description is HTML that has been escaped once more ("&lt;p&gt;"), so unescape
        # it before reading it as HTML.
        description = classify.html_to_text(html.unescape(item.get("content") or ""))
        departments = item.get("departments") or [{}]
        # Some companies number their departments ("8611 Security Analytics").
        department = re.sub(r"^\d+\s+", "", departments[0].get("name") or "") or None
        jobs.append(make_job(
            board, company, item["id"], item.get("title", ""), item.get("absolute_url", ""),
            _split_locations(location), classify.workplace(location), None, department,
            _date(item.get("first_published") or item.get("updated_at")), None, description,
        ))
    return jobs


# --- Lever ------------------------------------------------------------------------------------

def _lever_salary(item: dict) -> str | None:
    """Lever's salaryRange ({"currency": "USD", "interval": "per-year-salary", "min": 120000,
    "max": 150000}) as "USD 120,000 - 150,000 per year"."""
    pay = item.get("salaryRange") or {}
    if pay.get("min") is None or pay.get("max") is None:
        return None
    text = f"{pay.get('currency', '')} {classify.money(pay['min'])} - {classify.money(pay['max'])}"
    interval = (pay.get("interval") or "").removesuffix("-salary").replace("-", " ")
    return f"{text.strip()} {interval}".strip()


def parse_lever(data: list, board: Board, company: str) -> list[Job]:
    jobs = []
    for item in data:
        categories = item.get("categories") or {}
        locations = [categories.get("location"), *(categories.get("allLocations") or [])]
        # The description is in parts: the intro, then lists ("What you'll do", "About you"...),
        # then a closing part (often with the pay range).
        parts = [item.get("descriptionPlain") or ""]
        for section in item.get("lists") or []:
            parts.append(f"## {section.get('text', '')}\n"
                         + classify.html_to_text(section.get("content") or ""))
        parts.append(item.get("additionalPlain") or "")
        description = "\n\n".join(part.strip() for part in parts if part.strip())
        jobs.append(make_job(
            board, company, item["id"], item.get("text", ""), item.get("hostedUrl", ""), locations,
            classify.workplace(item.get("workplaceType"), categories.get("location")),
            categories.get("commitment"), categories.get("team") or categories.get("department"),
            _date(item.get("createdAt")), _lever_salary(item), description,
        ))
    return jobs


# --- Ashby ------------------------------------------------------------------------------------

# Ashby's employmentType values, as people write them.
ASHBY_EMPLOYMENT = {"FullTime": "Full-time", "PartTime": "Part-time", "Intern": "Intern",
                    "Contract": "Contract", "Temporary": "Temporary"}


def parse_ashby(data: dict, board: Board, company: str) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue  # Hidden from the careers page; only reachable by direct link.
        locations = [item.get("location"),
                     *((extra or {}).get("location") for extra in item.get("secondaryLocations") or [])]
        compensation = item.get("compensation") or {}
        salary = (compensation.get("scrapeableCompensationSalarySummary")
                  or compensation.get("compensationTierSummary"))
        employment = item.get("employmentType")
        jobs.append(make_job(
            board, company, item["id"], item.get("title", ""), item.get("jobUrl", ""), locations,
            "remote" if item.get("isRemote") else classify.workplace(item.get("workplaceType"),
                                                                     item.get("location")),
            ASHBY_EMPLOYMENT.get(employment, employment), item.get("department") or item.get("team"),
            _date(item.get("publishedAt")), salary,
            item.get("descriptionPlain") or classify.html_to_text(item.get("descriptionHtml") or ""),
        ))
    return jobs


PARSERS = {"greenhouse": parse_greenhouse, "lever": parse_lever, "ashby": parse_ashby}


async def fetch_board(board: Board, company: str) -> list[Job]:
    """All open jobs on a board. Raises BoardError if it can't be read."""
    url = API_URLS[board.source].format(board.name)
    try:
        data = await net.get_json(url)
        return PARSERS[board.source](data, board, company)
    except net.NotFound:
        raise BoardError(f"there's no {SOURCE_NAMES[board.source]} board called "
                         f"'{board.name}'") from None
    except net.NetError as error:
        raise BoardError(str(error)) from None
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise BoardError(f"{SOURCE_NAMES[board.source]} answered in a format we don't "
                         f"understand ({error!r})") from None


# --- Finding a company's board ----------------------------------------------------------------

def parse_board(text: str) -> Board | None:
    """The board a careers-page URL ("https://jobs.lever.co/spotify") or "source:name"
    ("lever:spotify") points at, or None if text is just a company name."""
    text = text.strip()
    source, _, name = text.partition(":")
    if source.lower() in SOURCES and name and not name.startswith("//"):
        return Board(source.lower(), name.strip().lower())
    if "/" not in text:
        return None
    parts = urlsplit(text if "://" in text else "https://" + text)
    source = BOARD_HOSTS.get((parts.hostname or "").lower())
    path = [part for part in parts.path.split("/") if part]
    # The API addresses have the board name further along (/v1/boards/<name>/jobs).
    for prefix in ("v1", "boards", "v0", "postings", "posting-api", "job-board"):
        if path and path[0] == prefix:
            path.pop(0)
    if not source or not path:
        return None
    return Board(source, path[0].lower())


def guesses(company: str) -> list[str]:
    """Board names a company probably uses: "Scale AI" -> ["scaleai", "scale-ai"]."""
    words = re.findall(r"[a-z0-9]+", company.lower())
    return list(dict.fromkeys(["".join(words), "-".join(words)])) if words else []


@dataclass(frozen=True)
class Found:
    board: Board
    jobs: list[Job]


async def detect(company: str, display_name: str) -> Found:
    """Find a company's board by trying likely board names on every source. If several exist
    (some companies left an old board behind), the one with the most jobs wins.

    Raises BoardError if none is found.
    """
    tries = [Board(source, name) for name in guesses(company) for source in SOURCES]
    if not tries:
        raise BoardError(f"'{company}' isn't a company name")
    results = await asyncio.gather(*(fetch_board(board, display_name) for board in tries),
                                   return_exceptions=True)
    found = [Found(board, jobs) for board, jobs in zip(tries, results) if isinstance(jobs, list)]
    if found:
        return max(found, key=lambda f: len(f.jobs))
    # Report real problems (a board that's down), not the expected "no such board" answers.
    problems = {str(error) for error in results if isinstance(error, BoardError)
                and "there's no" not in str(error)}
    if problems:
        raise BoardError(f"couldn't check every job board for '{company}': "
                         + "; ".join(sorted(problems)))
    raise BoardError(
        f"couldn't find '{company}' on Greenhouse, Lever or Ashby (tried "
        f"{', '.join(guesses(company))}). If it has a careers page on one of them, pass its URL "
        "(like https://jobs.lever.co/<name>); otherwise it uses a job board we can't read yet."
    )

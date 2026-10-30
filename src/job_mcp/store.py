"""The jobs database: the companies you watch, and every job seen on their boards.

It's one SQLite file. Jobs are searched with SQLite's built-in full-text search (FTS5), which
ranks matches with BM25 (the classic search-engine formula) and matches word forms ("engineer"
finds "engineering"). Each refresh compares a board with what we saw last time, so we know which
jobs are new and which have closed.

This module knows nothing about MCP, so it can be used and tested as plain Python.
"""

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from job_mcp.boards import Board, Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    name TEXT PRIMARY KEY COLLATE NOCASE,
    source TEXT NOT NULL,
    board TEXT NOT NULL,
    added TEXT NOT NULL,
    refreshed TEXT,          -- When its jobs were last fetched (UTC, ISO format).
    error TEXT,              -- Why the last refresh failed, if it did.
    UNIQUE (source, board)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    company TEXT NOT NULL COLLATE NOCASE REFERENCES companies (name) ON DELETE CASCADE,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    locations TEXT NOT NULL, -- Joined with LOCATION_SEPARATOR.
    workplace TEXT,
    level TEXT NOT NULL,
    employment_type TEXT,
    department TEXT,
    posted TEXT,
    salary TEXT,
    description TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    closed TEXT              -- When it disappeared from the board, or NULL while it's open.
);
CREATE INDEX IF NOT EXISTS jobs_by_company ON jobs (company, closed);

-- The full-text index, kept in step with the jobs table by the triggers below.
CREATE VIRTUAL TABLE IF NOT EXISTS jobs_text USING fts5 (
    title, company, department, locations, description,
    content = 'jobs', content_rowid = 'rowid', tokenize = 'porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS jobs_added AFTER INSERT ON jobs BEGIN
    INSERT INTO jobs_text (rowid, title, company, department, locations, description)
    VALUES (new.rowid, new.title, new.company, new.department, new.locations, new.description);
END;
CREATE TRIGGER IF NOT EXISTS jobs_removed AFTER DELETE ON jobs BEGIN
    INSERT INTO jobs_text (jobs_text, rowid, title, company, department, locations, description)
    VALUES ('delete', old.rowid, old.title, old.company, old.department, old.locations,
            old.description);
END;
CREATE TRIGGER IF NOT EXISTS jobs_changed AFTER UPDATE OF title, company, department, locations,
        description ON jobs BEGIN
    INSERT INTO jobs_text (jobs_text, rowid, title, company, department, locations, description)
    VALUES ('delete', old.rowid, old.title, old.company, old.department, old.locations,
            old.description);
    INSERT INTO jobs_text (rowid, title, company, department, locations, description)
    VALUES (new.rowid, new.title, new.company, new.department, new.locations, new.description);
END;
"""

LOCATION_SEPARATOR = " · "

# How much a match in each column counts when ranking (title, company, department, locations,
# description): a word in the title matters far more than the same word deep in the description.
COLUMN_WEIGHTS = (10.0, 3.0, 3.0, 2.0, 1.0)

# Places with more than one name. Searching for any of them finds them all.
PLACE_NAMES = [
    {"bangalore", "bengaluru"}, {"gurgaon", "gurugram"}, {"bombay", "mumbai"},
    {"madras", "chennai"}, {"calcutta", "kolkata"}, {"new delhi", "delhi"},
    {"sf", "san francisco"}, {"nyc", "new york"}, {"uk", "united kingdom"},
    {"bay area", "sf bay area", "san francisco bay area"},
]
# Boards often write just the city ("Bangalore", not "Bangalore, India"), so searching for a
# country (or region) also searches for its main tech cities.
COUNTRY_CITIES = {
    "bay area": ["san francisco", "oakland", "san jose", "palo alto", "mountain view",
                 "menlo park", "sunnyvale", "san mateo", "foster city", "redwood city",
                 "santa clara", "cupertino", "berkeley"],
    "india": ["bengaluru", "bangalore", "mumbai", "pune", "hyderabad", "chennai", "delhi",
              "gurugram", "gurgaon", "noida", "kolkata", "ahmedabad"],
    "united kingdom": ["london", "manchester", "edinburgh", "cambridge"],
    "canada": ["toronto", "vancouver", "montreal", "waterloo"],
    "germany": ["berlin", "munich", "hamburg"],
    "singapore": [],
    "australia": ["sydney", "melbourne"],
}


class StoreError(Exception):
    """A problem the user should hear about, like a company that isn't on the watch list."""


@dataclass(frozen=True)
class Company:
    name: str
    board: Board
    added: str
    refreshed: str | None
    error: str | None
    open_jobs: int


@dataclass(frozen=True)
class StoredJob:
    job: Job
    first_seen: str
    closed: str | None


@dataclass(frozen=True)
class Listing:
    """One search result: a job, plus the same job posted again for other places."""
    job: StoredJob
    also: list[StoredJob]

    @property
    def locations(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(place for stored in [self.job, *self.also]
                                   for place in stored.job.locations))


@dataclass(frozen=True)
class Update:
    """What a refresh found on a board."""
    new: list[Job]
    closed: int
    open: int


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fts_query(text: str) -> str:
    """Words typed by a person, as an FTS5 query that needs every word: `react "new grad"` ->
    `"react" "new grad"`. Quoting makes FTS5 read them as words, never as its own syntax
    (so "C++" or "front-end" can't break the query)."""
    phrases = re.findall(r'"([^"]*)"', text)
    words = re.sub(r'"[^"]*"', " ", text).split()
    terms = [" ".join(re.findall(r"\w+", term)) for term in [*phrases, *words]]
    return " ".join(f'"{term}"' for term in terms if term)


def place_names(place: str) -> set[str]:
    """Every name to look for when searching for a place: "Bangalore" -> {"bangalore",
    "bengaluru"}; "UK" -> {"uk", "united kingdom", "london", ...}."""
    place = " ".join(place.lower().split())
    names = next((names for names in PLACE_NAMES if place in names), {place})
    return names.union(*(COUNTRY_CITIES.get(name, []) for name in names))


def title_matches(query: str, title: str) -> int:
    """How many of the query's words are in the title (a word counts if a title word starts
    with its first 5 letters, so "engineer" matches "engineering")."""
    title_words = re.findall(r"\w+", title.lower())
    return sum(any(word.startswith(term[:5]) for word in title_words)
               for term in re.findall(r"\w+", query.lower()))


def same_role(job: Job) -> tuple:
    """Jobs that are the same role at the same company, posted separately for each city: the
    same title once place names are taken out ("Software Engineer - London" and "Software
    Engineer (Dublin)" are both "software engineer")."""
    title = job.title.lower()
    for place in job.locations:
        for name in (place.lower(), place.split(",")[0].strip().lower()):
            if name:
                title = title.replace(name, " ")
    title = re.sub(r"\b(remote|hybrid)\b", " ", title)
    return job.company.lower(), " ".join(re.findall(r"[\w+#]+", title)), job.level


def _job(row: sqlite3.Row) -> StoredJob:
    return StoredJob(Job(
        id=row["id"], company=row["company"], title=row["title"], url=row["url"],
        locations=tuple(filter(None, row["locations"].split(LOCATION_SEPARATOR))),
        workplace=row["workplace"], level=row["level"], employment_type=row["employment_type"],
        department=row["department"], posted=row["posted"], salary=row["salary"],
        description=row["description"],
    ), row["first_seen"], row["closed"])


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    # --- The watch list -----------------------------------------------------------------------

    def add_company(self, name: str, board: Board) -> None:
        """Watch a company's board. Raises StoreError if it's already watched."""
        with closing(self._connect()) as db, db:
            if old := db.execute("SELECT name FROM companies WHERE name = ? OR (source = ? AND "
                                 "board = ?)", (name, board.source, board.name)).fetchone():
                raise StoreError(f"already watching {old['name']}")
            db.execute("INSERT INTO companies (name, source, board, added) VALUES (?, ?, ?, ?)",
                       (name, board.source, board.name, now()))

    def remove_company(self, name: str) -> Company:
        """Stop watching a company, and forget its jobs. Raises StoreError if it isn't watched."""
        company = self.company(name)
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM companies WHERE name = ?", (name,))
        return company

    def company(self, name: str) -> Company:
        for company in self.companies():
            if company.name.lower() == name.strip().lower():
                return company
        raise StoreError(f"not watching '{name}' (list_companies shows who is)")

    def companies(self) -> list[Company]:
        with closing(self._connect()) as db:
            rows = db.execute("""
                SELECT companies.*, count(jobs.id) AS open_jobs FROM companies
                LEFT JOIN jobs ON jobs.company = companies.name AND jobs.closed IS NULL
                GROUP BY companies.name ORDER BY companies.name
            """).fetchall()
        return [Company(row["name"], Board(row["source"], row["board"]), row["added"],
                        row["refreshed"], row["error"], row["open_jobs"]) for row in rows]

    def refresh_failed(self, name: str, error: str) -> None:
        """Remember that a board couldn't be read (its jobs are kept as they were)."""
        with closing(self._connect()) as db, db:
            db.execute("UPDATE companies SET refreshed = ?, error = ? WHERE name = ?",
                       (now(), error, name))

    # --- Jobs ---------------------------------------------------------------------------------

    def update(self, name: str, jobs: list[Job]) -> Update:
        """Save what's on a company's board now: add new jobs, update changed ones, and mark
        jobs that have gone as closed. A closed job that comes back is open again."""
        when = now()
        with closing(self._connect()) as db, db:
            known = {row["id"]: row["closed"] for row in
                     db.execute("SELECT id, closed FROM jobs WHERE company = ?", (name,))}
            fresh = {job.id: job for job in jobs}  # A board can list the same job twice.
            for job in fresh.values():
                values = (job.id, name, job.title, job.url,
                          LOCATION_SEPARATOR.join(job.locations), job.workplace, job.level,
                          job.employment_type, job.department, job.posted, job.salary,
                          job.description, when)
                # Only touch rows that changed, so the full-text index isn't rebuilt each time.
                db.execute("""
                    INSERT INTO jobs (id, company, title, url, locations, workplace, level,
                        employment_type, department, posted, salary, description, first_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        title = excluded.title, url = excluded.url, locations = excluded.locations,
                        workplace = excluded.workplace, level = excluded.level,
                        employment_type = excluded.employment_type,
                        department = excluded.department, posted = excluded.posted,
                        salary = excluded.salary, description = excluded.description, closed = NULL
                    WHERE (title, url, locations, workplace, level, employment_type, department,
                           posted, salary, description, closed)
                        IS NOT (excluded.title, excluded.url, excluded.locations,
                                excluded.workplace, excluded.level, excluded.employment_type,
                                excluded.department, excluded.posted, excluded.salary,
                                excluded.description, NULL)
                """, values)
            gone = [job_id for job_id, closed in known.items() if closed is None and job_id not in fresh]
            db.executemany("UPDATE jobs SET closed = ? WHERE id = ?", [(when, i) for i in gone])
            db.execute("UPDATE companies SET refreshed = ?, error = NULL WHERE name = ?",
                       (when, name))
        # New: never seen before, or closed and now back.
        new = [job for job_id, job in fresh.items() if known.get(job_id, "gone") is not None]
        return Update(new=new, closed=len(gone), open=len(fresh))

    def get(self, job_id: str) -> StoredJob:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id.strip(),)).fetchone()
        if not row:
            raise StoreError(f"no job with id '{job_id}' (search_jobs gives each job's id)")
        return _job(row)

    def search(self, query: str = "", *, places: list[str] | None = None,
               remote_only: bool = False, levels: list[str] | None = None,
               companies: list[str] | None = None, posted_within_days: int | None = None,
               new_within_days: int | None = None, include_closed: bool = False,
               sort: str = "relevance", limit: int = 20) -> tuple[list[Listing], int]:
        """Jobs matching every filter given, best first, with the same role posted for several
        cities merged into one Listing. Returns up to limit listings, and how many jobs matched
        in all.

        places matches any of them, in the job's locations ("India" finds "Bengaluru, India";
        "Remote" also finds jobs the board marks remote). sort is "relevance" (needs a query;
        without one it's "newest") or "newest" (by posting date).
        """
        where, params = [], []
        match = fts_query(query)
        if match:
            where.append("jobs_text MATCH ?")
            params.append(match)
        if not include_closed:
            where.append("jobs.closed IS NULL")
        if places:
            ors = []
            for place in places:
                for name in place_names(place):
                    ors.append("jobs.locations LIKE ?")
                    params.append(f"%{name}%")
                if place.strip().lower() == "remote":
                    ors.append("jobs.workplace = 'remote'")
            where.append(f"({' OR '.join(ors)})")
        if remote_only:
            where.append("(jobs.workplace = 'remote' OR jobs.locations LIKE '%remote%')")
        if levels:
            where.append(f"jobs.level IN ({', '.join('?' * len(levels))})")
            params += levels
        if companies:
            where.append(f"jobs.company IN ({', '.join('?' * len(companies))})")
            params += [name.strip() for name in companies]
        if posted_within_days is not None:
            where.append("jobs.posted >= ?")
            params.append((date.today() - timedelta(days=posted_within_days)).isoformat())
        if new_within_days is not None:
            where.append("jobs.first_seen >= ?")
            params.append((datetime.now(UTC) - timedelta(days=new_within_days))
                          .isoformat(timespec="seconds"))

        source = "jobs JOIN jobs_text ON jobs_text.rowid = jobs.rowid" if match else "jobs"
        conditions = f"WHERE {' AND '.join(where)}" if where else ""
        if match and sort == "relevance":
            order = f"bm25(jobs_text, {', '.join(map(str, COLUMN_WEIGHTS))})"
        else:
            order = "jobs.posted DESC NULLS LAST, jobs.first_seen DESC"
        with closing(self._connect()) as db:
            total = db.execute(f"SELECT count(*) FROM {source} {conditions}", params).fetchone()[0]
            # Fetch extra rows: several may merge into one listing.
            rows = db.execute(f"SELECT jobs.* FROM {source} {conditions} ORDER BY {order} "
                              "LIMIT ?", [*params, limit * 10]).fetchall()

        if match and sort == "relevance":
            # BM25 can rank a job that mentions every word in its description above one with
            # most of them in its title; put title matches first (sorted() keeps BM25's order
            # among equals).
            rows = sorted(rows, key=lambda row: -title_matches(query, row["title"]))
        listings: dict[tuple, Listing] = {}
        for row in rows:
            stored = _job(row)
            key = same_role(stored.job)
            if key in listings:
                listings[key].also.append(stored)
            elif len(listings) < limit:
                listings[key] = Listing(stored, [])
        return list(listings.values()), total

    def counts(self) -> dict[str, int]:
        """How many open jobs there are at each level."""
        with closing(self._connect()) as db:
            return dict(db.execute("SELECT level, count(*) FROM jobs WHERE closed IS NULL "
                                   "GROUP BY level").fetchall())

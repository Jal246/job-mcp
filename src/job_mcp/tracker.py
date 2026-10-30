"""Your applications: which jobs you're interested in, have applied to, are interviewing for...
with the history of each and the documents made for it.

Applications keep their own copy of the job's company, title and link, so removing a company
from the watch list never loses your application history. They live in the same SQLite file as
the jobs.

This module knows nothing about MCP, so it can be used and tested as plain Python.
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from job_mcp.boards import Job

Status = Literal["interested", "applied", "oa", "interview", "offer", "rejected", "withdrawn"]
STATUSES: tuple[Status, ...] = Status.__args__
STATUS_NAMES = {"interested": "Interested", "applied": "Applied", "oa": "Online assessment",
                "interview": "Interviewing", "offer": "Offer", "rejected": "Rejected",
                "withdrawn": "Withdrawn"}
# Applications in these states are finished; the rest are still going.
DONE = {"offer", "rejected", "withdrawn"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,     -- When the status last changed.
    deadline TEXT,             -- YYYY-MM-DD, e.g. when an online assessment is due.
    resume TEXT,               -- Paths of the documents made for it.
    cover_letter TEXT
);
CREATE TABLE IF NOT EXISTS application_events (
    job_id TEXT NOT NULL REFERENCES applications (job_id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
"""


class TrackerError(Exception):
    """A problem the user should hear about."""


@dataclass(frozen=True)
class Event:
    at: str
    status: str
    note: str


@dataclass(frozen=True)
class Application:
    job_id: str
    company: str
    title: str
    url: str
    status: str
    created: str
    updated: str
    deadline: str | None
    resume: str | None
    cover_letter: str | None
    events: tuple[Event, ...] = ()

    def days_since_update(self, today: date | None = None) -> int:
        return ((today or date.today()) - local_date(self.updated)).days


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def local_date(timestamp: str) -> date:
    """The user's own date for a stored UTC time (it can already be tomorrow in UTC)."""
    return datetime.fromisoformat(timestamp).astimezone().date()


class Tracker:
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

    def track(self, job: Job, status: Status, note: str = "", deadline: str | None = None) -> Application:
        """Start tracking a job, or move it to a new status (with a note for the history)."""
        if status not in STATUSES:
            raise TrackerError(f"status must be one of {', '.join(STATUSES)}")
        if deadline:
            try:
                date.fromisoformat(deadline)
            except ValueError:
                raise TrackerError("deadline must be a date like 2026-11-15") from None
        when = now()
        with closing(self._connect()) as db, db:
            old = db.execute("SELECT status FROM applications WHERE job_id = ?", (job.id,)).fetchone()
            if old is None:
                db.execute("""INSERT INTO applications (job_id, company, title, url, status, created,
                                  updated, deadline) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                           (job.id, job.company, job.title, job.url, status, when, when, deadline))
            else:
                db.execute("""UPDATE applications SET status = ?, deadline = coalesce(?, deadline),
                                  updated = CASE WHEN status = ? THEN updated ELSE ? END
                              WHERE job_id = ?""", (status, deadline, status, when, job.id))
            if old is None or old["status"] != status or note:
                db.execute("INSERT INTO application_events (job_id, at, status, note) VALUES (?, ?, ?, ?)",
                           (job.id, when, status, note.strip()))
        return self.get(job.id)

    def attach(self, job: Job, resume: str | None = None, cover_letter: str | None = None) -> Application:
        """Remember the documents made for a job (tracking it as "interested" if it isn't yet)."""
        if not self.find(job.id):
            self.track(job, "interested", "Documents made")
        with closing(self._connect()) as db, db:
            db.execute("""UPDATE applications SET resume = coalesce(?, resume),
                              cover_letter = coalesce(?, cover_letter) WHERE job_id = ?""",
                       (resume, cover_letter, job.id))
        return self.get(job.id)

    def find(self, job_id: str) -> Application | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            events = db.execute("SELECT at, status, note FROM application_events WHERE job_id = ? "
                                "ORDER BY at, rowid", (job_id,)).fetchall()
        return Application(**dict(row), events=tuple(Event(**dict(e)) for e in events))

    def get(self, job_id: str) -> Application:
        if application := self.find(job_id):
            return application
        raise TrackerError(f"not tracking '{job_id}' (track_application starts tracking a job)")

    def all(self, statuses: list[str] | None = None) -> list[Application]:
        """Applications, most recently updated first."""
        with closing(self._connect()) as db:
            ids = [row[0] for row in db.execute("SELECT job_id FROM applications ORDER BY updated DESC")]
        found = [self.get(job_id) for job_id in ids]
        return [a for a in found if not statuses or a.status in statuses]

    def statuses(self) -> dict[str, str]:
        """Each tracked job's status, by job id."""
        with closing(self._connect()) as db:
            return dict(db.execute("SELECT job_id, status FROM applications").fetchall())

from dataclasses import replace
from datetime import date

import pytest

from job_mcp.tracker import Tracker, TrackerError, local_date

from test_fit import job


@pytest.fixture
def tracker(tmp_path) -> Tracker:
    return Tracker(tmp_path / "jobs.db")


def test_track_and_move_along(tracker):
    posting = job()
    first = tracker.track(posting, "interested")
    assert (first.status, first.company, first.title) == ("interested", "Acme", "Software Engineering Intern")
    tracker.track(posting, "applied", "Applied with tailored resume")
    tracker.track(posting, "applied")  # Same status, no note: nothing new for the history.
    later = tracker.track(posting, "oa", "HackerRank: 2 questions", deadline="2026-11-15")
    assert later.status == "oa" and later.deadline == "2026-11-15"
    assert [(e.status, e.note) for e in later.events] == [
        ("interested", ""), ("applied", "Applied with tailored resume"), ("oa", "HackerRank: 2 questions")]
    assert tracker.statuses() == {posting.id: "oa"}


def test_attach_documents(tracker):
    posting = job()
    application = tracker.attach(posting, resume="r.pdf")
    assert (application.status, application.resume, application.cover_letter) == ("interested", "r.pdf", None)
    application = tracker.attach(posting, cover_letter="c.pdf")
    assert (application.resume, application.cover_letter) == ("r.pdf", "c.pdf")
    assert [e.note for e in application.events] == ["Documents made"]


def test_all_and_filters(tracker):
    tracker.track(job(), "applied")
    tracker.track(replace(job(), id="greenhouse:acme:2"), "rejected")
    assert sorted(a.status for a in tracker.all()) == ["applied", "rejected"]
    assert [a.status for a in tracker.all(["applied"])] == ["applied"]


def test_errors(tracker):
    with pytest.raises(TrackerError, match="status must be one of"):
        tracker.track(job(), "hired")
    with pytest.raises(TrackerError, match="deadline must be a date"):
        tracker.track(job(), "oa", deadline="next friday")
    with pytest.raises(TrackerError, match="not tracking"):
        tracker.get("nope")
    assert tracker.find("nope") is None


def test_days_since_update(tracker):
    application = tracker.track(job(), "applied")
    today = local_date(application.updated)
    assert application.days_since_update(today) == 0
    assert application.days_since_update(date.fromordinal(today.toordinal() + 21)) == 21

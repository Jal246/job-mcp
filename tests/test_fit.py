from dataclasses import replace
from datetime import date

import pytest

from job_mcp.boards import Job
from job_mcp.fit import assess, eligibility, freshness, in_country, role_kind
from job_mcp.profile import Preferences

from test_profile import ada


def job(title: str = "Software Engineering Intern", description: str = "We use Python and Go.",
        locations: tuple[str, ...] = ("London",), level: str = "intern",
        posted: str | None = "2026-10-20") -> Job:
    return Job(id="greenhouse:acme:1", company="Acme", title=title, url="https://acme.example/1",
               locations=locations, workplace=None, level=level, employment_type=None,
               department=None, posted=posted, salary=None, description=description)


@pytest.mark.parametrize("title, kind", [
    ("Software Engineering Intern", "software"),
    ("Backend Engineer, New Grad", "software"),
    ("Machine Learning Engineer Intern", "ml"),
    ("Applied Scientist Intern", "ml"),
    ("Data Engineer Intern", "data"),
    ("Security Engineer Intern", "security"),
    ("Platform Engineer", "infrastructure"),
    ("Product Design Intern", "design"),
    ("Associate Product Manager Intern", "product"),
    ("Revenue Operations Intern", "business"),
    ("Cohort 0", "other"),
])
def test_role_kind(title, kind):
    assert role_kind(title) == kind


@pytest.mark.parametrize("place, country, expected", [
    ("San Francisco, CA", "United States", True),
    ("Remote, US", "United States", True),
    ("New York City", "United States", True),
    ("Vienna, Austria", "United States", False),
    ("Remote", "United States", None),
    ("Hybrid - San Francisco", "United States", True),
    ("Bengaluru, India", "India", True),
    ("London", "India", False),
])
def test_in_country(place, country, expected):
    assert in_country(place, country) is expected


def blockers_of(posting: Job, **preferences) -> list[str]:
    me = ada()
    me.preferences = me.preferences.model_copy(update=preferences)
    return eligibility(posting, me)[0]


def test_eligibility_degree():
    assert blockers_of(job("PhD Intern, Software Engineer")) == ["for PhD students"]
    assert blockers_of(job("Master's or PhD ML Intern")) == ["for Master's or PhD students"]
    assert blockers_of(job("High School Internship, Software Engineering")) == ["for high school students"]
    assert blockers_of(job("Bachelor's or Master's Software Intern")) == []


def test_eligibility_season_and_year():
    assert blockers_of(job("Software Engineering Intern - Winter '27")) == [
        "a winter internship (you want Summer 2027)"]
    assert blockers_of(job("Software Engineer, Intern (Summer or Winter)")) == []
    assert blockers_of(job("Software Engineering Intern (Summer 2026)")) == [
        "for 2026 (you want Summer 2027)"]  # The right season, a year early.
    assert blockers_of(job("Software Engineering Intern 2026")) == ["for 2026 (you want Summer 2027)"]
    assert blockers_of(job("Software Engineering Intern (2027 Start)")) == []
    # Seasons only count for internships.
    assert blockers_of(job("Software Engineer, Winter Team", level="mid")) == []


def test_eligibility_country():
    assert blockers_of(job(locations=("Vienna, Austria",)), work_countries=["United States"]) == [
        "based in Vienna, Austria, outside United States"]
    assert blockers_of(job(locations=("Vienna, Austria", "Remote, US")), work_countries=["United States"]) == []
    assert blockers_of(job(locations=("Remote",)), work_countries=["United States"]) == []


def test_eligibility_role_kind():
    assert blockers_of(job("Product Design Intern")) == ["a design role, not software/data/ml"]
    assert blockers_of(job("Cohort 0")) == []


def test_eligibility_warnings():
    me = ada()
    _, warnings = eligibility(job(description="Students graduating in December 2027 are eligible."), me)
    assert warnings == ["asks for graduation in 2027 (you graduate 2028): Students graduating in "
                        "December 2027 are eligible."]
    assert eligibility(job(description="Graduating in 2027 or later."), me)[1] == []
    _, warnings = eligibility(job(description="You are currently pursuing a PhD in statistics."), me)
    assert warnings == ["the description asks for graduate students"]
    _, warnings = eligibility(job("Software Intern", description="This internship runs in the fall."), me)
    assert warnings == ["the description mentions fall, not Summer 2027"]


def test_freshness():
    today = date(2026, 10, 29)
    assert freshness("2026-10-20", today) == 1.0
    assert freshness("2026-10-01", today) == 0.7
    assert freshness("2026-09-10", today) == 0.4
    assert freshness("2025-01-01", today) == 0.1
    assert freshness(None, today) == freshness("garbage", today) == 0.5


def test_assess():
    me = ada()
    today = date(2026, 10, 29)
    good = assess(job(description="## Requirements:\n- Python\n- Go\n- SQL\n\nNice to have:\n- Docker"), me, today)
    assert (good.matched_required, good.missing_required) == (["Go", "Python", "SQL"], [])
    assert good.matched_preferred == ["Docker"]
    assert good.projects[0] == ("Payments API", ["Go"])
    assert good.eligible and good.summary() == "you have 3 of 3 required skills (Go, Python, SQL)"

    poor = assess(job(description="## Requirements:\n- Rust\n- Kafka\n- Scala"), me, today)
    assert poor.missing_required == ["Kafka", "Rust", "Scala"]
    assert poor.score < good.score
    assert "missing Kafka, Rust, Scala" in poor.summary()

    # General skills (APIs, testing...) are never counted as gaps.
    assert assess(job(description="## Requirements:\n- APIs and testing"), me, today).missing_required == []
    # An older posting scores lower; a blocked one is not eligible.
    assert assess(replace(job(), posted="2024-01-01"), me, today).score < assess(job(), me, today).score
    assert not assess(job("PhD Intern"), me, today).eligible


def test_assess_prefers_roles_listed_first():
    me = ada()
    today = date(2026, 10, 29)
    software = assess(job("Software Engineering Intern"), me, today).score
    ml = assess(job("Machine Learning Intern"), me, today).score
    assert software > ml

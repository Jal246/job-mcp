from dataclasses import replace

import pytest

from job_mcp.boards import Board, Job
from job_mcp.store import Store, StoreError, fts_query, place_names, same_role, title_matches


def job(number: int, title: str = "Software Engineer", company: str = "Acme",
        locations: tuple[str, ...] = ("London",), level: str = "mid", workplace: str | None = None,
        posted: str | None = "2026-10-01", description: str = "Write code.") -> Job:
    return Job(id=f"greenhouse:acme:{number}", company=company, title=title,
               url=f"https://acme.example/{number}", locations=locations, workplace=workplace,
               level=level, employment_type=None, department=None, posted=posted, salary=None,
               description=description)


@pytest.fixture
def store(tmp_path) -> Store:
    store = Store(tmp_path / "jobs.db")
    store.add_company("Acme", Board("greenhouse", "acme"))
    return store


def titles(listings) -> list[str]:
    return [listing.job.job.title for listing in listings]


def test_fts_query():
    assert fts_query('react "new grad"') == '"new grad" "react"'
    assert fts_query("C++ front-end") == '"C" "front end"'  # FTS5 syntax can't sneak in.
    assert fts_query('  "" ') == ""


def test_place_names():
    assert place_names("Bangalore") == {"bangalore", "bengaluru"}
    assert {"india", "bengaluru", "bangalore", "pune"} <= place_names("India")
    assert {"uk", "united kingdom", "london"} <= place_names("UK")
    assert {"san francisco", "palo alto", "foster city"} <= place_names("SF Bay Area")
    assert place_names("  Paris ") == {"paris"}


def test_same_role():
    london = job(1, "Software Engineer - London")
    dublin = job(2, "Software Engineer (Dublin)", locations=("Dublin, Ireland",))
    remote = job(3, "Software Engineer, Remote", locations=("Remote - US",))
    payments = job(4, "Software Engineer - Payments")
    assert same_role(london) == same_role(dublin) == same_role(remote)
    assert same_role(payments) != same_role(london)
    assert same_role(replace(london, level="senior")) != same_role(london)


def test_title_matches():
    assert title_matches("software engineer", "Software Engineering Intern") == 2
    assert title_matches("software engineer", "Procurement Specialist") == 0


def test_add_and_remove_companies(store):
    with pytest.raises(StoreError, match="already watching Acme"):
        store.add_company("acme", Board("lever", "other"))
    with pytest.raises(StoreError, match="already watching Acme"):
        store.add_company("Acme Two", Board("greenhouse", "acme"))
    store.update("Acme", [job(1), job(2)])
    assert [(c.name, c.open_jobs) for c in store.companies()] == [("Acme", 2)]
    assert store.remove_company("ACME").open_jobs == 2
    assert store.companies() == []
    assert store.search() == ([], 0)  # Its jobs went with it.
    with pytest.raises(StoreError, match="not watching 'Acme'"):
        store.remove_company("Acme")


def test_update_finds_new_and_closed_jobs(store):
    first = store.update("Acme", [job(1), job(2), job(2)])  # A board can list a job twice.
    assert ([j.id for j in first.new], first.closed, first.open) == (
        ["greenhouse:acme:1", "greenhouse:acme:2"], 0, 2)

    second = store.update("Acme", [job(2, "Software Engineer II"), job(3)])
    assert ([j.id for j in second.new], second.closed, second.open) == (["greenhouse:acme:3"], 1, 2)
    assert store.get("greenhouse:acme:1").closed
    assert store.get("greenhouse:acme:2").job.title == "Software Engineer II"

    back = store.update("Acme", [job(1), job(2, "Software Engineer II"), job(3)])
    assert [j.id for j in back.new] == ["greenhouse:acme:1"]  # Closed, now open again.
    assert store.get("greenhouse:acme:1").closed is None


def test_update_leaves_unchanged_jobs_alone(store):
    store.update("Acme", [job(1)])
    seen = store.get("greenhouse:acme:1").first_seen
    store.update("Acme", [job(1)])
    assert store.get("greenhouse:acme:1").first_seen == seen
    assert titles(store.search("software")[0]) == ["Software Engineer"]  # Still indexed once.
    assert store.search("software")[1] == 1


def test_refresh_failed_keeps_the_jobs(store):
    store.update("Acme", [job(1)])
    store.refresh_failed("Acme", "answered 500")
    [company] = store.companies()
    assert (company.error, company.open_jobs) == ("answered 500", 1)
    store.update("Acme", [job(1)])
    assert store.companies()[0].error is None


def test_get_unknown_job(store):
    with pytest.raises(StoreError, match="no job with id 'nope'"):
        store.get("nope")


def test_search_by_words(store):
    store.update("Acme", [
        job(1, "Procurement Specialist", description="Buy laptops for software engineer teams."),
        job(2, "Senior Software Engineer", level="senior", description="Kafka."),
        job(3, "Designer", description="Figma."),
        job(4, "Software Engineering Intern", level="intern", description="Learn."),
    ])
    listings, total = store.search("software engineer")
    # Every word must match somewhere; title matches come first, then the description match.
    assert titles(listings) == ["Senior Software Engineer", "Software Engineering Intern",
                                "Procurement Specialist"]
    assert total == 3
    assert titles(store.search('"software engineer" teams')[0]) == ["Procurement Specialist"]
    assert store.search("rust")[1] == 0


def test_search_filters(store):
    store.update("Acme", [
        job(1, "Backend Engineer", locations=("Bangalore",), level="new_grad", posted="2026-10-28"),
        job(2, "Frontend Engineer", locations=("Pune, India",), posted="2020-01-01"),
        job(3, "Data Engineer", locations=("Remote - US",), workplace="remote"),
        job(4, "Platform Engineer", locations=("Berlin",), workplace="remote"),
        job(5, "Sales Lead", locations=("New York, NY",), level="senior"),
    ])
    assert titles(store.search(places=["India"])[0]) == ["Backend Engineer", "Frontend Engineer"]
    assert titles(store.search(places=["Bengaluru"])[0]) == ["Backend Engineer"]
    assert titles(store.search(places=["NYC"])[0]) == ["Sales Lead"]
    assert titles(store.search(places=["remote"])[0]) == ["Data Engineer", "Platform Engineer"]
    assert titles(store.search(remote_only=True, places=["Berlin"])[0]) == ["Platform Engineer"]
    assert titles(store.search(levels=["new_grad", "senior"])[0]) == ["Backend Engineer", "Sales Lead"]
    assert titles(store.search(posted_within_days=365 * 3)[0])[-1] != "Frontend Engineer"
    assert store.search(new_within_days=1)[1] == 5
    assert store.search(companies=["acme"])[1] == 5
    assert store.search(companies=["Other"])[1] == 0


def test_search_newest_first(store):
    store.update("Acme", [job(1, "Old", posted="2026-01-01"), job(2, "New", posted="2026-10-01"),
                          job(3, "Undated", posted=None)])
    assert titles(store.search()[0]) == ["New", "Old", "Undated"]
    assert titles(store.search("old OR new", sort="newest")[0]) == []  # Every word must match.


def test_search_merges_the_same_role_in_other_places(store):
    store.update("Acme", [
        job(1, "Software Engineer, New Grad", locations=("Bangalore",)),
        job(2, "Software Engineer, New Grad", locations=("Dublin", "London")),
        job(3, "Software Engineer, New Grad - Tokyo", locations=("Tokyo",)),
        job(4, "Software Engineer, Payments"),
    ])
    listings, total = store.search("new grad")
    assert total == 3
    [listing] = listings
    assert len(listing.also) == 2
    assert listing.locations == ("Bangalore", "Dublin", "London", "Tokyo")


def test_search_limit_counts_roles(store):
    store.update("Acme", [job(n, f"Role {n}") for n in range(10)])
    listings, total = store.search(limit=3)
    assert (len(listings), total) == (3, 10)


def test_closed_jobs_are_hidden_unless_asked_for(store):
    store.update("Acme", [job(1), job(2, "Designer")])
    store.update("Acme", [job(2, "Designer")])
    assert titles(store.search()[0]) == ["Designer"]
    assert sorted(titles(store.search(include_closed=True)[0])) == ["Designer", "Software Engineer"]


def test_counts(store):
    store.update("Acme", [job(1, level="intern"), job(2, level="intern"), job(3)])
    assert store.counts() == {"intern": 2, "mid": 1}

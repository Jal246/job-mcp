import pytest

from job_mcp import careers, skills
from job_mcp.boards import Board, BoardError
from job_mcp.careers import early_career, fetch, parse_site

pytestmark = pytest.mark.anyio

WORKDAY = Board("workday", "acmewd.wd1/Careers")


def details_requested(requests: list[str]) -> list[str]:
    return [url for url in requests if "/job/" in url or "position_details" in url]


async def test_workday(fake_boards):
    intern, grad = await fetch(WORKDAY, "AcmeWD")  # The director job isn't early-career.
    assert intern.id == "workday:acmewd.wd1/Careers:JR1"
    assert (intern.title, intern.level, intern.locations, intern.posted) == (
        "Software Engineering Intern", "intern", ("San Jose, CA",), "2026-10-13")
    assert intern.salary == "$40 - $50"
    assert intern.url.endswith("/Careers/job/San-Jose/Software-Engineering-Intern_JR1")
    assert intern.description == "Write Python and Go.\nPay: $40 - $50 per hour."
    assert (grad.level, grad.locations) == ("new_grad", ("Austin, TX", "Remote, US"))
    # The intern job, found by two searches, was fetched once.
    assert len(details_requested(fake_boards.requests)) == 2


async def test_career_sites_only_fetch_new_jobs(fake_boards):
    first = await fetch(WORKDAY, "AcmeWD")
    fake_boards.requests.clear()
    again = await fetch(WORKDAY, "AcmeWD", {job.id: job for job in first})
    assert again == first
    assert details_requested(fake_boards.requests) == []  # Only the search pages.
    # Watched under another name: known jobs take it.
    renamed = await fetch(WORKDAY, "Acme Corp", {job.id: job for job in first})
    assert {job.company for job in renamed} == {"Acme Corp"}


async def test_amazon():
    [job] = await fetch(Board("amazon", "amazon"), "Amazon")
    assert job.id == "amazon:amazon:10593054"
    assert (job.level, job.employment_type, job.posted) == ("intern", "Intern", "2026-10-30")
    assert job.url == "https://www.amazon.jobs/en/jobs/10593054/software-dev-engineer-intern"
    assert job.description == ("Build services at scale.\n\n## Basic qualifications\n"
                               "- Currently enrolled in a Bachelor's degree\n- Experience with Java or Python\n\n"
                               "## Preferred qualifications\n- Experience with AWS")
    # The qualifications land in the right parts for matching.
    assert skills.requirements(job.description) == ({"Java", "Python"}, {"AWS"})


async def test_microsoft(fake_boards):
    [job] = await fetch(Board("microsoft", "microsoft"), "Microsoft")
    assert job.id == "microsoft:microsoft:200001"
    assert (job.locations, job.workplace, job.department, job.posted) == (
        ("Redmond, WA, US",), "onsite", "Engineering", "2026-10-01")
    assert job.salary == "$6,810 - $13,480"
    assert job.url == "https://apply.careers.microsoft.com/careers/job/111"
    fake_boards.requests.clear()
    await fetch(Board("microsoft", "microsoft"), "Microsoft", {job.id: job})
    assert details_requested(fake_boards.requests) == []


async def test_fetch_errors():
    with pytest.raises(BoardError, match="there's no Workday site called 'acmewd.wd1/Missing'"):
        await fetch(Board("workday", "acmewd.wd1/Missing"), "AcmeWD")
    with pytest.raises(BoardError, match="isn't a Workday site"):
        await fetch(Board("workday", "nonsense"), "X")


async def test_fetch_reads_job_boards_too():
    jobs = await fetch(Board("greenhouse", "acme"), "Acme")
    assert len(jobs) == 4


async def test_paging_stops_when_titles_stop_matching(monkeypatch):
    pages = []

    async def search(text, offset):
        pages.append((text, offset))
        page = offset // 2
        titles = ["Intern A", "Intern B"] if page == 0 else ["Intern C", "Manager"] if page == 1 else ["Director", "VP"]
        return ([{"t": t} for t in titles], 100, lambda i: i["t"], lambda i: i["t"])

    monkeypatch.setattr(careers, "SEARCHES", ("intern",))
    found = await careers._paged(search, 2)
    assert [item["t"] for item in found] == ["Intern A", "Intern B", "Intern C"]
    assert pages == [("intern", 0), ("intern", 2), ("intern", 4)]  # Stopped after a page of none.


@pytest.mark.parametrize("title, expected", [
    ("Software Engineering Intern - 2027", True), ("NVIDIA 2027 Internships: Systems Software", True),
    ("Software Engineer, New Grad", True), ("University Graduate - Data", True), ("Co-op, Firmware", True),
    ("Director, International Tax", False), ("Senior Software Engineer", False), ("Internal Tools Engineer", False),
])
def test_early_career(title, expected):
    assert early_career(title) is expected


@pytest.mark.parametrize("text, expected", [
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", Board("workday", "nvidia.wd5/NVIDIAExternalCareerSite")),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/X_JR1",
     Board("workday", "nvidia.wd5/NVIDIAExternalCareerSite")),
    ("workday:intel.wd1/External", Board("workday", "intel.wd1/External")),
    ("Amazon", Board("amazon", "amazon")),
    ("https://www.amazon.jobs/en/jobs/123", Board("amazon", "amazon")),
    ("microsoft", Board("microsoft", "microsoft")),
    ("https://jobs.lever.co/spotify", None),
    ("Stripe", None),
])
def test_parse_site(text, expected):
    assert parse_site(text) == expected

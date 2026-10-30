"""A fake internet for the tests: made-up companies' job boards on fake Greenhouse, Lever and
Ashby APIs, so the tests never touch the real network.

Each test gets its own copy of BOARDS (the `fake_boards` fixture), and can change it to see how
a refresh handles jobs being added or taken down.
"""

import copy
import html
import json

import httpx
import pytest

from job_mcp import net

# Milliseconds since 1970 for 2026-10-01 (Lever's date format).
OCT_1_MS = 1790812800000

ACME_DESCRIPTION = html.escape(
    "<h2>About the role</h2><p>Build payments APIs with Python&nbsp;and Go.</p>"
    "<ul><li>Design services</li><li>Ship code</li></ul>"
    "<p>The pay range is $100,000 - $120,000 per year.</p><style>.x{color:red}</style>"
)

BOARDS = {
    ("greenhouse", "acme"): {"jobs": [
        {"id": 101, "title": "Software Engineer, New Grad", "absolute_url": "https://acme.example/jobs/101",
         "location": {"name": "Bangalore"}, "content": ACME_DESCRIPTION,
         "departments": [{"name": "8611 University"}], "first_published": "2026-10-20T09:00:00-04:00"},
        {"id": 102, "title": "Software Engineer, New Grad", "absolute_url": "https://acme.example/jobs/102",
         "location": {"name": "Dublin"}, "content": ACME_DESCRIPTION,
         "departments": [{"name": "8611 University"}], "first_published": "2026-10-18T09:00:00-04:00"},
        {"id": 103, "title": "Senior Backend Engineer", "absolute_url": "https://acme.example/jobs/103",
         "location": {"name": "Remote - India"}, "content": html.escape("<p>Kafka and Postgres.</p>"),
         "departments": [], "updated_at": "2026-09-01T00:00:00Z"},
        {"id": 104, "title": "Procurement Specialist", "absolute_url": "https://acme.example/jobs/104",
         "location": {"name": "Bengaluru, India"},
         "content": html.escape("<p>Buy laptops for our software engineer teams.</p>"),
         "departments": [{"name": "Finance"}], "first_published": "2026-10-25T00:00:00Z"},
    ], "meta": {"total": 4}},
    ("lever", "leverco"): [
        {"id": "lv-1", "text": "Software Engineering Intern", "hostedUrl": "https://jobs.lever.co/leverco/lv-1",
         "categories": {"location": "London", "allLocations": ["London", "Manchester"],
                        "commitment": "Intern", "team": "Engineering"},
         "workplaceType": "hybrid", "createdAt": OCT_1_MS,
         "salaryRange": {"currency": "GBP", "interval": "per-month-salary", "min": 3000, "max": 3500},
         "descriptionPlain": "Join us for the summer.",
         "lists": [{"text": "What you'll do", "content": "<li>Write Rust</li><li>Review code</li>"}],
         "additionalPlain": "We sponsor visas."},
        {"id": "lv-2", "text": "Staff Data Scientist", "hostedUrl": "https://jobs.lever.co/leverco/lv-2",
         "categories": {"location": "New York, NY", "commitment": "Full-time", "department": "Data"},
         "workplaceType": "remote", "createdAt": OCT_1_MS, "descriptionPlain": "Models and SQL."},
    ],
    ("ashby", "ashco"): {"jobs": [
        {"id": "as-1", "title": "Product Designer", "jobUrl": "https://jobs.ashbyhq.com/ashco/as-1",
         "location": "Remote", "isRemote": True, "employmentType": "FullTime", "isListed": True,
         "department": "Design", "publishedAt": "2026-10-27T10:00:00.000+00:00",
         "compensation": {"scrapeableCompensationSalarySummary": "$150K - $180K"},
         "descriptionPlain": "Design things people love."},
        {"id": "as-2", "title": "Secret Role", "jobUrl": "https://jobs.ashbyhq.com/ashco/as-2",
         "location": "Remote", "isListed": False, "descriptionPlain": "Shh."},
        {"id": "as-3", "title": "Engineering Manager, Payments", "jobUrl": "https://jobs.ashbyhq.com/ashco/as-3",
         "location": "San Francisco", "secondaryLocations": [{"location": "Seattle"}],
         "workplaceType": "OnSite", "employmentType": "FullTime", "team": "Payments",
         "publishedAt": "2026-10-10T10:00:00.000+00:00", "descriptionHtml": "<p>Lead a team.</p>"},
    ]},
    # An old, nearly empty board the same company left behind: detection should skip it.
    ("greenhouse", "ashco"): {"jobs": [
        {"id": 1, "title": "Old Job", "absolute_url": "https://ashco.example/old", "location": {"name": "Paris"},
         "content": "", "departments": []},
    ]},
    # Answers 503 to the first request, then works.
    ("greenhouse", "busyco"): {"jobs": []},
}
# Boards whose API is broken: they always answer 500.
BROKEN = {("lever", "downco")}

# Career sites, in the formats the real ones answer in.
WORKDAY_SEARCHES = {  # searchText -> the jobs found (Workday's search is fuzzy).
    "intern": [{"title": "Software Engineering Intern", "externalPath": "/job/San-Jose/Software-Engineering-Intern_JR1"},
               {"title": "Director of Sales", "externalPath": "/job/Austin/Director-of-Sales_JR2"}],
    "new grad": [{"title": "Software Engineer, New Grad", "externalPath": "/job/Austin/Software-Engineer-New-Grad_JR3"}],
    "university graduate": [{"title": "Software Engineering Intern", "externalPath": "/job/San-Jose/Software-Engineering-Intern_JR1"}],
}
WORKDAY_JOBS = {
    "/job/San-Jose/Software-Engineering-Intern_JR1": {
        "title": "Software Engineering Intern", "location": "San Jose, CA", "startDate": "2026-10-13",
        "timeType": "Full time", "externalUrl": "https://acmewd.wd1.myworkdayjobs.com/Careers/job/San-Jose/Software-Engineering-Intern_JR1",
        "jobDescription": "<p>Write Python and Go.</p><p>Pay: $40 - $50 per hour.</p>"},
    "/job/Austin/Software-Engineer-New-Grad_JR3": {
        "title": "Software Engineer, New Grad", "location": "Austin, TX", "additionalLocations": ["Remote, US"],
        "startDate": "2026-10-01", "jobDescription": "<p>C++ on embedded systems.</p>"},
}
AMAZON_JOB = {
    "id": "a1", "id_icims": "10593054", "title": "Software Dev Engineer Intern, Summer 2027",
    "job_path": "/en/jobs/10593054/software-dev-engineer-intern", "normalized_location": "Seattle, Washington, USA",
    "posted_date": "October 30, 2026", "is_intern": True, "job_category": "Software Development",
    "description": "<p>Build services at scale.</p>",
    "basic_qualifications": "- Currently enrolled in a Bachelor's degree<br/>- Experience with Java or Python",
    "preferred_qualifications": "Experience with AWS"}
MICROSOFT_POSITION = {"id": 111, "displayJobId": "200001", "name": "Software Engineering Intern"}
MICROSOFT_DETAILS = {**MICROSOFT_POSITION, "standardizedLocations": ["Redmond, WA, US"], "postedTs": 1790812800,
                     "jobDescription": "<p>Write C# and TypeScript.</p><p>USD $6,810 - $13,480 per month.</p>",
                     "publicUrl": "https://apply.careers.microsoft.com/careers/job/111",
                     "workLocationOption": ["onsite"], "department": ["Engineering"]}


def fake_career_site(request: httpx.Request) -> httpx.Response | None:
    """Answer like Workday (the made-up "acmewd.wd1/Careers" site), Amazon or Microsoft; None
    for other hosts."""
    url = request.url
    if url.host == "acmewd.wd1.myworkdayjobs.com" and url.path.startswith("/wday/cxs/acmewd/Careers"):
        path = url.path.removeprefix("/wday/cxs/acmewd/Careers")
        if request.method == "POST" and path == "/jobs":
            found = WORKDAY_SEARCHES.get(json.loads(request.content)["searchText"], [])
            return httpx.Response(200, json={"total": len(found), "jobPostings": found})
        if path in WORKDAY_JOBS:
            return httpx.Response(200, json={"jobPostingInfo": WORKDAY_JOBS[path]})
        return httpx.Response(404)
    if url.host.endswith(".myworkdayjobs.com"):
        return httpx.Response(422)  # Workday's answer for a site that doesn't exist.
    if url.host == "www.amazon.jobs" and url.path == "/en/search.json":
        found = [AMAZON_JOB] if url.params["base_query"] == "intern" else []
        return httpx.Response(200, json={"hits": len(found), "jobs": found})
    if url.host == "apply.careers.microsoft.com" and url.path == "/api/pcsx/search":
        found = [MICROSOFT_POSITION] if url.params["query"] == "intern" else []
        return httpx.Response(200, json={"data": {"count": len(found), "positions": found}})
    if url.host == "apply.careers.microsoft.com" and url.path == "/api/pcsx/position_details":
        return httpx.Response(200, json={"data": MICROSOFT_DETAILS})
    return None


def board_of(url: httpx.URL) -> tuple[str, str] | None:
    """The (source, board) a fake API URL asks for."""
    path = url.path.strip("/").split("/")
    match url.host, path:
        case "boards-api.greenhouse.io", ["v1", "boards", name, "jobs"]:
            return "greenhouse", name
        case "api.lever.co", ["v0", "postings", name]:
            return "lever", name
        case "api.ashbyhq.com", ["posting-api", "job-board", name]:
            return "ashby", name
    return None


@pytest.fixture(autouse=True)
def fake_boards(monkeypatch):
    """Route every request to the fake boards. Returns the boards (change them to change what
    the APIs answer); the URLs requested are in fake_boards.requests."""
    boards = FakeBoards(copy.deepcopy(BOARDS))
    requests = boards.requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if (answer := fake_career_site(request)) is not None:
            return answer
        key = board_of(request.url)
        if key in BROKEN:
            return httpx.Response(500)
        if key == ("greenhouse", "busyco") and requests.count(str(request.url)) == 1:
            return httpx.Response(503)
        if key not in boards:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=boards[key])

    async def sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr(net, "TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(net, "SLEEP", sleep)
    return boards


class FakeBoards(dict):
    requests: list[str]


@pytest.fixture
def anyio_backend():
    return "asyncio"

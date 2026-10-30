"""A fake internet for the tests: made-up companies' job boards on fake Greenhouse, Lever and
Ashby APIs, so the tests never touch the real network.

Each test gets its own copy of BOARDS (the `fake_boards` fixture), and can change it to see how
a refresh handles jobs being added or taken down.
"""

import copy
import html

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

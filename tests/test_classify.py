import pytest

from job_mcp.classify import html_to_text, level, money, salary_in, workplace


@pytest.mark.parametrize("title, expected", [
    ("Software Engineering Intern - Summer '27", "intern"),
    ("Co-op, Backend", "intern"),
    ("Software Engineer, New Grad", "new_grad"),
    ("University Graduate - Data", "new_grad"),
    ("Entry-Level Analyst", "new_grad"),
    ("Junior Frontend Developer", "junior"),
    ("Software Engineer I", "junior"),
    ("Software Engineer", "mid"),
    ("Product Manager", "mid"),  # A product manager doesn't manage people.
    ("Internal Tools Engineer", "mid"),  # "Internal" isn't "intern".
    ("Senior Backend Engineer", "senior"),
    ("Sr. Software Engineer", "senior"),
    ("Software Engineer III", "senior"),
    ("Senior Staff Engineer", "staff"),
    ("Principal Architect", "staff"),
    ("Engineering Manager, Payments", "manager"),
    ("Director of Engineering", "manager"),
    ("Head of Design", "manager"),
])
def test_level(title, expected):
    assert level(title) == expected


def test_level_trusts_the_board_saying_intern():
    assert level("Summer Analyst", "Intern") == "intern"
    assert level("Summer Analyst", "Full-time") == "mid"


def test_workplace():
    assert workplace("Remote - India") == "remote"
    assert workplace(None, "Hybrid - San Francisco") == "hybrid"
    assert workplace("on-site") == workplace("OnSite") == "onsite"
    assert workplace("unspecified", "London") is None
    assert workplace("hybrid", "Remote") == "hybrid"  # The first text that says wins.


def test_salary_in():
    assert salary_in("Pay: $87,069 - $124,385 plus equity.") == "$87,069 - $124,385"
    assert salary_in("€60K–€80K") == "€60K–€80K"
    assert salary_in("₹12,00,000 to ₹18,00,000 a year") == "₹12,00,000 to ₹18,00,000"
    assert salary_in("$120 - 150K USD") == "$120 - 150K USD"
    assert salary_in("We have 3-5 openings and a $500 budget") is None


def test_money():
    assert money(120000) == "120,000"
    assert money(52.5) == "52.50"


def test_html_to_text():
    page = ("<h2>About</h2><p>We build&nbsp;<b>fast</b> things &amp; more.</p>"
            "<ul><li>Python</li><li>Go</li></ul><script>alert(1)</script><p></p><p>Apply!</p>")
    assert html_to_text(page) == "## About\nWe build fast things & more.\n\n- Python\n- Go\n\nApply!"

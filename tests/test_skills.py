import pytest

from job_mcp.skills import canonical, find, requirements, sections


@pytest.mark.parametrize("text, expected", [
    ("Python, Java and SQL", {"Python", "Java", "SQL"}),
    ("JavaScript and TypeScript (JS/TS)", {"JavaScript", "TypeScript"}),
    ("We use Go, Rust and C++", {"Go", "Rust", "C++"}),
    ("Postgres and Redis", {"PostgreSQL", "Redis"}),
    ("React Native for mobile apps", {"React Native", "Mobile"}),
    ("- Python\n- Go\n", {"Python", "Go"}),  # A list item.
    ("React.js and Next.js", {"React", "Next.js"}),
    ("Experience with GitHub Copilot or Claude Code", {"AI-assisted development", "GitHub"}),
    ("SQLite FTS5 with BM25 ranking", {"SQLite", "Full-text search"}),
])
def test_find(text, expected):
    assert find(text) == expected


@pytest.mark.parametrize("text", [
    "Let's go to the office",  # "go" the verb.
    "Where everyone can excel in their role",  # "excel" the verb.
    "Our multiplayer cursors",  # Not the Cursor editor.
    "Gold, silver and rust colors",  # Lowercase rust.
    "We take Swift action on feedback",  # Swift the adjective.
])
def test_find_avoids_ordinary_words(text):
    assert find(text) == set()


def test_canonical():
    assert canonical("postgres") == "PostgreSQL"
    assert canonical("Model Context Protocol (MCP)") == "MCP"
    assert canonical("Underwater basket weaving") is None


DESCRIPTION = """\
## About the role
You'll build payments APIs with Kafka.

## What you'll bring
- Python or Java
- Experience with SQL
- Kubernetes is a plus

Nice to haves:
- Go
- Terraform

At Example we believe in hiring people from every background, and we encourage you to apply even if you don't meet every requirement listed above.

Pay Transparency Disclosure
The range is $50 an hour. Bonus: Rust skills.
"""


def test_sections():
    parts = sections(DESCRIPTION)
    assert parts["required"] == "- Python or Java\n- Experience with SQL"
    assert parts["preferred"] == "- Kubernetes is a plus\n- Go\n- Terraform"
    assert "Kafka" in parts["other"] and "Rust" in parts["other"]  # Legal text isn't preferred.


def test_requirements():
    assert requirements(DESCRIPTION) == ({"Python", "Java", "SQL"}, {"Kubernetes", "Go", "Terraform"})
    # No must-have part: everything outside the nice-to-haves counts as required.
    assert requirements("We use Python and Go.\n\nBonus: Rust") == ({"Python", "Go"}, {"Rust"})

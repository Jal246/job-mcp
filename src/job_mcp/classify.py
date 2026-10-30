"""Reading a job posting: its level (intern, new grad, senior...), whether it's remote, its salary,
and its description as plain text.

Job boards don't say most of this in a structured way, so it's worked out from the title and
text with a few patterns. They're simple on purpose: easy to read, test and fix.

This module knows nothing about MCP, so it can be used and tested as plain Python.
"""

import html
import re

# From least to most experienced. "mid" is what's left when nothing else matches.
LEVELS = ("intern", "new_grad", "junior", "mid", "senior", "staff", "manager")

# Checked in this order, on the lowercased title; the first match wins. So "Senior Staff
# Engineer" is staff (checked before senior), and "Engineering Manager, New Grad Hiring" is a
# manager (checked before new_grad).
LEVEL_PATTERNS = [
    ("intern", r"\b(intern|internship|co-?op|apprentice(ship)?|working student|trainee)s?\b"),
    ("staff", r"\b(staff|principal|distinguished|fellow)\b"),
    ("manager", r"\b(director|head of|vp|vice president|chief)\b"
                r"|\b(engineering|software|development|people|team) manager\b"),
    ("senior", r"\b(senior|sr|lead)\b|\b(iii|iv)\b"),
    ("new_grad", r"\b(new grad(uate)?s?|graduate|university|early career|entry[- ]level|campus)\b"),
    ("junior", r"\b(junior|jr|associate)\b|\b(engineer|developer|analyst|scientist) i\b"),
]


def level(title: str, employment_type: str | None = None) -> str:
    """The level of a job, from its title (and the board's employment type, when it says
    "Intern"): one of LEVELS."""
    if employment_type and "intern" in employment_type.lower():
        return "intern"
    lowered = title.lower()
    for name, pattern in LEVEL_PATTERNS:
        if re.search(pattern, lowered):
            return name
    return "mid"


def workplace(*texts: str | None) -> str | None:
    """"remote", "hybrid" or "onsite" if one of the texts (a location, a board's workplace
    field...) says so, else None. The first text that says anything wins."""
    for text in texts:
        lowered = (text or "").lower().replace("-", "").replace(" ", "")
        if "remote" in lowered:
            return "remote"
        if "hybrid" in lowered:
            return "hybrid"
        if "onsite" in lowered or "inoffice" in lowered:
            return "onsite"
    return None


# A pay range written in a description, like "$87,069 - $124,385", "€60K–€80K" or
# "₹12,00,000 to ₹18,00,000". The second currency sign is optional ("$120 - 150K").
_AMOUNT = r"\d[\d,.]*\s?[kKmM]?"
SALARY = re.compile(rf"([$£€₹])\s?{_AMOUNT}\s*(?:-|–|—|to)\s*\1?\s?{_AMOUNT}"
                    r"(?:\s?(?:USD|CAD|AUD|GBP|EUR|INR|SGD))?")


def salary_in(text: str) -> str | None:
    """The first pay range written in text, tidied, or None."""
    if match := SALARY.search(text):
        return " ".join(match.group(0).split())
    return None


def money(amount: float) -> str:
    """120000 -> "120,000"; 52.5 -> "52.50" (an hourly rate)."""
    return f"{amount:,.0f}" if amount == int(amount) else f"{amount:,.2f}"


# Tags whose text isn't part of the description at all.
_HIDDEN = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
# Tags that end a line, the end of a list (followed by a blank line), and list items (which
# start a line, with a dash).
_LINE_END = re.compile(r"<\s*(br|/p|/div|/h[1-6]|/tr|/section)\b[^>]*>", re.IGNORECASE)
_LIST_END = re.compile(r"<\s*/(ul|ol)\b[^>]*>", re.IGNORECASE)
_LIST_ITEM = re.compile(r"<\s*li\b[^>]*>", re.IGNORECASE)
_HEADING = re.compile(r"<\s*h[1-6]\b[^>]*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def html_to_text(page: str) -> str:
    """A job description's HTML as plain text: one paragraph per line, list items as "- item",
    headings as "## heading", blank lines tidied."""
    text = _HIDDEN.sub("", page)
    text = _LIST_ITEM.sub("\n- ", text)
    text = _HEADING.sub("\n## ", text)
    text = _LINE_END.sub("\n", text)
    text = _LIST_END.sub("\n\n", text)
    text = html.unescape(_TAG.sub("", text)).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    # Drop list markers and headings that ended up empty, then squeeze runs of blank lines.
    lines = ["" if line in ("-", "##") else line for line in lines]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

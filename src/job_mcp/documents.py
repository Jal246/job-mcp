"""Turning a resume or cover letter into a PDF.

The layout is the classic one-column student resume recruiters and applicant tracking systems
(ATS) read easily: real text (not an image), standard fonts, no tables of skills, no columns.
If a resume runs a little over one page, it's set slightly smaller (down to 90%) to fit.

This module knows nothing about MCP, so it can be used and tested as plain Python.
"""

from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import pypdfium2
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from job_mcp.profile import Resume

FONT, BOLD, ITALIC = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
MARGIN = 0.5 * inch
# Sizes tried, in order, until the resume fits on one page.
SCALES = (1.0, 0.97, 0.94, 0.9)

# The standard PDF fonts only have Western European letters; swap the usual others.
_REPLACE = str.maketrans({"→": "->", "←": "<-", "≈": "~", "≥": ">=", "≤": "<=", "×": "x",
                          "✓": "", "★": "*", " ": " ", " ": " "})


def _text(value: str) -> str:
    """Text safe to put in a reportlab paragraph: escaped, and only characters the font has."""
    value = value.translate(_REPLACE).encode("cp1252", errors="replace").decode("cp1252")
    return escape(value)


def _href(link: str) -> str:
    if "@" in link and "/" not in link:
        return f"mailto:{link}"
    return link if "://" in link else f"https://{link}"


def _link(text: str, target: str | None = None) -> str:
    return f'<a href="{escape(_href(target or text))}">{_text(text)}</a>'


@dataclass(frozen=True)
class Styles:
    name: ParagraphStyle
    contact: ParagraphStyle
    section: ParagraphStyle
    body: ParagraphStyle
    right: ParagraphStyle
    bullet: ParagraphStyle
    gap: float


def styles(scale: float) -> Styles:
    size = 10 * scale
    body = ParagraphStyle("body", fontName=FONT, fontSize=size, leading=size * 1.24)
    return Styles(
        name=ParagraphStyle("name", fontName=BOLD, fontSize=20 * scale, leading=24 * scale,
                            alignment=TA_CENTER),
        contact=ParagraphStyle("contact", parent=body, fontSize=size * 0.95, alignment=TA_CENTER),
        section=ParagraphStyle("section", fontName=BOLD, fontSize=11.5 * scale,
                               leading=13 * scale, spaceBefore=7 * scale, spaceAfter=1),
        body=body,
        right=ParagraphStyle("right", parent=body, alignment=TA_RIGHT),
        bullet=ParagraphStyle("bullet", parent=body, leftIndent=12, bulletIndent=3),
        gap=3 * scale,
    )


def _row(left: str, right: str, style: Styles, width: float) -> Table:
    """A line with text on the left and on the right (a school and its dates)."""
    table = Table([[Paragraph(left, style.body), Paragraph(right, style.right)]],
                  colWidths=[width * 0.7, width * 0.3])
    table.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return table


def _bullets(items: list[str], style: Styles) -> list:
    return [Paragraph(_text(item), style.bullet, bulletText="•") for item in items]


def _header(resume: Resume, style: Styles) -> list:
    contact = [_text(part) for part in (resume.location, resume.phone) if part]
    contact.append(_link(resume.email))
    contact += [_link(link) for link in resume.links]
    return [Paragraph(_text(resume.name.upper()), style.name),
            Paragraph("  |  ".join(contact), style.contact)]


def _resume_story(resume: Resume, style: Styles, width: float) -> list:
    story = _header(resume, style)

    def section(title: str, entries: list) -> None:
        if entries:
            story.append(Paragraph(title.upper(), style.section))
            story.append(HRFlowable(width="100%", thickness=0.8, color="black", spaceBefore=0,
                                    spaceAfter=style.gap))
            story.extend(entries)

    sections = {}
    entries = []
    for school in resume.education:
        where = f"<b>{_text(school.school)}</b>" + (f", {_text(school.location)}" if school.location else "")
        lines = [_row(where, _text(school.dates), style, width)]
        degree = _text(school.degree) + (f"  |  GPA: {_text(school.gpa)}" if school.gpa else "")
        lines.append(Paragraph(degree, style.body))
        if school.coursework:
            lines.append(Paragraph(f"<b>Relevant Coursework:</b> {_text(', '.join(school.coursework))}",
                                   style.body))
        entries.append(KeepTogether(lines))
    sections["education"] = ("Education", entries)

    sections["skills"] = ("Technical Skills", [
        Paragraph(f"<b>{_text(group.name)}:</b> {_text(', '.join(group.items))}", style.body)
        for group in resume.skills if group.items])

    entries = []
    for project in resume.projects:
        left = f"<b>{_text(project.name)}</b>"
        if project.tech:
            left += f"  |  <i>{_text(', '.join(project.tech))}</i>"
        right = " · ".join(filter(None, [_link(project.url) if project.url else "",
                                         _text(project.dates or "")]))
        entries.append(KeepTogether([_row(left, right, style, width), *_bullets(project.bullets, style),
                                     Spacer(1, style.gap)]))
    sections["projects"] = ("Projects", entries)

    entries = []
    for job in resume.experience:
        where = f"<b>{_text(job.organization)}</b>" + (f", {_text(job.location)}" if job.location else "")
        entries.append(KeepTogether([_row(where, _text(job.dates), style, width),
                                     Paragraph(f"<i>{_text(job.title)}</i>", style.body),
                                     *_bullets(job.bullets, style), Spacer(1, style.gap)]))
    sections["experience"] = ("Experience", entries)

    entries = []
    for activity in resume.activities:
        left = f"<b>{_text(activity.name)}</b>"
        if activity.role:
            left += f"  |  <i>{_text(activity.role)}</i>"
        if activity.location:
            left += f", {_text(activity.location)}"
        entries.append(KeepTogether([_row(left, _text(activity.dates), style, width),
                                     *_bullets(activity.bullets, style), Spacer(1, style.gap)]))
    sections["activities"] = (resume.activities_title, entries)

    sections["certifications"] = ("Certifications", [
        Paragraph(f"<b>{_text(cert.name)}</b>" + (f" - {_text(cert.detail)}" if cert.detail else ""),
                  style.body) for cert in resume.certifications])

    for name in resume.section_order:
        section(*sections[name])
    return story


def pages(path: Path) -> int:
    pdf = pypdfium2.PdfDocument(path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def pdf_text(path: Path) -> str:
    """The text in a PDF, as an applicant tracking system would read it."""
    pdf = pypdfium2.PdfDocument(path)
    try:
        return "\n".join(page.get_textpage().get_text_range() for page in pdf)
    finally:
        pdf.close()


def _document(path: Path, title: str, author: str, margin: float) -> SimpleDocTemplate:
    path.parent.mkdir(parents=True, exist_ok=True)
    return SimpleDocTemplate(str(path), pagesize=LETTER, leftMargin=margin, rightMargin=margin,
                             topMargin=margin, bottomMargin=margin, title=title, author=author)


def render_resume(resume: Resume, path: Path) -> tuple[int, float]:
    """Write the resume to path as a PDF, at the largest size (scale) that fits one page.
    Returns (pages, scale); pages is more than 1 if even the smallest size doesn't fit."""
    for scale in SCALES:
        document = _document(path, f"{resume.name} - Resume", resume.name, MARGIN)
        document.build(_resume_story(resume, styles(scale), LETTER[0] - 2 * MARGIN))
        if (count := pages(path)) == 1:
            return count, scale
    return count, scale


def render_cover_letter(resume: Resume, company: str, paragraphs: list[str], greeting: str,
                        closing: str, today: str, path: Path) -> int:
    """Write a cover letter (with the resume's header, so they match) to path. Returns pages."""
    style = styles(1.05)
    letter = ParagraphStyle("letter", parent=style.body, spaceAfter=8)
    story = _header(resume, styles(0.92))  # The letter's margins are wider: keep contact on one line.
    story += [HRFlowable(width="100%", thickness=0.8, color="black", spaceBefore=4, spaceAfter=14),
              Paragraph(_text(today), letter), Paragraph(f"{_text(company)} Hiring Team", letter),
              Paragraph(_text(greeting), letter)]
    story += [Paragraph(_text(paragraph), letter) for paragraph in paragraphs]
    story += [Paragraph(_text(closing), letter), Paragraph(_text(resume.name), style.body)]
    document = _document(path, f"{resume.name} - Cover Letter - {company}", resume.name, 0.8 * inch)
    document.build(story)
    return pages(path)

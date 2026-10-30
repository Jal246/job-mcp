"""Your master profile: every fact about you that a resume or cover letter may use, plus what
you're looking for. It's the single source of truth: a tailored resume may choose, reorder and
reword these facts, but never add new ones. check_resume() enforces that.

The profile can hold more than fits on one page (extra projects, extra bullets per project);
tailoring picks what fits each job best.

This module knows nothing about MCP, so it can be used and tested as plain Python.
"""

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from job_mcp import skills

Section = Literal["education", "skills", "projects", "experience", "activities", "certifications"]
WorkAuthorization = Literal["us_citizen", "green_card", "f1_student", "needs_sponsorship"]
RoleKind = Literal["software", "data", "ml", "infrastructure", "security", "hardware", "design",
                   "product", "business"]
Degree = Literal["high_school", "bachelors", "masters", "phd", "mba"]


class Education(BaseModel):
    school: str
    degree: str = Field(description='e.g. "Bachelor of Science in Computer Science"')
    location: str = ""
    dates: str = Field(description='e.g. "Expected May 2028" or "Aug 2024 - May 2028"')
    gpa: str | None = None
    coursework: list[str] = []


class Experience(BaseModel):
    organization: str
    title: str
    location: str = ""
    dates: str = Field(description='e.g. "Mar 2024 - Jun 2024"')
    bullets: list[str] = []


class Project(BaseModel):
    name: str
    tech: list[str] = Field(default=[], description="Languages, frameworks and tools used")
    url: str | None = Field(default=None, description='e.g. "github.com/you/project"')
    dates: str | None = None
    bullets: list[str] = []


class Activity(BaseModel):
    """A hackathon, club, competition or volunteer role."""
    name: str = Field(description='e.g. "Cerebral Valley AI Hackathon"')
    role: str = Field(default="", description='e.g. "Participant", "Finalist", "Organizer"')
    location: str = ""
    dates: str = ""
    bullets: list[str] = []


class SkillGroup(BaseModel):
    name: str = Field(description='e.g. "Languages", "Frameworks", "Tools"')
    items: list[str]


class Certification(BaseModel):
    name: str
    detail: str = ""


class Resume(BaseModel):
    """A resume: what gets rendered to a page."""
    name: str
    email: str
    phone: str = ""
    location: str = ""
    links: list[str] = Field(default=[], description='e.g. ["github.com/you", "linkedin.com/in/you"]')
    education: list[Education] = []
    skills: list[SkillGroup] = []
    projects: list[Project] = []
    experience: list[Experience] = []
    activities: list[Activity] = []
    activities_title: str = Field(default="Activities", description='e.g. "Hackathons & Activities"')
    certifications: list[Certification] = []
    section_order: list[Section] = ["education", "skills", "projects", "experience",
                                    "activities", "certifications"]


class Preferences(BaseModel):
    """What you're looking for; match_jobs uses it to rank jobs and hide ones you can't apply to."""
    degree: Degree = Field(default="bachelors", description="The degree you're studying for")
    graduation: str = Field(default="", description='When you graduate, "YYYY-MM", e.g. "2028-05"')
    seasons: list[str] = Field(default=[], description='Internship seasons, e.g. ["Summer 2027"]')
    levels: list[Literal["intern", "new_grad", "junior", "mid", "senior", "staff", "manager"]] = ["intern"]
    places: list[str] = Field(default=[], description='e.g. ["Bay Area", "Remote"]')
    work_countries: list[str] = Field(default=["United States"],
                                      description="Countries you can work in (for remote jobs)")
    roles: list[RoleKind] = ["software", "data", "ml", "infrastructure", "security"]
    work_authorization: WorkAuthorization = Field(default="us_citizen", description=(
        "For US jobs: us_citizen, green_card, f1_student (international student; internships "
        "usually on CPT), or needs_sponsorship"))


class Profile(Resume):
    """Your master profile: a resume with everything in it, plus your preferences."""
    preferences: Preferences = Preferences()
    extra_facts: list[str] = Field(default=[], description=(
        "True things about you that aren't on the resume but can be used in cover letters and "
        "tailored bullets: awards, clubs, details about projects, why you like a field..."))


def load(path: Path) -> Profile | None:
    return Profile.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def save(path: Path, profile: Profile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")


# --- What a profile or resume says ------------------------------------------------------------

def text_of(resume: Resume) -> str:
    """All of a resume's words, as one text (for finding skills and numbers in it)."""
    parts = [resume.name]
    for school in resume.education:
        parts += [school.school, school.degree, *school.coursework]
    for group in resume.skills:
        parts += group.items
    for project in resume.projects:
        parts += [project.name, ", ".join(project.tech), *project.bullets]
    for job in resume.experience:
        parts += [job.organization, job.title, *job.bullets]
    for activity in resume.activities:
        parts += [activity.name, activity.role, *activity.bullets]
    for cert in resume.certifications:
        parts += [cert.name, cert.detail]
    if isinstance(resume, Profile):
        parts += resume.extra_facts
    return "\n".join(parts)


def skills_of(resume: Resume) -> set[str]:
    """Every skill a resume shows: listed, used in projects, or mentioned in bullets."""
    found = skills.find(text_of(resume))
    for item in [i for g in resume.skills for i in g.items] + [t for p in resume.projects for t in p.tech]:
        if name := skills.canonical(item):
            found.add(name)
    return found


# Numbers like "8,472", "95x", "0.33s", "41", "100%": each has to come from the profile.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUMBER.findall(text)}


def _same(a: str | None, b: str | None) -> bool:
    return " ".join((a or "").lower().split()) == " ".join((b or "").lower().split())


# Openings that make a bullet weaker than it needs to be.
WEAK_STARTS = ("responsible for", "helped", "worked on", "assisted", "participated in",
               "tasked with", "involved in")
# A bullet longer than this usually wraps to a third line on the page.
MAX_BULLET_CHARS = 210


def check_resume(resume: Resume, master: Profile, also_allowed: str = "") -> tuple[list[str], list[str]]:
    """Check a tailored resume only uses facts from the master profile.

    Returns (errors, warnings). Errors are made-up facts: a school, job or project that isn't in
    the profile, changed titles or dates, skills or numbers the profile never mentions. Warnings
    are style problems worth fixing. also_allowed is extra text whose numbers may be used.
    """
    errors, warnings = [], []
    if not _same(resume.name, master.name) or not _same(resume.email, master.email):
        errors.append("name and email must be the profile's")
    if resume.phone and not _same(resume.phone, master.phone):
        errors.append(f"phone {resume.phone!r} isn't the profile's")
    for link in resume.links:
        if not any(_same(link, known) for known in master.links):
            errors.append(f"link {link!r} isn't in the profile")

    for school in resume.education:
        known = next((s for s in master.education if _same(s.school, school.school)), None)
        if not known:
            errors.append(f"school {school.school!r} isn't in the profile")
        elif not (_same(school.degree, known.degree) and _same(school.dates, known.dates)
                  and _same(school.gpa, known.gpa)):
            errors.append(f"{school.school}: degree, dates and GPA must be as in the profile")
        elif extra := [c for c in school.coursework if not any(_same(c, k) for k in known.coursework)]:
            errors.append(f"{school.school}: coursework {extra} isn't in the profile")
    for job in resume.experience:
        known = next((j for j in master.experience if _same(j.organization, job.organization)), None)
        if not known:
            errors.append(f"employer {job.organization!r} isn't in the profile")
        elif not (_same(job.title, known.title) and _same(job.dates, known.dates)):
            errors.append(f"{job.organization}: title and dates must be as in the profile "
                          f"({known.title}, {known.dates})")
    for project in resume.projects:
        known = next((p for p in master.projects if _same(p.name, project.name)), None)
        if not known:
            errors.append(f"project {project.name!r} isn't in the profile")
        elif project.url and not _same(project.url, known.url):
            errors.append(f"{project.name}: the link must be the profile's ({known.url})")
    for activity in resume.activities:
        known = next((a for a in master.activities if _same(a.name, activity.name)), None)
        if not known:
            errors.append(f"activity {activity.name!r} isn't in the profile")
        elif not (_same(activity.role, known.role) and _same(activity.dates, known.dates)):
            errors.append(f"{activity.name}: role and dates must be as in the profile")
    for cert in resume.certifications:
        if not any(_same(cert.name, known.name) for known in master.certifications):
            errors.append(f"certification {cert.name!r} isn't in the profile")

    if made_up := sorted(skills_of(resume) - skills_of(master)):
        errors.append(f"skills the profile never mentions: {', '.join(made_up)}")
    if made_up := sorted(numbers_in(text_of(resume)) - numbers_in(text_of(master) + also_allowed)):
        errors.append(f"numbers the profile never mentions: {', '.join(made_up)}")

    bullets = [b for part in [*resume.projects, *resume.experience, *resume.activities]
               for b in part.bullets]
    for bullet in bullets:
        if bullet.lower().startswith(WEAK_STARTS):
            warnings.append(f"starts weakly (use a strong verb like Built, Designed, Cut): {bullet[:60]!r}")
        if len(bullet) > MAX_BULLET_CHARS:
            warnings.append(f"too long ({len(bullet)} characters; keep under {MAX_BULLET_CHARS}): "
                            f"{bullet[:60]!r}")
    return errors, warnings

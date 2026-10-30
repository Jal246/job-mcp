"""How well a job fits you: can you apply at all (degree, season, country, kind of role), and how
well do your skills match what it asks for, as a score from 0 to 100 with the reasons.

Everything here is a rule you can read, so every score can be explained ("you have 5 of its 7
required skills; missing: Go, Kafka").

This module knows nothing about MCP, so it can be used and tested as plain Python.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from job_mcp import skills
from job_mcp.boards import Job
from job_mcp.profile import Profile, Project, skills_of

# --- What kind of role is it? -----------------------------------------------------------------

# Checked in order on the lowercased title; the first match wins.
ROLE_KINDS = [
    ("design", r"\bdesign(?:er)?\b|\bux\b|\bui\b|user research"),
    ("product", r"product manag|\bapm\b|program manag|product management|product operations"),
    ("business", r"\bsales\b|marketing|\bfinance\b|financial|accounting|revenue|legal|recruit|"
                 r"investment|\bmba\b|partnerships|strategy|customer success|support|"
                 r"communications|\bpeople\b|operations|\bbusiness\b|writer|writing|content"),
    # Before software: these are "engineer" titles too.
    ("hardware", r"hardware|\basic\b|silicon|electrical|circuit|\bfpga\b|\brf\b|wireless|"
                 r"mechanical|manufacturing|analog|\bpcb\b|semiconductor|physical design"),
    ("ml", r"machine learning|\bml\b|\bai\b|applied scien|research (?:engineer|scien)|deep learning|"
           r"\bnlp\b|computer vision|\bllm"),
    ("data", r"\bdata\b|analytics|analyst|statistic"),
    ("security", r"security|appsec|detection"),
    ("infrastructure", r"infrastructure|platform|devops|\bsre\b|site reliability|\bcloud\b|"
                       r"systems engineer|reliability"),
    ("software", r"software|engineer|developer|\bswe\b|front[- ]?end|back[- ]?end|full[- ]?stack|"
                 r"mobile|\bios\b|android|\bweb\b|programmer"),
]


def role_kind(title: str) -> str:
    """software, data, ml, infrastructure, security, design, product, business, or other."""
    lowered = title.lower()
    for kind, pattern in ROLE_KINDS:
        if re.search(pattern, lowered):
            return kind
    return "other"


# --- Can you apply? ---------------------------------------------------------------------------

# Matched in titles ignoring case, except the capitalized abbreviations ("MS", "BS").
DEGREES = {
    "phd": r"\bph\.?\s?d\b|doctoral",
    "masters": r"\bmaster['’]?s\b|(?-i:\bMS\b)|\bm\.s\.|"
               r"\bgraduate (?:student|intern|internship)s?\b|\bintern,\s*graduate\b",
    "mba": r"\bmba\b",
    "high_school": r"high[- ]school",
    "bachelors": r"bachelor['’]?s|undergrad(?:uate)?|(?-i:\bBS\b)|\bb\.s\.|(?-i:\bBA\b)",
}
DEGREE_NAMES = {"phd": "PhD", "masters": "Master's", "mba": "MBA", "high_school": "high school",
                "bachelors": "Bachelor's"}
SEASONS = r"summer|winter|fall|autumn|spring"

US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE "
             "NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
US_PLACES = re.compile(
    r"united states|\busa?\b|\bu\.s\.|america|\bamer\b|nationwide|"
    r"san francisco|new york|seattle|austin|boston|chicago|los angeles|palo alto|mountain view|"
    r"menlo park|foster city|sunnyvale|san jose|san mateo|redwood city|oakland|berkeley|denver|"
    r"atlanta|miami|washington|portland|san diego|philadelphia|pittsburgh|salt lake|"
    rf",\s*(?:{'|'.join(US_STATES)})\b", re.IGNORECASE)
ANYWHERE = re.compile(r"^(?:remote|anywhere|global|worldwide|distributed|hybrid|flexible|remote[- ]first)$",
                      re.IGNORECASE)


def in_country(place: str, country: str) -> bool | None:
    """Whether a job location is in country: True, False, or None when it doesn't say
    ("Remote")."""
    words = re.sub(r"[()\-–,/|]+", " ", place).strip()
    if not words or ANYWHERE.match(" ".join(words.split())):
        return None
    if country.lower() in ("united states", "usa", "us"):
        return bool(US_PLACES.search(place))
    return country.lower() in place.lower()


def _seasons(text: str) -> set[str]:
    return {s.replace("autumn", "fall") for s in re.findall(rf"\b({SEASONS})\b", text.lower())}


def _years(text: str) -> set[int]:
    years = {int(y) for y in re.findall(r"\b(20[2-3]\d)\b", text)}
    years |= {2000 + int(y) for y in re.findall(r"'(2\d|3\d)\b", text)}  # "Summer '27".
    return years


# What postings say about who may work there. Citizenship and clearance rule out everyone who
# isn't a citizen; export-control rules ("U.S. persons") rule out visa holders; "no sponsorship"
# rules out people who need a work visa, and is worth checking for F-1 students (internships
# are usually on CPT, which needs no sponsorship, but some companies mean "now or later").
CITIZENS_ONLY = re.compile(
    r"security clearance|clearance (?:is )?required|\bts/sci\b|"
    r"u\.?s\.? citizenship (?:is )?required|requires? u\.?s\.? citizenship|"
    r"must be (?:a )?u\.?s\.? citizens?|only u\.?s\.? citizens", re.IGNORECASE)
US_PERSONS = re.compile(r"\bitar\b|export control|u\.?s\.? persons?\b", re.IGNORECASE)
NO_SPONSORSHIP = re.compile(
    r"(?:not|unable to|cannot|won't|will not|does not|do not)\s+(?:be able to\s+)?"
    r"(?:offer|provide|sponsor)\w*(?:\s+\w+){0,4}?\s+(?:visa|sponsorship|immigration)|"
    r"without (?:the need for )?(?:current or future |the need for )?(?:visa |employer )?sponsorship|"
    r"\bno (?:visa )?sponsorship", re.IGNORECASE)


def _quote(pattern: re.Pattern, text: str) -> str | None:
    """The sentence of text that matches pattern, shortened."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        if pattern.search(sentence):
            return sentence.strip()[:160]
    return None


def authorization(job: Job, status: str) -> tuple[list[str], list[str]]:
    """(blockers, warnings) from what the posting says about who may work there."""
    blockers, warnings = [], []
    if status == "us_citizen":
        return blockers, warnings
    if quote := _quote(CITIZENS_ONLY, job.description):
        blockers.append(f"requires US citizenship or a security clearance: {quote!r}")
    elif status != "green_card" and (quote := _quote(US_PERSONS, job.description)):
        blockers.append(f"export-controlled (US citizens and permanent residents): {quote!r}")
    elif status != "green_card" and (quote := _quote(NO_SPONSORSHIP, job.description)):
        if status == "needs_sponsorship":
            blockers.append(f"won't sponsor a visa: {quote!r}")
        else:
            warnings.append(f"says it won't sponsor visas; internships on CPT usually don't need "
                            f"it, but ask the recruiter: {quote!r}")
    return blockers, warnings


def eligibility(job: Job, profile: Profile) -> tuple[list[str], list[str]]:
    """(blockers, warnings): reasons you can't apply, and things to check before you do."""
    prefs = profile.preferences
    blockers, warnings = [], []
    title = job.title.lower()
    opening = job.description[:2000].lower()

    # Degree: a title naming other degrees ("PhD Intern") is for those students.
    named = {degree for degree, pattern in DEGREES.items()
             if re.search(pattern, job.title, re.IGNORECASE)}
    if named and prefs.degree not in named:
        blockers.append(f"for {' or '.join(DEGREE_NAMES[d] for d in sorted(named))} students")
    elif not named and prefs.degree == "bachelors" and re.search(
            r"(?:pursuing|enrolled in|currently in) an? (?:ph\.?d|master)", opening) and not re.search(
            DEGREES["bachelors"], job.description, re.IGNORECASE):
        warnings.append("the description asks for graduate students")

    # Season and year, for internships.
    if job.level == "intern" and prefs.seasons:
        wanted = {season.split()[0].lower().replace("autumn", "fall") for season in prefs.seasons}
        wanted_years = _years(" ".join(prefs.seasons))
        in_title = _seasons(job.title)
        if in_title and not in_title & wanted:
            blockers.append(f"a {'/'.join(sorted(in_title))} internship (you want "
                            f"{', '.join(prefs.seasons)})")
        elif years := _years(job.title):
            if wanted_years and not years & wanted_years:
                blockers.append(f"for {', '.join(map(str, sorted(years)))} (you want "
                                f"{', '.join(prefs.seasons)})")
        elif not in_title and (described := _seasons(opening)) and not described & wanted:
            warnings.append(f"the description mentions {'/'.join(sorted(described))}, "
                            f"not {', '.join(prefs.seasons)}")

    # Graduation date: "students graduating in December 2027 or later".
    if prefs.graduation[:4].isdigit():
        grad_year = int(prefs.graduation[:4])
        for sentence in re.split(r"(?<=[.!?])\s+|\n", job.description):
            if re.search(r"graduat|class of", sentence, re.IGNORECASE):
                years = _years(sentence)
                if years and grad_year not in years and (
                        max(years) < grad_year and "or later" not in sentence.lower()):
                    warnings.append(f"asks for graduation in {'/'.join(map(str, sorted(years)))} "
                                    f"(you graduate {grad_year}): {sentence.strip()[:150]}")
                    break

    # Country: jobs only in places outside the countries you can work in.
    if prefs.work_countries and job.locations:
        verdicts = [[in_country(place, country) for country in prefs.work_countries]
                    for place in job.locations]
        if all(v is False for verdict in verdicts for v in verdict):
            blockers.append(f"based in {'; '.join(job.locations[:3])}, outside "
                            f"{' and '.join(prefs.work_countries)}")

    # Work authorization.
    more_blockers, more_warnings = authorization(job, prefs.work_authorization)
    blockers += more_blockers
    warnings += more_warnings

    # Kind of role.
    kind = role_kind(job.title)
    if prefs.roles and kind not in prefs.roles and kind != "other":
        blockers.append(f"a {kind} role, not {'/'.join(prefs.roles)}")
    return blockers, warnings


# --- How well does it match? ------------------------------------------------------------------

@dataclass
class Fit:
    score: int  # 0 to 100.
    kind: str
    blockers: list[str]
    warnings: list[str]
    matched_required: list[str]
    missing_required: list[str]
    matched_preferred: list[str]
    missing_preferred: list[str]
    projects: list[tuple[str, list[str]]] = field(default_factory=list)  # Best first, with shared skills.

    @property
    def eligible(self) -> bool:
        return not self.blockers

    def summary(self) -> str:
        """One line: why it's a good (or bad) match."""
        have = len(self.matched_required)
        total = have + len(self.missing_required)
        parts = [f"you have {have} of {total} required skills" if total else "no specific skills listed"]
        if self.matched_required:
            parts[0] += f" ({', '.join(self.matched_required[:5])})"
        if self.missing_required:
            parts.append(f"missing {', '.join(self.missing_required[:4])}")
        return "; ".join(parts)


def freshness(posted: str | None, today: date | None = None) -> float:
    """1 for a job posted in the last two weeks, falling to 0.1 for one older than two months."""
    if not posted:
        return 0.5
    try:
        age = ((today or date.today()) - date.fromisoformat(posted)).days
    except ValueError:
        return 0.5
    return 1.0 if age <= 14 else 0.7 if age <= 30 else 0.4 if age <= 60 else 0.1


def project_overlap(project: Project, wanted: set[str]) -> list[str]:
    """The wanted skills a project shows."""
    shown = skills.find("\n".join([project.name, ", ".join(project.tech), *project.bullets]))
    shown |= {name for tech in project.tech if (name := skills.canonical(tech))}
    return sorted(shown & wanted)


def assess(job: Job, profile: Profile, today: date | None = None) -> Fit:
    """How well a job fits the profile.

    The score is out of 100: the share of required skills you have (55), of nice-to-have skills
    (15), both smoothed so a job naming only one or two skills doesn't swing to 0 or 100,
    the kind of role being one you want, earlier in your list counting more (20), and how
    fresh the posting is (10). Each warning takes 5 off.
    """
    required, preferred = skills.requirements(job.description)
    required -= skills.TOO_GENERAL
    preferred -= skills.TOO_GENERAL
    have = skills_of(profile)
    blockers, warnings = eligibility(job, profile)
    kind = role_kind(job.title)

    # Smoothed shares ("add one, add two"): a job naming one skill you lack scores 1/3, not 0,
    # and one naming one you have scores 2/3, not 1. Few skills named say little either way.
    required_share = (len(required & have) + 1) / (len(required) + 2)
    preferred_share = (len(preferred & have) + 1) / (len(preferred) + 2)
    roles = profile.preferences.roles
    if kind in roles:
        kind_share = 1.0 - 0.1 * roles.index(kind)
    else:
        kind_share = 0.4 if kind == "other" else 0.0
    score = (55 * required_share + 15 * preferred_share + 20 * max(kind_share, 0)
             + 10 * freshness(job.posted, today) - 5 * len(warnings))

    wanted = required | preferred
    projects = sorted(((p.name, project_overlap(p, wanted)) for p in profile.projects),
                      key=lambda item: -len(item[1]))
    return Fit(
        score=max(0, min(100, round(score))), kind=kind, blockers=blockers, warnings=warnings,
        matched_required=sorted(required & have), missing_required=sorted(required - have),
        matched_preferred=sorted(preferred & have), missing_preferred=sorted(preferred - have),
        projects=projects,
    )

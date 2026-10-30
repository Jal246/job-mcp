"""Tests of the profile, matching, application and tracking tools, through a real MCP client."""

import json

import pytest

from job_mcp import documents, server

from test_profile import ada, resume_of
from test_server import call, text, text_of

pytestmark = pytest.mark.anyio

GRAD_JOB = "greenhouse:acme:101"  # "Software Engineer, New Grad" in Bangalore (Python and Go).
INTERN_JOB = "lever:leverco:lv-1"  # "Software Engineering Intern" in London (Rust).


@pytest.fixture(autouse=True)
def temporary_data(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(server, "STARTER", {"Acme": server.Board("greenhouse", "acme"),
                                            "LeverCo": server.Board("lever", "leverco")})


@pytest.fixture
async def ready():
    """Ada's profile saved, and the fake companies watched."""
    await text("save_profile", {"profile_data": ada().model_dump()})
    await text("add_companies", {"starter_list": True, "companies": ["AshCo"]})


def resume_args(**changes) -> dict:
    return {"resume": resume_of(ada(), **changes).model_dump(), "job_id": GRAD_JOB}


# --- Profile ----------------------------------------------------------------------------------

async def test_profile_tools():
    result = await call("get_profile")
    assert result.is_error and "no profile yet" in text_of(result)
    saved = await text("save_profile", {"profile_data": ada().model_dump()})
    assert saved.startswith("Saved the profile of Ada Example: 2 projects, 1 jobs, ")
    assert "Looking for: intern, new_grad roles (software, data, ml) in Bangalore, London, Remote, " \
           "Summer 2027; graduating 2028-05, bachelors degree." in saved
    assert json.loads(await text("get_profile"))["name"] == "Ada Example"


async def test_save_profile_warns_about_weak_bullets():
    me = ada()
    me.projects[1].bullets = ["Helped with a notes app in Python."]
    saved = await text("save_profile", {"profile_data": me.model_dump()})
    assert "Bullets worth improving:\n- starts weakly" in saved


async def test_import_resume(tmp_path):
    pdf = tmp_path / "resume.pdf"
    documents.render_resume(resume_of(ada()), pdf)
    reply = await text("import_resume", {"path": f'"{pdf}"'})
    assert reply.startswith("# The resume's text\n\nADA EXAMPLE")
    assert "There's no profile yet." in reply and "call save_profile" in reply
    notes = tmp_path / "resume.md"
    notes.write_text("# Ada\nPython person", encoding="utf-8")
    assert "Python person" in await text("import_resume", {"path": str(notes)})
    for path, message in [(tmp_path / "missing.pdf", "there's no file"),
                          (tmp_path / "resume.docx", "must be a PDF")]:
        if path.suffix == ".docx":
            path.write_bytes(b"PK")
        result = await call("import_resume", {"path": str(path)})
        assert result.is_error and message in text_of(result)


# --- Matching ---------------------------------------------------------------------------------

async def test_match_jobs(ready, fake_boards):
    fake_boards[("ashby", "ashco")]["jobs"].append({
        "id": "as-4", "title": "Product Design Intern", "jobUrl": "https://jobs.ashbyhq.com/ashco/as-4",
        "location": "Remote", "isRemote": True, "employmentType": "Intern", "descriptionPlain": "Design."})
    await text("refresh_jobs")
    reply = await text("match_jobs")
    # Ada wants intern/new grad software, data or ML roles in Bangalore, London or Remote.
    # The Product Design Intern (remote) is a design role, so it's hidden.
    assert reply.startswith("2 jobs fit your profile (intern, new_grad, Bangalore or London or "
                            "Remote, Summer 2027); the best 2 roles:\nHidden 1 you can't apply to: "
                            "1 a design role, not software/data/ml")
    first, second = reply.split("\n\n")[1:3]
    assert first.startswith("1. **Software Engineer, New Grad** · Acme · fit ")
    assert "Why: you have 2 of 2 required skills (Go, Python)" in first
    assert second.startswith("2. **Software Engineering Intern** · LeverCo")
    assert "missing Rust" in second
    everything = await text("match_jobs", {"include_ineligible": True})
    assert "Can't apply: a design role" in everything


async def test_match_jobs_needs_a_profile():
    result = await call("match_jobs")
    assert result.is_error and "no profile yet" in text_of(result)


async def test_explain_fit(ready):
    reply = await text("explain_fit", {"job_id": INTERN_JOB})
    assert reply.startswith("# Software Engineering Intern at LeverCo: fit ")
    assert "You can apply." in reply
    assert "Required skills: missing Rust" in reply
    assert "Projects that show what it wants: none directly" in reply


# --- Resumes ----------------------------------------------------------------------------------

async def test_tailor_resume(ready):
    reply = await text("tailor_resume", {"job_id": GRAD_JOB})
    assert reply.startswith("# Tailor a resume: Software Engineer, New Grad at Acme\njob_id: greenhouse:acme:101")
    assert "Fit " in reply and "Payments API (Go)" in reply
    assert '"name": "Ada Example"' in reply and '"preferences"' not in reply
    assert "## How to write it" in reply


async def test_save_resume_for_a_job(ready, tmp_path):
    reply = await text("save_resume", resume_args())
    folder = tmp_path / "data" / "applications" / "acme-software-engineer-new-grad-101"
    assert reply.splitlines() == [
        f"Saved a one-page resume: {folder / 'Ada_Example_Resume.pdf'}",
        "ATS check (the PDF's text, as a screening system reads it): shows 2 of the job's 2 required skills.",
        "Tracking Acme · Software Engineer, New Grad (Interested).",
    ]
    assert json.loads((folder / "resume.json").read_text())["name"] == "Ada Example"
    assert "made: resume" in await text("list_applications")


async def test_save_resume_reports_skills_left_off(ready):
    reply = await text("save_resume", resume_args(
        skills=[{"name": "Languages", "items": ["SQL"]}],
        projects=[{"name": "Notes", "bullets": ["Built a notes app."]}]))
    assert "shows 0 of the job's 2 required skills; you have but didn't show: Go, Python." in reply


async def test_save_resume_general(ready, tmp_path):
    reply = await text("save_resume", {"resume": resume_of(ada()).model_dump()})
    assert reply == f"Saved a one-page resume: {tmp_path / 'data' / 'resume' / 'Ada_Example_Resume.pdf'}"


async def test_save_resume_rejects_made_up_facts(ready):
    result = await call("save_resume", resume_args(skills=[{"name": "Languages", "items": ["Rust"]}]))
    assert result.is_error
    assert ("Not saved: the resume has facts that aren't in the profile:\n"
            "- skills the profile never mentions: Rust\n") in text_of(result)


async def test_save_resume_rejects_two_pages(ready, tmp_path):
    long = "Built a notes app in Python that people found useful, every single day of the week. " * 2
    result = await call("save_resume", resume_args(
        projects=[{"name": "Notes", "bullets": [long] * 40}]))
    assert result.is_error and "Not saved: it runs to 3 pages even set at 90% size" in text_of(result)
    assert not list((tmp_path / "data" / "applications").rglob("*.pdf"))


# --- Cover letters ----------------------------------------------------------------------------

LETTER = [
    "I'm applying for the Software Engineer, New Grad role at Acme. Building payments APIs with "
    "Python and Go is exactly the work I want to do.",
    "My Payments API project is a Go service that handles 1,200 requests per second, backed by "
    "85 tests against a fake bank. It taught me to design for failure first and to measure "
    "before optimizing anything, which is how I'd approach your payments work.",
    "Beyond code, serving 200 customers a day as a barista taught me to stay calm and clear when "
    "things get busy, and to communicate well with people who need something from me quickly. "
    "I learn new tools fast and I enjoy asking good questions.",
    "I'd welcome the chance to talk about how I could help your team. Thank you for your time "
    "and consideration; I look forward to hearing from you about next steps for Summer 2027.",
]


async def test_cover_letter(ready, tmp_path):
    brief = await text("write_cover_letter", {"job_id": GRAD_JOB})
    assert brief.startswith("# Cover letter: Software Engineer, New Grad at Acme")
    assert "## About the company and role (from the posting)\nBuild payments APIs with Python and Go." in brief
    reply = await text("save_cover_letter", {"job_id": GRAD_JOB, "paragraphs": LETTER})
    folder = tmp_path / "data" / "applications" / "acme-software-engineer-new-grad-101"
    assert reply.splitlines()[0].startswith("Saved the cover letter (")
    assert (folder / "Ada_Example_Cover_Letter.pdf").exists()
    plain = (folder / "cover_letter.txt").read_text(encoding="utf-8")
    assert plain.startswith("Dear Hiring Team,\n\nI'm applying") and plain.endswith("Sincerely,\nAda Example\n")
    assert "made: cover letter" in await text("list_applications")


async def test_cover_letter_checks(ready):
    for paragraphs, message in [
        (["Too short."], "it has 1 paragraphs"),
        ([*LETTER[:3], "I'd love to join [Team Name]. Thanks!"], "fill in the placeholders: [Team Name]"),
        ([LETTER[0].replace("role", "role, which 9,000 people applied for"), *LETTER[1:]],
         "numbers that aren't in the profile or the posting: 9000"),
    ]:
        result = await call("save_cover_letter", {"job_id": GRAD_JOB, "paragraphs": paragraphs})
        assert result.is_error and message in text_of(result), text_of(result)


async def test_cover_letter_cliches(ready):
    letter = [LETTER[0].replace("Building", "I am passionate about building"), *LETTER[1:]]
    reply = await text("save_cover_letter", {"job_id": GRAD_JOB, "paragraphs": letter})
    assert "Style: avoid the cliché 'passionate'" in reply


# --- Interviews and tracking ------------------------------------------------------------------

async def test_prep_interview(ready):
    reply = await text("prep_interview", {"job_id": GRAD_JOB})
    assert reply.startswith("# Interview prep: Software Engineer, New Grad at Acme")
    assert "## The resume they sent" not in reply
    await text("save_resume", resume_args())
    assert "## The resume they sent" in await text("prep_interview", {"job_id": GRAD_JOB})


async def test_track_and_list_applications(ready, monkeypatch, fake_boards):
    assert (await text("list_applications")).startswith("No applications tracked yet")
    reply = await text("track_application", {"job_id": GRAD_JOB, "status": "applied",
                                             "note": "Applied on the website"})
    assert reply.startswith("Acme · Software Engineer, New Grad: Applied\nHistory:\n- ")
    assert reply.endswith(" Applied: Applied on the website")
    await text("track_application", {"job_id": INTERN_JOB, "status": "oa", "deadline": "2020-01-01"})

    listing = await text("list_applications")
    assert listing.startswith("Pipeline: 1 applied, 1 online assessment")
    assert "deadline 2020-01-01 has passed" in listing
    monkeypatch.setattr(server, "FOLLOW_UP_DAYS", 0)
    assert "no news in 0 days: consider a polite follow-up" in await text("list_applications")
    assert "Online assessment" not in await text("list_applications", {"statuses": ["applied"]})

    # The job is taken off its board: the application says so.
    fake_boards[("greenhouse", "acme")]["jobs"].pop(0)
    await text("refresh_jobs")
    assert "taken off the board on" in await text("list_applications")


async def test_track_application_survives_removing_the_company(ready):
    await text("track_application", {"job_id": GRAD_JOB, "status": "applied"})
    await text("remove_company", {"company": "Acme"})
    reply = await text("track_application", {"job_id": GRAD_JOB, "status": "interview"})
    assert reply.startswith("Acme · Software Engineer, New Grad: Interviewing")
    result = await call("track_application", {"job_id": "nope", "status": "applied"})
    assert result.is_error and "no job with id 'nope'" in text_of(result)
    result = await call("track_application", {"job_id": GRAD_JOB, "status": "oa", "deadline": "soon"})
    assert result.is_error and "deadline must be a date" in text_of(result)

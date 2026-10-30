# job-mcp

A Python [MCP](https://modelcontextprotocol.io) server that turns Claude into a job-search assistant: it watches companies' own job boards, ranks every open job by how well it fits **you**, writes a tailored one-page resume and cover letter for each application (checked so nothing is made up), preps you for interviews, and tracks every application.

No API keys and no scraping of sites that forbid it: Greenhouse, Lever and Ashby, the job boards thousands of companies use for their careers pages, all have free public APIs meant to be read. Big companies' own career sites (Workday sites like NVIDIA's, Salesforce's and Adobe's, plus Amazon and Microsoft) are read through the JSON their careers pages load, politely: only early-career jobs, and only new jobs' details. And no LLM API key either: Claude, already in your terminal, does the writing; the server supplies the facts, the rules and the checks.

## Demo

> **You:** Find internships that fit me.
>
> **Claude** calls `match_jobs`:
>
> ```
> 27 jobs fit your profile (intern, Bay Area or Remote, Summer 2027); the best 10 roles:
> Hidden 32 you can't apply to: 10 for PhD students, 8 a winter internship, 5 a design role,
> 3 outside United States, 2 for Master's students, 2 a product role
>
> 1. **Software Engineer Intern (Summer 2027)** · Figma · fit 72/100
>    San Francisco, CA • New York, NY · posted 2026-09-14 · $55—$55 USD
>    Why: you have 3 of 4 required skills (Java, JavaScript, Python); missing C++
> ```
>
> **You:** Tailor my resume and write a cover letter for the Figma one.
>
> **Claude** calls `tailor_resume`, picks and rewords the profile's most relevant projects for Figma's posting, and calls `save_resume`: a one-page PDF, checked against the master profile (a skill, employer or number that isn't in it is rejected) and for the job's keywords as a screening system would read them. Then `write_cover_letter` and `save_cover_letter` do the same for a 280-word letter.

## What it can do

**Finding jobs**

| Tool | What it's for |
|---|---|
| `add_companies` | Watch companies by name ("Stripe": the board is found on Greenhouse, Lever or Ashby automatically), careers-page URL, or `source:board`. Workday careers-page URLs, "Amazon" and "Microsoft" work too. `starter_list=True` adds 76 tech companies (about 16,000 jobs). |
| `search_jobs` | Full-text search (SQLite FTS5 with BM25 ranking; title matches first) with filters for places, remote, level, company, posting date and "new since". "India" finds "Bangalore", "Bay Area" finds Palo Alto, and the same role posted for many cities is shown once. |
| `get_job` | A job's full description and details. |
| `refresh_jobs` | Fetch every board now: new jobs per company; jobs taken down are marked closed. |
| `list_companies`, `remove_company` | The watch list. |

**Your profile and fit**

| Tool | What it's for |
|---|---|
| `import_resume`, `save_profile`, `get_profile` | Your master profile: every fact a resume may use (more than fits on a page), and what you're looking for (degree, graduation, seasons, places, countries, kinds of role). |
| `match_jobs` | Every job ranked by a fit score (0-100): required skills you have, nice-to-haves, kind of role, freshness. Hides the ones you can't apply to (other degrees, seasons, countries, kinds of role) and says how many. |
| `explain_fit` | Why: the skills you have and miss, which of your projects show what it wants. |

**Applying**

| Tool | What it's for |
|---|---|
| `tailor_resume`, `save_resume` | A one-page tailored resume PDF: chosen and reworded from your profile, never added to. Made-up skills, employers, schools, links or numbers are rejected; an overflowing page is set slightly smaller, or sent back to cut. Includes an ATS keyword check. |
| `write_cover_letter`, `save_cover_letter` | A cover letter PDF (matching the resume's header) plus plain text for application forms. Checks length, placeholders, numbers, and clichés. |
| `prep_interview` | A prep sheet: likely technical topics, project deep-dive questions, behavioral STAR stories from your profile, gaps to close, questions to ask; then a mock interview. |
| `track_application`, `list_applications` | Your pipeline (interested → applied → online assessment → interview → offer), with history, deadlines, follow-up reminders and jobs that were taken down. |

## How it works

```
src/job_mcp/
  net.py        polite HTTP: timeouts, retries when a server is busy
  boards.py     Greenhouse / Lever / Ashby → one Job shape; finding a company's board
  careers.py    Workday, Amazon and Microsoft career sites: early-career jobs, new ones' details only
  classify.py   level from the title, remote/hybrid/onsite, salary ranges, HTML → text
  store.py      SQLite + FTS5: watch list, jobs, new/closed tracking, search
  skills.py     150+ skills and how jobs write them; a job's must-haves vs nice-to-haves
  profile.py    the master profile, and the check that a resume only uses its facts
  fit.py        can you apply (degree, season, country, role) and the fit score
  documents.py  resume and cover letter PDFs (reportlab), and reading PDFs back as text
  tracker.py    applications and their history
  guides.py     how the AI writes resumes, cover letters and interview prep
  starter.py    the starter watch list
  server.py     the MCP tools
```

Only `server.py` knows about MCP; everything else is plain Python, tested on its own (206 tests, with fake job boards, so they never touch the network).

## Setup

Needs [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Jal246/job-mcp
cd job-mcp
uv sync
claude mcp add -s user jobs -- uv run --directory /path/to/job-mcp job-mcp
```

Then ask Claude to import your resume (`import_resume` with its path) and add the starter companies.

Everything you create lives in `data/` (set `JOB_DATA_DIR` to keep it elsewhere): the jobs database, your profile, and a folder of documents per application. It's ignored by git, so your personal details never get committed.

## Tests

```sh
uv run pytest
```

## Roadmap

- **Phase 4:** a Next.js dashboard (kanban of applications, daily "new for you" feed) and a scheduled daily scan.

# job-mcp

A Python [MCP](https://modelcontextprotocol.io) server that lets Claude hunt for jobs for you, straight from companies' own job boards: watch a list of companies, keep every open job in a local database that stays up to date, and search them by words, place, level (intern, new grad, senior...) and freshness.

No API keys and no scraping of sites that forbid it: Greenhouse, Lever and Ashby, the job boards thousands of companies use for their careers pages, all have free public APIs meant to be read.

## Demo

> **You:** Find me new grad or intern software engineering jobs in India.
>
> **Claude** calls `search_jobs` with `query="software engineer"`, `locations=["India"]`, `levels=["new_grad", "intern"]`, and gets back (shortened):
>
> ```
> 1. **Software Engineer, New Grad** · Stripe
>    new_grad · Bangalore; Taiwan; Singapore (+5 more places) · posted 2026-10-28
>    id: greenhouse:stripe:8130876 · https://stripe.com/jobs/search?gh_jid=8130876
>    (the same role has 7 more postings for other places)
> ```
>
> Then `get_job` reads the full description, so Claude can tell you what they're looking for.

With the starter list, that's about 8,500 open jobs at 39 companies, searched in a tenth of a second.

## What it can do

| Tool | What it's for |
|---|---|
| `add_companies` | Watch companies by name ("Stripe": the board is found on Greenhouse, Lever or Ashby automatically), careers-page URL, or `source:board`. `starter_list=True` adds 39 tech companies (Stripe, OpenAI, Anthropic, Databricks, Figma, Notion, Spotify, Groww, CRED, Meesho...). |
| `search_jobs` | Full-text search (SQLite FTS5 with BM25 ranking; title matches first) with filters for places, remote, level, company, posting date and "new since". Countries match their cities ("India" finds "Bangalore"), and the same role posted for many cities is shown once. Boards older than 6 hours are refreshed first. |
| `get_job` | A job's full description and details (salary when the board or description gives one), in parts if it's long. |
| `refresh_jobs` | Fetch every board now: reports new jobs per company, and marks jobs taken down as closed. |
| `list_companies`, `remove_company` | See the watch list (open jobs, last refresh, errors, jobs by level), and tidy it. |

## How it works

```
src/job_mcp/
  net.py       polite HTTP: timeouts, retries when a server is busy
  boards.py    Greenhouse / Lever / Ashby → one Job shape; finding a company's board
  classify.py  level from the title, remote/hybrid/onsite, salary ranges, HTML → text
  store.py     SQLite + FTS5: watch list, jobs, new/closed tracking, search
  starter.py   the starter watch list
  server.py    the MCP tools
```

Only `server.py` knows about MCP; everything else is plain Python, tested on its own.

## Setup

Needs [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Jal246/job-mcp
cd job-mcp
uv sync
claude mcp add jobs -- uv run --directory /path/to/job-mcp job-mcp
```

Your jobs database is `data/jobs.db` (set `JOB_DATA_DIR` to keep it elsewhere). It's ignored by git.

## Tests

```sh
uv run pytest
```

The tests use fake job boards, so they never touch the network.

## Roadmap

- **Phase 2:** your resume: parse it, score every job against it, explain the skill gaps; an application tracker.
- **Phase 3:** tailor your resume and write cover letters for a job; interview prep from the job description.
- **Phase 4:** a Next.js dashboard (kanban of applications, daily "new for you" feed) and a scheduled daily scan.

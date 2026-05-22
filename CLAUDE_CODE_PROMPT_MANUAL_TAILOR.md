# Claude Code starter prompt — Manual job-URL tailor feature

Paste this into a fresh Claude Code session at `/Users/jarvis/dev/jarvis/job-pipeline/`.
It assumes the MCPs and plugins listed in §3 are connected; if any aren't,
ask Vishal to enable them before proceeding instead of falling back to less
precise tools.

---

You are joining an active job-application automation project owned by
Vishal Pathak. Your task today is to add a **manual job-URL tailoring**
entry point so Vishal can paste a job-posting URL into the dashboard and
have the tailor produce a resume + cover letter for that single posting,
bypassing the discovery/scoring pipeline.

## 1. Ramp-up

Drive ramp-up through the **graphify skill** — it's installed in this
Claude Code environment. The flow is:

1. **Read `CLAUDE.md`** at this repo root first — narrative profile +
   the unified `jobpipe-hunt / jobpipe-tailor / jobpipe-submit`
   architecture, plus the candidate's application-form defaults. This
   is the only "must-read-in-full" file.
2. **Invoke graphify to build the graph for this repo** if
   `graphify-out/` does not already exist here:
   ```
   graphify build .
   ```
   The build is cached by SHA256, so reruns are cheap. If
   `graphify-out/` already exists, run `graphify build .` only if the
   repo has changed since the cache was written.
3. **Open `graphify-out/GRAPH_REPORT.md`** for the corpus summary, god
   nodes, community hubs, surprising connections, and suggested
   questions.
4. **Query the graph** with `graphify query` for anything you need
   beyond that. Examples for THIS feature:
   ```
   graphify query "what does process_approved_jobs do and what calls it"
   graphify query "how does the tailor resolve a URL into a jobs row"
   graphify query "what helpers exist in jobpipe/shared for job IDs and ATS detection"
   graphify query "where does the tailor write generated PDFs to Supabase Storage"
   ```
   Treat `graphify query` as your default "where is X" tool. Avoid
   broad `grep`/`find` sweeps until the graph has been queried — it
   returns better-scoped answers and costs less context.
5. **Targeted reads** based on what the graph points you at. For this
   feature the likely list is:
   - `jobpipe/tailor/pipeline.py` (`run_tailor_only` line 895,
     `run_submit_only` line 948, and the `process_approved_jobs()`
     helper they wrap)
   - `jobpipe/tailor/DATA_CONTRACT.md` and `jobpipe/tailor/url_resolver.py`
   - `jobpipe/shared/{jobid,ats_detect,html}.py`
   - `jobpipe/hunt/sources/` adapters that match the ATS kinds you
     expect to scrape (`greenhouse.py`, `lever.py`, `ashby.py`,
     `workday.py`)
6. **Dashboard repo ramp-up.** The dashboard lives at
   `/Users/jarvis/dev/jarvis/portfolio/` — a Next.js 15 + TypeScript
   app deployed to vishal.pa.thak.io. Before touching it, `cd` there
   and run `graphify build .` (no `graphify-out/` exists there yet).
   Then read its `CLAUDE.md`, the resulting `GRAPH_REPORT.md`, and use
   `graphify query` for "where does X live" questions. Surface you
   will extend:
   - `app/dashboard/` — the form goes here
   - `app/api/dashboard/jobs/[job_id]/*/route.ts` — pattern for
     per-job API routes
   - `app/api/dashboard/runs/tailor/route.ts` — existing endpoint
     that already triggers tailor runs; the manual flow should reuse
     or extend it
   - `app/lib/supabase.ts` — the existing Supabase client
   - `app/api/materials/[jobId]/[kind]/route.ts` — how generated
     materials are served back to the dashboard
7. **Cross-repo context (optional).** The three legacy repos at
   `../job-hunter/`, `../job-applicant/`, `../job-submitter/` are the
   pre-consolidation versions of this codebase. Their
   `graphify-out/` directories already exist and the per-function
   notes in their `obsidian/` folders can be useful historical
   reference, but treat `job-pipeline` as canonical.

Persist anything non-obvious you learn during ramp-up via the
**memory MCP** so subsequent sessions don't redo this work.

## 2. Feature spec

**User story.** Vishal finds a job posting through a forward, a tweet,
or a hand-search. He pastes the URL into a form on his dashboard. The
backend resolves the URL into a `jobs` table row (no scoring, no
discovery), then runs the existing tailor against just that row.

**Acceptance criteria.**
- New console script `jobpipe-tailor-one <URL>` (or similar name fitting
  the existing `jobpipe-*` convention) that:
  1. Resolves the URL → canonical job_id (reuse `shared.jobid`,
     `shared.ats_detect`).
  2. Scrapes title / company / description / location with whatever
     extractor in `jobpipe/hunt/sources/` matches the ATS kind, falling
     back to a generic Playwright-driven scrape if no source adapter
     fits.
  3. Upserts a `jobs` row with `status='approved'` (or whatever status
     the tailor's `process_approved_jobs()` consumes) and a
     `source='manual'` marker so downstream code can tell it apart from
     discovered jobs.
  4. Calls `process_approved_jobs(job_id=...)` (refactor the existing
     function to accept a single-job filter if needed) to produce the
     tailored resume + cover letter, written to Supabase Storage under
     `job-materials/{job_id}/` per the existing contract.
- New HTTP endpoint in the dashboard repo (`portfolio`) that accepts a
  URL POST, scrapes + upserts the `jobs` row, and triggers the tailor
  run. Pattern after `app/api/dashboard/jobs/[job_id]/*/route.ts` and
  reuse / extend `app/api/dashboard/runs/tailor/route.ts` rather than
  building a parallel trigger.
- Dashboard form under `app/dashboard/`: single text input (URL) +
  submit button + a status area showing the resulting `job_id` and
  links to the generated materials. Match the existing dashboard
  aesthetic — don't introduce a new design system for one form. The
  portfolio root has `PROMPT_fix_dashboard_styling.md` and similar
  per-feature `PROMPT_*.md` files; drop a `PROMPT_manual_tailor_form.md`
  there summarizing the work after it lands.
- Tests in `tests/` covering: URL → job_id resolution, manual-row
  upsert, single-job tailor invocation, end-to-end with a fixture URL.

**Out of scope for this PR.** Submission. The submitter pipeline
already handles `status='tailored'` rows; no changes needed there.

## 3. MCP / plugin / skill usage map

Use these explicitly — don't reinvent what they already do.

| Tool | Use it for |
|---|---|
| **graphify skill** | Default "where is X" / "what calls Y" tool. Re-run `graphify build .` after non-trivial changes so the graph stays current. Use `graphify query` instead of broad grep/find — it costs less context and returns better-scoped answers. |
| **superpowers plugin** | The TDD / planning / code-review skills it ships. Invoke a planning skill before writing code; invoke the code-review skill before opening the PR. |
| **sequential-thinking MCP** | The initial planning pass over this spec — decomposition, risk-listing, ordering of subtasks. Don't skip this; this feature touches three layers. |
| **memory MCP** | Persist any decisions you make (naming conventions for the new console script, schema additions, etc.) so the next session can pick up where you left off. |
| **context7 MCP** | Pull current docs for: `supabase-py` (row inserts), `playwright` Python API (generic scrape), `FastAPI` or `Flask` (HTTP endpoint), and the Vercel Python runtime if you use a Vercel Function. |
| **playwright MCP** | Build and test the generic-fallback scraper. Validate against 3+ real job-posting URLs before declaring the scraper done. |
| **claude.ai Supabase MCP** | Insert / inspect rows in the `jobs` table to verify the manual-row upsert works against the live schema, and to confirm Storage uploads land in `job-materials/{job_id}/`. **Read-mostly.** Do not delete production rows; if you need a sandbox, ask Vishal to point you at a dev project. |
| **claude.ai Vercel MCP** + **vercel plugin** | Deploy the dashboard changes + any Vercel Function. Preview deploy first, never push straight to prod. Use the plugin's helpers for env-var management. |
| **github MCP** | Open the PR. Title format: `feat(tailor): manual job-URL entry point (PR-N)` matching this repo's PR-numbering convention. Body should reference this file. |
| **ui-ux-pro-max plugin** | Design the dashboard form. Apply its accessibility + design-system skills; match the existing portfolio aesthetic (dark, restrained, no busy graphics). |
| **exa MCP** | Only if you need to look up real-world job-posting URL patterns or example ATS markup for the fallback scraper. Not for general code search — the graphify outputs cover that. |

## 4. Suggested execution order

1. **Plan** with sequential-thinking + superpowers planning skill. Output
   the subtask list and risks. Persist via memory MCP.
2. **Refactor first.** `process_approved_jobs()` likely loops over all
   approved rows. Add a `job_ids: list[str] | None = None` filter
   parameter and prove existing behavior is unchanged with tests.
3. **URL → row.** Wire `shared.jobid` + `shared.ats_detect` +
   per-source adapters + Playwright fallback into a new
   `jobpipe.tailor.manual.resolve_url(url) -> JobsRow` function. Test
   against 3 fixtures from different ATSes.
4. **CLI entry point.** Add `jobpipe-tailor-one` to `[project.scripts]`
   in `pyproject.toml`. Smoke-test locally end-to-end.
5. **HTTP endpoint.** FastAPI route, Vercel Function wrapper, env-var
   plumbing.
6. **Dashboard form.** Single input, submit, status display. Use the
   ui-ux-pro-max plugin's helpers.
7. **PR.** Run the superpowers code-review skill on your diff before
   opening the GitHub PR.

## 5. Constraints

- **No live submissions.** This feature ends at `status='tailored'`.
  The submitter is a separate downstream concern.
- **Profile data is canonical at `profile/`** in this repo (per
  `DATA_CONTRACT.md`). Don't synthesize candidate fields — read from
  the profile loader.
- **Treat the manual rows as first-class `jobs` rows.** Use the
  existing materials_hash flow, the existing Storage paths, the
  existing notification path. The only difference from a discovered
  job is `source='manual'` and the absence of a score.
- **Don't add a second Supabase client.** Reuse `jobpipe.db`.
- **Ask, don't guess, when:** the URL extractor is uncertain (low
  confidence on company / title), or when the form would force Vishal
  to fill in fields the discovery pipeline normally infers. Surface a
  confirmation step instead of writing a half-empty row.

## 6. Verification before declaring done

- Console script runs end-to-end against a fresh Greenhouse, Lever, and
  Ashby URL.
- Generated resume PDF + cover letter PDF appear in
  `job-materials/{job_id}/` in Supabase Storage.
- Dashboard form successfully triggers the flow and surfaces links to
  the generated materials.
- All existing tests pass (`pytest`).
- The new tests cover: URL resolution, single-job filter, scraper
  fallback, HTTP endpoint contract.
- `graphify build` rerun so the next agent inherits an accurate map.

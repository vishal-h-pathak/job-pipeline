# jobpipe.tailor

Application-prep + DOM-based form-fill pipeline for Vishal Pathak.
Reads approved jobs from Supabase (written by the `jobpipe.hunt`
subpackage — PR-9 unified what was previously a sibling `job-hunter`
repo into this monorepo), tailors a resume + cover letter + form-answer
drafts, opens a visible browser, fills standard fields via per-ATS
Playwright handlers (Ashby / Greenhouse / Lever) or a prepare-only
vision agent, and **stops at the form's Submit button**.

The system never clicks Submit. The human reviews the visible browser,
clicks Submit themselves, and then clicks "Mark Applied" in the
dashboard cockpit. That click — not any system signal — is the source
of truth that the job was actually submitted.

---

## Project structure

```
pipeline.py            # Entry point — process_approved_jobs / process_prefill_requested_jobs (M-7) — wired as `jobpipe-tailor` (PR-4 rename of main.py)
db.py                  # Supabase read/write
storage.py             # PDF upload/download against Supabase Storage
notify.py              # Resend email notifications
DATA_CONTRACT.md       # User-layer / system-layer file boundary (J-10)
prompts/               # Versioned prompts (J-7)
  _shared.md           # Global rules — anti-slop, voice, ethics (J-5)
  tailor_resume.md     # JSON-output resume tailoring
  tailor_cover_letter.md
  tailor_latex_resume.md
  classify_archetype.md  # Archetype router (J-4)
  star_stories.md      # STAR+R generator (J-3)
  agent_common.md      # Submission agent — common rules (prepare-only post M-4)
  agent_prepare.md     # Submission agent — prepare-only tail (no click_submit)
  form_answers.md      # Form-answer draft generator (M-1, "Block H")
tailor/
  resume.py            # Resume tailoring (JSON metadata)
  cover_letter.py      # Cover letter generation (markdown text)
  latex_resume.py      # LaTeX resume generation + pdflatex compile
  cover_letter_pdf.py  # ReportLab cover letter PDF rendering
  archetype.py         # Archetype classifier + config loader (J-4)
  normalize.py         # ATS Unicode normalization (J-5)
  form_answers.py      # Form-answer draft generator (M-1, "Block H")
url_resolver.py        # Aggregator → real ATS endpoint (PR-4 moved up from applicant/)
applicant/
  base.py              # BaseApplicant abstract class (held for PR-7)
  browser_tools.py     # Playwright tool primitives (held for PR-7)
  __init__.py          # plus PR-4 re-export shims for the moved modules:
                       #   detector.py     → jobpipe.shared.ats_detect
                       #   url_resolver.py → jobpipe.tailor.url_resolver
                       #   agent_loop.py   → jobpipe.submit.adapters.prepare_loop
                       #   ashby.py / lever.py / greenhouse.py / universal.py
                       #     → jobpipe.submit.adapters.prepare_dom.*
interview_prep/
  generator.py         # STAR+R story generator (J-3)
  bank.py              # Supabase r/w for star_stories
scripts/
  migration.sql, migration_storage.sql        # Original schema
  003_legitimacy.sql                          # Posting-legitimacy columns (J-2)
  004_archetype.sql                           # Archetype + confidence columns (J-4)
  005_star_stories.sql                        # STAR+R bank table (J-3)
  006_pattern_analyses.sql                    # Pattern-analysis output table (J-6)
  007_career_ops_alignment.sql                # M-1..M-3: form_answers JSONB,
                                              #          status-flow simplification,
                                              #          stop-at-submit columns
  analyze_patterns.py                         # Closed-loop pattern analyzer (J-6)
  cv_sync_check.py                            # CV / digest / BASE_RESUME drift detector (J-9)
templates/
  VOICE_PROFILE.md     # Tone for cover-letter prose (referenced by prompts)
CLAUDE.md              # Narrative profile aggregator (compat fallback)
```

User-layer ground truth lives in the unified `jobpipe` repo at
`profile/` (top-level) + `jobpipe/hunt/profile/`. `prompts.load_profile()`
scans both via `_resolve_profile_search_dirs()`. See `DATA_CONTRACT.md`.

---

## Pipeline (post M-1..M-7, career-ops alignment)

```
job-hunter writes → status=new
   ↓ (you mark approved in dashboard)
status=approved
   ↓ pipeline.py::process_approved_jobs
[archetype classify] → [resume tailor] → [cover letter]
   → [LaTeX compile + PDF upload] → [STAR+R stories]
   → [form_answers (M-1, gated on score >= 6)]
   → [resolve ATS URL]
status=ready_for_review  (PDFs + form_answers in Postgres; cockpit
                          renders everything for human review)
   ↓ (Pre-fill Form click in /dashboard/review/[job_id])
status=prefilling
   ↓ pipeline.py::process_prefill_requested_jobs (visible browser)
[per-ATS DOM handler — Ashby / Greenhouse / Lever — OR
 prepare-only vision agent for unknown ATSes]
   → [post-fill screenshot uploaded to job-materials Storage]
   → [terminal blocks on input() while browser stays open]
status=awaiting_human_submit
   ↓ (HUMAN reviews visible browser, clicks Submit themselves;
      then comes back and clicks "Mark Applied" in cockpit)
status=applied  (the cockpit click is the source of truth)
```

Every LLM call along the way uses the prompts in `prompts/` and reads
the canonical user-layer profile.

---

## Running manually

```bash
pip install -r requirements.txt
playwright install chromium

jobpipe-tailor --status                    # job counts by status
jobpipe-tailor --test-tailor <job_id>      # smoke-test tailoring + form_answers (read-only)
jobpipe-tailor                             # tailor approved jobs only (no browser).
                                           # Runs process_approved_jobs() and exits.

jobpipe-submit --status                    # same status output as --status above
jobpipe-submit                             # visible-browser pre-fill for any rows the
                                           # cockpit flagged. Terminal blocks on input()
                                           # so you can review and click Submit yourself.

# PR-13 split the previous combined cycle into the two scripts above.
# Run `jobpipe-tailor` whenever there are approved rows to materialize;
# run `jobpipe-submit` when you're at the keyboard and ready to file a
# pre-filled application.
```

Standalone scripts:

```bash
python -m scripts.analyze_patterns         # weekly pattern report (J-6)
python -m scripts.cv_sync_check            # CV / digest drift report (J-9)
```

---

## Environment variables

```
ANTHROPIC_API_KEY=
SUPABASE_URL=
SUPABASE_KEY=
SUPABASE_SERVICE_ROLE_KEY=
CLAUDE_MODEL=claude-sonnet-4-20250514
HUMAN_APPROVAL_REQUIRED=true
AUTO_SUBMIT_ENABLED=false
POLL_INTERVAL_MINUTES=120
```

---

## What changed (career-ops integration, 2026-04-27)

- **J-2**: Scorer (in job-hunter) emits posting-legitimacy as a separate
  axis. Migration `003_legitimacy.sql` lives here for ergonomics.
- **J-3**: STAR+R story generator + `star_stories` table. Stories
  accumulate as a side effect of every tailoring run; `/dashboard/stories`
  curates the master set.
- **J-4**: Archetype classifier routes each JD into one of five lanes
  before tailoring. Persisted on the job row for `/dashboard/insights`.
- **J-5**: `tailor.normalize.normalize_for_ats()` runs on every cover-
  letter + LaTeX bullet before PDF compile.
- **J-6**: `analyze_patterns.py` writes to `pattern_analyses`; the
  insights page renders the latest run.
- **J-7**: Every inline prompt extracted to `prompts/*.md`. Loader
  prepends `_shared.md` once and supports template substitution.
- **J-9**: `cv_sync_check.py` runs as a side effect of `tailor_resume`
  import; warns on drift but never blocks.
- **J-11**: Match Agent → profile writeback writes to
  `profile/learned-insights.md` (top-level; recognized by the user-
  layer loader).

---

## What changed (career-ops alignment, 2026-04-28)

DOM-based form-fill, stop-at-submit, manual-submission cockpit. The
system never clicks Submit anymore.

- **M-1**: `tailor/form_answers.py` produces a structured JSON of
  identity + four narrative fields. Identity / contact / location /
  comp / work-auth / current-employment fields come from `profile.yml`
  in Python — the LLM only drafts `why_this_role`, `why_this_company`,
  optional `additional_info`, and JD-specific `additional_questions`.
  Persisted to `jobs.form_answers` JSONB. Gated on score >= 6.
- **M-2**: Status flow simplified to
  `discovered → approved → preparing → ready_for_review → prefilling →
  awaiting_human_submit → applied`. Migration 007 collapses legacy
  `ready_to_submit` / `submit_confirmed` / `submitting` rows into
  `ready_for_review` and adds the stop-at-submit columns
  (`submission_url`, `prefill_screenshot_path`, `prefill_completed_at`,
  `submitted_at`, `submission_notes`).
- **M-3**: New per-ATS DOM handlers for Greenhouse and Lever (modeled
  on `applicant/ashby.py`). All three are pure Playwright — zero
  Anthropic API calls — and read from `jobs.form_answers`. `ashby.py`
  refactored to read from `form_answers` instead of hardcoded values;
  its `submit()` / `_click_submit` / `_wait_for_confirmation` removed.
  `applicant/detector.py` flipped: per-ATS handlers default;
  `UniversalApplicant` is the fallback. `USE_LEGACY_APPLICANTS` env
  flag removed.
- **M-4**: `applicant/agent_loop.py` stripped of submit mode and the
  `click_submit` tool. The vision agent is prepare-only; can call
  `finish_preparation` or `queue_for_review` and nothing else terminal.
  `BrowserSession.mode` / `submitted` / `submit_confirmation_text`
  removed. `prompts/agent_submit.md` deleted. `form_answers` injected
  into the agent's system prompt so it doesn't OCR identity fields.
- **M-5**: `process_prefill_requested_jobs()` orchestrator opens a
  visible browser, dispatches uniformly to the per-ATS handler or the
  prepare-only vision agent (via `UniversalApplicant.apply_with_page`),
  uploads the post-fill screenshot to Storage, marks
  `awaiting_human_submit`, sends `notify_awaiting_submit`, then
  **blocks on terminal `input()`** while the human reviews.
- **M-6**: Dashboard cockpit at `/dashboard/review/[job_id]` (in the
  sibling `portfolio` repo). Header / status banner / three-accordion
  materials section / pre-fill screenshot / Match Agent panel / sticky
  action bar (Pre-fill Form, Mark Applied modal, Open Manually, Skip,
  Mark Failed). New API routes: `prefill`, `mark-applied`, `skip`,
  `mark-failed`. The "Mark Applied" click is the single source of
  truth for whether a job got submitted.
- **M-7**: `run_cycle` calls `process_approved_jobs +
  process_prefill_requested_jobs`. Removed `process_confirmed_jobs`,
  `submit_one_visible`, and `--submit-visible` (~170 lines).
- **M-8**: `notify_ready_for_review` body now includes score, tier,
  archetype, legitimacy, and a deep link to the cockpit. New
  `notify_awaiting_submit(job, screenshot_path)` for the awaiting-
  human-submit state. Set `PORTFOLIO_BASE_URL` to override the
  cockpit base URL for staging deploys.

### Cost profile

- **Ashby / Greenhouse / Lever** — `$0` in form-fill tokens. DOM
  handlers read everything from `jobs.form_answers` (which itself was
  written by a single Sonnet call during tailoring).
- **Vision-agent fallback** (Workday / iCIMS / SmartRecruiters /
  aggregators) — roughly `~$3/job` of agent-loop tokens, similar to
  the previous flow but cheaper because identity fields no longer
  require OCR — they come pre-baked in the system prompt.
- **`form_answers` generation itself** — one Sonnet call per
  ready-for-review job, ~2k output tokens. Gated on score >= 6 so
  jobs that won't be applied don't burn the call.

---

## Companion subpackages and repos

- `jobpipe.hunt` — discovery + scoring (writes status=new rows). PR-9
  unified what was previously the sibling `job-hunter` repo into this
  subpackage; the original repo can be archived after PR-9 merges.
- `../portfolio` — Next.js dashboard at vishal.pa.thak.io/dashboard.
  Stays as a separate repo (frontend, deployed on Vercel).

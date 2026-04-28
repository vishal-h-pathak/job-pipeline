# job-applicant

Application-prep + auto-submit pipeline for Vishal Pathak. Reads
approved jobs from Supabase (written by the sibling `job-hunter` repo),
tailors a resume + cover letter, fills the form via Playwright, and
pauses for a human "Confirm Submit" click before clicking submit.

The system never auto-applies on its own — the human is always the
trigger. The agent does the busywork.

---

## Project structure

```
main.py                # Entry point — process_approved_jobs / process_confirmed_jobs
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
  agent_common.md      # Submission agent — common rules
  agent_prepare.md     # Submission agent — prepare-mode tail
  agent_submit.md      # Submission agent — submit-mode tail
tailor/
  resume.py            # Resume tailoring (JSON metadata)
  cover_letter.py      # Cover letter generation (markdown text)
  latex_resume.py      # LaTeX resume generation + pdflatex compile
  cover_letter_pdf.py  # ReportLab cover letter PDF rendering
  archetype.py         # Archetype classifier + config loader (J-4)
  normalize.py         # ATS Unicode normalization (J-5)
applicant/
  agent_loop.py        # Claude tool-use loop driving Playwright
  detector.py          # ATS detection (Ashby, Greenhouse, Lever, ...)
  url_resolver.py      # Aggregator → real ATS endpoint
  ashby.py, universal.py  # Per-ATS form-fillers
  browser_tools.py     # Playwright tool primitives
interview_prep/
  generator.py         # STAR+R story generator (J-3)
  bank.py              # Supabase r/w for star_stories
scripts/
  migration.sql, migration_storage.sql        # Original schema
  003_legitimacy.sql                          # Posting-legitimacy columns (J-2)
  004_archetype.sql                           # Archetype + confidence columns (J-4)
  005_star_stories.sql                        # STAR+R bank table (J-3)
  006_pattern_analyses.sql                    # Pattern-analysis output table (J-6)
  analyze_patterns.py                         # Closed-loop pattern analyzer (J-6)
  cv_sync_check.py                            # CV / digest / BASE_RESUME drift detector (J-9)
templates/
  VOICE_PROFILE.md     # Tone for cover-letter prose (referenced by prompts)
CLAUDE.md              # Narrative profile aggregator (compat fallback)
```

User-layer ground truth lives in the sibling `job-hunter/profile/` repo;
`prompts.load_profile()` resolves it via fallback. See `DATA_CONTRACT.md`.

---

## Pipeline

```
job-hunter writes → status=new
   ↓ (you mark approved in dashboard)
status=approved
   ↓ main.py::process_approved_jobs
[archetype classify] → [resume tailor] → [cover letter]
   → [LaTeX compile + PDF upload] → [STAR+R stories]
   → [resolve ATS URL]
status=ready_to_submit  (PDFs in Storage; reviewer can read everything)
   ↓ (Confirm Submit click in /dashboard)
status=submit_confirmed
   ↓ main.py::process_confirmed_jobs
[Playwright agent re-fills form] → [click submit]
status=applied  (or failed / needs_review)
```

Every LLM call along the way uses the prompts in `prompts/` and reads
the canonical user-layer profile.

---

## Running manually

```bash
pip install -r requirements.txt
playwright install chromium

python main.py --status                    # job counts by status
python main.py --test-tailor <job_id>      # smoke-test materials for one job
python main.py                             # one full cycle (approved + confirmed)
python main.py --submit-visible <job_id>   # watch the browser submit a confirmed job
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
  `../job-hunter/profile/learned-insights.md` (recognized by the user-
  layer loader).

---

## Companion repos

- `../job-hunter` — discovery + scoring (writes status=new rows)
- `../portfolio` — Next.js dashboard at vishal.pa.thak.io/dashboard

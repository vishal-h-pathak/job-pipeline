# Changelog

## 2026-04-28 — Career-ops alignment (DOM-based form-fill, stop-at-submit)

The system never clicks Submit anymore. The dashboard cockpit's "Mark
Applied" click is the single source of truth for whether a job was
actually submitted. Per-ATS DOM handlers for Greenhouse and Lever
land alongside a refactored Ashby; the vision agent's submit-mode and
`click_submit` tool are deleted entirely.

- **M-1**: `tailor/form_answers.py` + `prompts/form_answers.md` generate
  a structured form-answer JSON. Identity / contact / location / comp /
  work-auth fields filled from `profile.yml` in Python (LLM never
  hallucinates these); only `why_this_role`, `why_this_company`,
  `additional_info`, and `additional_questions` are model-generated.
  Persisted to `jobs.form_answers` JSONB (migration 007). Gated on
  score >= 6. `--test-tailor` Step 5 prints the draft regardless of
  score.
- **M-2**: Status flow collapsed. New canonical states:
  `discovered → approved → preparing → ready_for_review → prefilling →
  awaiting_human_submit → applied` plus terminals `failed / skipped /
  expired / ignored`. Legacy `ready_to_submit / submit_confirmed /
  submitting` rows migrated to `ready_for_review`. Stop-at-submit
  columns added: `submission_url`, `prefill_screenshot_path`,
  `prefill_completed_at`, `submitted_at`, `submission_notes`.
  `mark_ready_to_submit` kept as deprecation alias of
  `mark_ready_for_review`. `mark_needs_review` routes to `mark_failed`
  since `needs_review` is no longer in the CHECK enum.
- **M-3**: `applicant/greenhouse.py` and `applicant/lever.py` added
  modeled on `applicant/ashby.py`. All three are pure Playwright +
  DOM selectors — zero Anthropic API calls — and read field_map from
  `jobs.form_answers` (a shared `_build_field_map` helper lives in
  `ashby.py`). `ashby.py` refactored: hardcoded "Vishal Pathak" /
  "vshlpthk1@gmail.com" / etc. removed; `submit() / _click_submit /
  _wait_for_confirmation` deleted. `applicant/detector.py` flipped:
  per-ATS handlers default; `UniversalApplicant` is the fallback.
  `USE_LEGACY_APPLICANTS` env flag removed.
- **M-4**: Vision agent stripped of submit mode. Removed
  `click_submit` from `TOOL_SCHEMAS`, the `_run_tool` branch, and
  `mode` from `BrowserSession`. Deleted `prompts/agent_submit.md`.
  Updated `prompts/agent_common.md` + `agent_prepare.md` to forbid
  submission and inject `form_answers` so the agent doesn't OCR
  identity fields. `UniversalApplicant.submit()` removed → falls
  through to `BaseApplicant.submit()` which raises
  `NotImplementedError`. Default `headless=False` on the visible
  browser path.
- **M-5**: `process_prefill_requested_jobs()` orchestrator added.
  Opens a visible browser, dispatches uniformly to the per-ATS
  handler (`fill_form`) or the vision agent
  (`UniversalApplicant.apply_with_page`), uploads the post-fill
  screenshot to Storage at `{job_id}/prefill.png` via the new
  `storage.upload_prefill_screenshot`, marks `awaiting_human_submit`,
  sends `notify_awaiting_submit`, then **blocks on terminal
  `input()`** so the browser stays open for human review.
- **M-6**: Dashboard cockpit (lives in the sibling `portfolio` repo).
  Replaced `/dashboard/review/[job_id]` UI with header / status
  banner / three-accordion materials section (resume, cover letter,
  form-answer drafts with copy buttons) / pre-fill screenshot /
  Match Agent panel / sticky action bar (Pre-fill Form / Mark
  Applied modal / Open Manually / Skip / Mark Failed). Four new API
  routes: `prefill`, `mark-applied`, `skip`, `mark-failed`. The
  "Mark Applied" click is the single source of truth for `applied`.
- **M-7**: `run_cycle` calls
  `process_approved_jobs + process_prefill_requested_jobs`. Removed
  `process_confirmed_jobs`, `submit_one_visible`, and the
  `--submit-visible` CLI flag (~170 lines net deletion).
- **M-8**: `notify_ready_for_review` body lists score / tier /
  archetype / legitimacy and a cockpit deep link. New
  `notify_awaiting_submit(job, screenshot_path)` writes the
  `[ACTION]`-prefixed notification with the cockpit URL and
  screenshot path. `PORTFOLIO_BASE_URL` env var overrides the
  default `https://vishal.pa.thak.io`.

### Cost profile

- Ashby / Greenhouse / Lever: $0 in form-fill tokens (pure DOM).
- Vision-agent fallback: ~$3/job, OCR removed for identity fields.
- `form_answers` generation: one Sonnet call per `ready_for_review`
  job (~2k output tokens), gated on score >= 6.


## 2026-04-27 — Career-ops integration

Adopted the high-leverage patterns from `santifer/career-ops` while
keeping this repo's database-backed, real-submission architecture.

- **J-2**: Schema migration `003_legitimacy.sql` adds posting-legitimacy
  columns (the scorer that writes them lives in `../job-hunter`). 275
  pre-existing rows backfilled to `proceed_with_caution`.
- **J-3**: STAR+R interview-prep accumulator. New `interview_prep/`
  module generates 3-5 stories per tailored job; `005_star_stories.sql`
  table accumulates them with archetype + tag indices for browsing.
- **J-4**: Archetype routing for tailoring. `tailor/archetype.py`
  classifies each JD into one of five lanes; downstream prompts inject
  the chosen framing/emphasis/tone/bullet-template via a new
  `{archetype_block}` slot. `004_archetype.sql` persists the chosen
  key + confidence on the job row.
- **J-5**: `tailor/normalize.py::normalize_for_ats()` runs on every
  cover-letter + LaTeX bullet before PDF render. Banned-phrases list,
  specificity rule, and Unicode hygiene live in
  `prompts/_shared.md`.
- **J-6**: `scripts/analyze_patterns.py` walks the jobs table, computes
  response/interview/offer rates per group, flags effect-size patterns,
  and writes both a markdown report and a row to
  `pattern_analyses` (migration `006_pattern_analyses.sql`).
- **J-7**: Every inline triple-quoted prompt extracted into
  `prompts/*.md`. `prompts/__init__.py::load_prompt()` joins multiple
  named prompts with `---` separators and prepends `_shared.md`.
- **J-9**: `scripts/cv_sync_check.py` does anchor-based cross-comparison
  of numeric facts across `cv.md`, `article-digest.md`, `BASE_RESUME`,
  and `CLAUDE.md`. `tailor/resume.py` calls `warn_if_drift()` on
  import — surfaces a one-line warning, never blocks tailoring.
- **J-10**: User-layer reads from sibling `../job-hunter/profile/` via
  `prompts.load_profile()` with legacy CLAUDE.md fallback.
- **J-11**: Prompt loader recognizes the new `learned-insights.md` file
  written by the dashboard's Match Agent → "Save to profile" flow.

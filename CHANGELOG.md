# Changelog

> **Convention:** every PR adds an entry under `## [Unreleased]` with the
> right `[hunt]` / `[tailor]` / `[submit]` / `[shared]` / `[profile]` /
> `[pipeline]` / `[tests]` tag(s). Cross-cutting work gets multiple tags
> (e.g. `[tailor][submit]`).

## [Unreleased]

> PR ordering note: the migration landed PR-3 → PR-5 → PR-4 → PR-6 →
> PR-7 → PR-8 → PR-9 → PR-9b → PR-10. PR-4 landed after PR-5 due to a
> mid-migration sequencing error; both PRs were verified to produce no
> behavior conflicts at landing time. See `git log` for actual commit
> dates.

### Added

- [pipeline] Top-level repo skeleton — `pyproject.toml` (minimal
  `jobpipe` package, Python>=3.9, `[dev]=pytest+ruff`), `.gitignore`
  (Python + macOS + graphify-output), top-level `README.md`, empty
  `tests/conftest.py`, smoke tests so pytest exits 0 (not 5: no-tests-
  collected) before real tests arrive, and `.github/workflows/ci.yml`
  for pytest collect + run on push/PR (PR-0).
- [pipeline] Subtree-merged history of `job-hunter` → `jobpipe/hunt/`,
  `job-applicant` → `jobpipe/tailor/`, and `job-submitter` →
  `jobpipe/submit/` so cross-tree history-walkbacks via
  `git log --follow` continue to work in the unified repo (PR-0a).
- [shared] `jobpipe/shared/` as the canonical home for previously
  duplicated utilities — `jobid.py` (`make_job_id`, `canonical_url`,
  god-node with 15 callers), `validator.py` (`validate_url`),
  `storage.py` (unified `download_to_tmp` / `download_bytes` adopting
  applicant's defensive empty-check + try/except cleanup AND
  submitter's optional suffix kwarg + debug log; lazy supabase import).
  Re-export shims left at the old paths until PR-3..PR-5 rewrite
  importers (PR-1).
- [profile] Top-level `profile/` user layer — `profile.yml`,
  `article-digest.md`, `voice-profile.md` (promoted from
  `jobpipe/tailor/templates/VOICE_PROFILE.md`), `learned-insights.md`.
  `application_form_defaults` migrated to the canonical
  `application_defaults` key in `profile.yml`. New
  `jobpipe/profile_loader.py` with `load_profile`,
  `load_application_defaults`, `load_archetypes`, `load_voice_profile`,
  `load_article_digest`, `load_learned_insights`. `JOBPIPE_PROFILE_DIR`
  env var override; resolution walks up to `pyproject.toml`. New
  `tests/test_application_defaults_consistency.py` pins the shape
  against drift on either side (PR-2).
- [hunt] `jobpipe-hunt` console script (`jobpipe.hunt.agent:run`).
  `jobpipe/shared/html.py` with `strip_tags` + `clean_html_to_text`
  consolidating 9 duplicated `_strip_html` definitions across
  ashby/greenhouse/lever/remoteok/workday/indeed/hn_whoshiring/
  eighty_thousand_hours/enricher. `jobpipe/hunt/sources/_http.py` with
  `fetch_json` (GET/POST + 404-warn-skip), `sleep_between_requests`
  (polite-pause), and re-exports of `passes_title_filter` /
  `location_filter_enabled`. `KEEP-DISABLED` headers on
  `sources/indeed.py` and `sources/linkedin.py` documenting why they
  sit on disk but aren't in the `SOURCES` tuple. `--mode {local_remote,
  us_wide}` matches legacy semantics; `--once` documented as a no-op
  for cron / verification scripts (PR-3).
- [submit] `jobpipe-submit` console script
  (`jobpipe.submit.runner:run`). `adapters/deterministic/` grouping
  for `greenhouse` / `lever` / `ashby` (M-3 grouping made explicit on
  disk). `runner.py` thin `run()` wrapper around the existing
  `main_loop()` + signal handlers; legacy
  `python jobpipe/submit/runner.py` continues to work via the
  `__main__` guard. `review/packet.py` flattened to `review_packet.py`
  (one-file dir collapsed) (PR-5).
- [tailor] `jobpipe-tailor` console script
  (`jobpipe.tailor.pipeline:run`). `jobpipe/shared/ats_detect.py`
  rehomed from `applicant/detector.py`. New
  `submit/adapters/prepare_dom/{ashby,lever,greenhouse,universal}.py`
  and `submit/adapters/prepare_loop.py` (rehomed from `applicant/
  agent_loop.py`). `tailor/url_resolver.py` rehomed from
  `applicant/url_resolver.py`. `tailor/archetype.py`'s bespoke
  `_resolve_profile_yml` + `_load_archetypes` (which walked up to
  `../job-hunter/profile/`) replaced with
  `jobpipe.profile_loader.load_archetypes`. PR-1-style re-export
  shims left at every legacy `applicant/*` path so unmigrated
  `tailor/scripts/*` still work; PR-9 finishes the cutover (PR-4).
- [tailor][submit] `jobpipe.config.require_env(name)` cross-subtree
  env-var checker. Tailor-side `mark_failed` renamed to
  `mark_tailor_failed` with new keyword-only params
  (`clear_materials: bool = True`, `screenshot_path`,
  `uncertain_fields`); when `clear_materials=True` it now also calls
  `storage.delete_all_for_job` and nulls `resume_pdf_path` /
  `cover_letter_pdf_path` atomically (was previously two calls).
  Submit-side `mark_failed` and `mark_needs_review` become the single
  canonical owners of those names (PR-6).
- [submit] `prepare_dom/_common.py` shared sync-Playwright helpers —
  `fill_text`, `upload_file`, `paste_textarea`, `load_cover_letter`,
  `build_field_map`, label- and name-attr selector builders, plus
  `note_unfilled_custom_questions` (extracted from the byte-identical
  5-line block duplicated in lever and greenhouse). Helpers are
  duck-typed on `Page` so unit tests run without Playwright installed.
  `BaseApplicant` and `BrowserSession` co-located under
  `submit/adapters/applicant_base.py` alongside the async `Adapter`
  base. Adapters now import via explicit jobpipe-namespaced paths;
  `_bootstrap_tailor_sys_path()` in `shared/ats_detect.py` removed
  (PR-7).
- [pipeline] Canonical `jobpipe/db.py`, `jobpipe/notify.py`,
  `jobpipe/config.py` consolidating per-subtree copies. Supabase
  client unified on a lazy module-level singleton (vs. previous mix of
  per-call factory in hunt and eager singletons in tailor/submit) —
  singleton wins under polling load, lazy because import-time HTTP
  was untestable. `CLAUDE_MODEL` intentionally split into
  `TAILOR_CLAUDE_MODEL` (default `claude-sonnet-4-20250514`) and
  `SUBMITTER_CLAUDE_MODEL` (default `claude-sonnet-4-6`) so resume /
  cover-letter LLM output stays frozen; both read a `CLAUDE_MODEL`
  env fallback for future one-flip unification. Notify functions
  renamed `notify_*` → `send_*` (canonical) with `notify_*` aliases
  retained as once-per-process deprecation warnings; user-visible
  `notification.type` and `jobs.status` strings (`ready_for_review`,
  `awaiting_human_submit`) deliberately decoupled from the symbol
  rename so the dashboard contract is unchanged. New
  `tests/test_unified_modules_consistency.py` (PR-8).
- [pipeline] `jobpipe/tailor/paths.py` — `OUTPUT_DIR` and
  `CANDIDATE_PROFILE_PATH` rehomed from the deleted `tailor/config.py`
  shim; `CANDIDATE_PROFILE_PATH` now points at the consolidated
  repo-root `CLAUDE.md` (PR-9).
- [pipeline] Consolidated top-level `CLAUDE.md` — single identity
  prose, unified pipeline architecture overview, application-form
  defaults appearing exactly once. Replaces the three subpackage
  `CLAUDE.md` files (PR-9).

### Changed

- [pipeline] Three repos consolidated into `job-pipeline/` over
  PR-0..PR-9. After PR-9 lands, the original
  `~/dev/jarvis/{job-hunter,job-applicant,job-submitter}/` repos can
  be archived; the unified repo owns its own venv and console scripts
  going forward.
- [hunt] Profile-loader unification: `scorer.py` calls
  `prompts.build_profile_prompt_string()` directly (renamed from
  `load_profile()` to disambiguate from
  `jobpipe.profile_loader.load_profile()` which returns a dict).
  Behavior delta worth flagging for future bisects: between PR-2 and
  PR-3, `hunt/prompts/load_profile()` was still pointed at
  `jobpipe/hunt/profile/` and silently dropped 3 of 5 user-layer
  files (the scorer saw only `disqualifiers.yml` and `cv.md`). PR-3
  restores the full set
  `{profile.yml, disqualifiers.yml, cv.md, article-digest.md,
  learned-insights.md}` via `jobpipe.profile_loader`. Score
  divergence within the PR-2..PR-3 window is explained by this, not
  by a regression in `scorer.py` (PR-3).
- [submit] `pyproject.toml` dropped `pythonpath = ["."]` from
  `[tool.pytest.ini_options]` once the editable install obviated the
  workaround (PR-5).
- [tailor] `prepare_loop._load_voice_profile` re-anchored its template
  lookup to `jobpipe/tailor/templates/VOICE_PROFILE.md` via a
  repo-root walk after `applicant/agent_loop.py` moved to
  `submit/adapters/prepare_loop.py` (the legacy
  `Path(__file__).parent.parent / "templates"` worked from
  `applicant/` but pointed at `jobpipe/submit/templates/` after the
  move) (PR-4).
- [tailor][submit] Submit-side `mark_failed` and `mark_needs_review`
  documented as the canonical owners of those names; tailor side
  renamed to `mark_tailor_failed` to remove the cross-subtree
  collision. Tailor failures default to clearing materials so the
  cockpit doesn't surface stale partial PDFs; submit failures
  preserve materials so a human can re-attempt or review. Tailor
  `mark_needs_review` deleted (was an M-2 alias whose body wrote
  `status='failed'`); submit `mark_needs_review` (which writes the
  real `'needs_review'` status for ambiguous post-submit pages) is
  the single canonical owner. 9 tailor-side call sites in
  `pipeline.py` and `scripts/submit_one.py` updated (PR-6).
- [tailor] `tailor/tailor/cover_letter_pdf.py`: `reportlab` imports
  moved inside `render_cover_letter_pdf()` per Convention #4 (lazy
  heavy SDKs); `reportlab` declared as a runtime dep so cover-letter
  rendering works in the production path (PR-9, carryover from PR-8).
- [tailor] `jobpipe/tailor/prompts/__init__.py`: profile-dir
  resolution rewritten from sibling-repo hop to a multi-dir scan over
  `<root>/profile/` + `<root>/jobpipe/hunt/profile/`; legacy
  `CLAUDE.md` fallback now points at the consolidated repo-root copy
  (PR-9).
- [tests] Auto-discovered test count grew from ~47 (PR-7) to 152
  (PR-8) via the new schema-consistency test plus submit-subtree
  tests being picked up at the root pytest level. After PR-9's shim
  removal + 5 obsolete shim-consistency-test deletions the count
  settles at 147 passed / 1 skipped. Future test-count changes
  should be measured against this 147 baseline.

### Removed

- [pipeline] All migration scaffolding (PR-9): 19 PR-1/4/7/8 shim
  files (`jobpipe/hunt/utils/{__init__,jobid,validator}.py`;
  `jobpipe/{hunt,tailor,submit}/db.py`; `jobpipe/hunt/notifier.py`;
  `jobpipe/tailor/notify.py`; `jobpipe/{hunt,tailor}/config.py`;
  `jobpipe/tailor/applicant/{__init__,agent_loop,ashby,base,
  browser_tools,detector,greenhouse,lever,universal,url_resolver}.py`),
  3 subpackage `CLAUDE.md` files (consolidated into the top-level
  one), `hunt/seen_jobs.json` (PR-3 moved dedup to Supabase),
  `hunt/run_agent.sh` (hardcoded sibling-repo path, calls renamed
  file), and 3 per-subpackage `requirements.txt` files (consolidated
  into `pyproject.toml::[project].dependencies`).
- [tailor] Vision-agent submit mode: `click_submit` from
  `TOOL_SCHEMAS`, the `_run_tool` submit branch, `mode` from
  `BrowserSession`, `prompts/agent_submit.md`,
  `UniversalApplicant.submit()`. Falls through to
  `BaseApplicant.submit()` which raises `NotImplementedError`
  (M-4 carried forward; finalized in PR-9 by deleting the last
  `applicant/` shims).
- [tailor] Auto-submit polling path: `process_confirmed_jobs`
  (~85 lines), `submit_one_visible` (~85 lines), and the
  `--submit-visible` CLI flag — net ~170 lines of legacy
  auto-submit orchestration removed; the new prefill orchestrator
  already lives in `pipeline.py` from M-5 (M-7 carried forward).
- [pipeline] `jobpipe/submit/config.py::_require` replaced with
  `from jobpipe.config import require_env` (PR-6).

## Pre-merge history (preserved verbatim from source repos)

> Each entry below is tagged at consolidation time based on which
> source repo it lived in: `job-hunter` → `[hunt]`,
> `job-applicant` → `[tailor]`. The `job-submitter` repo never
> maintained a CHANGELOG; its Milestone-1..Milestone-6 history
> (deterministic Greenhouse/Lever/Ashby adapters, generic Stagehand
> Agent fallback, three-tier custom-question classifier) lives only
> in `git log`.

### 2026-04-28 — Career-ops alignment (DOM-based form-fill, stop-at-submit)

The system never clicks Submit anymore. The dashboard cockpit's "Mark
Applied" click is the single source of truth for whether a job was
actually submitted. Per-ATS DOM handlers for Greenhouse and Lever
land alongside a refactored Ashby; the vision agent's submit-mode and
`click_submit` tool are deleted entirely.

- **[tailor] M-1**: `tailor/form_answers.py` + `prompts/form_answers.md`
  generate a structured form-answer JSON. Identity / contact /
  location / comp / work-auth fields filled from `profile.yml` in
  Python (LLM never hallucinates these); only `why_this_role`,
  `why_this_company`, `additional_info`, and `additional_questions`
  are model-generated. Persisted to `jobs.form_answers` JSONB
  (migration 007). Gated on score >= 6. `--test-tailor` Step 5 prints
  the draft regardless of score.
- **[tailor] M-2**: Status flow collapsed. New canonical states:
  `discovered → approved → preparing → ready_for_review → prefilling
  → awaiting_human_submit → applied` plus terminals
  `failed / skipped / expired / ignored`. Legacy
  `ready_to_submit / submit_confirmed / submitting` rows migrated to
  `ready_for_review`. Stop-at-submit columns added: `submission_url`,
  `prefill_screenshot_path`, `prefill_completed_at`, `submitted_at`,
  `submission_notes`. `mark_ready_to_submit` kept as deprecation
  alias of `mark_ready_for_review`. `mark_needs_review` routes to
  `mark_failed` since `needs_review` is no longer in the CHECK enum.
- **[tailor] M-3**: `applicant/greenhouse.py` and `applicant/lever.py`
  added modeled on `applicant/ashby.py`. All three are pure
  Playwright + DOM selectors — zero Anthropic API calls — and read
  field_map from `jobs.form_answers` (a shared `_build_field_map`
  helper lives in `ashby.py`). `ashby.py` refactored: hardcoded
  "Vishal Pathak" / "vshlpthk1@gmail.com" / etc. removed;
  `submit() / _click_submit / _wait_for_confirmation` deleted.
  `applicant/detector.py` flipped: per-ATS handlers default;
  `UniversalApplicant` is the fallback. `USE_LEGACY_APPLICANTS` env
  flag removed.
- **[tailor] M-4**: Vision agent stripped of submit mode. Removed
  `click_submit` from `TOOL_SCHEMAS`, the `_run_tool` branch, and
  `mode` from `BrowserSession`. Deleted `prompts/agent_submit.md`.
  Updated `prompts/agent_common.md` + `agent_prepare.md` to forbid
  submission and inject `form_answers` so the agent doesn't OCR
  identity fields. `UniversalApplicant.submit()` removed → falls
  through to `BaseApplicant.submit()` which raises
  `NotImplementedError`. Default `headless=False` on the visible
  browser path.
- **[tailor] M-5**: `process_prefill_requested_jobs()` orchestrator
  added. Opens a visible browser, dispatches uniformly to the
  per-ATS handler (`fill_form`) or the vision agent
  (`UniversalApplicant.apply_with_page`), uploads the post-fill
  screenshot to Storage at `{job_id}/prefill.png` via the new
  `storage.upload_prefill_screenshot`, marks `awaiting_human_submit`,
  sends `notify_awaiting_submit`, then **blocks on terminal
  `input()`** so the browser stays open for human review.
- **[tailor] M-6**: Dashboard cockpit (lives in the sibling
  `portfolio` repo). Replaced `/dashboard/review/[job_id]` UI with
  header / status banner / three-accordion materials section
  (resume, cover letter, form-answer drafts with copy buttons) /
  pre-fill screenshot / Match Agent panel / sticky action bar
  (Pre-fill Form / Mark Applied modal / Open Manually / Skip / Mark
  Failed). Four new API routes: `prefill`, `mark-applied`, `skip`,
  `mark-failed`. The "Mark Applied" click is the single source of
  truth for `applied`.
- **[tailor] M-7**: `run_cycle` calls
  `process_approved_jobs + process_prefill_requested_jobs`. Removed
  `process_confirmed_jobs`, `submit_one_visible`, and the
  `--submit-visible` CLI flag (~170 lines net deletion).
- **[tailor] M-8**: `notify_ready_for_review` body lists score / tier
  / archetype / legitimacy and a cockpit deep link. New
  `notify_awaiting_submit(job, screenshot_path)` writes the
  `[ACTION]`-prefixed notification with the cockpit URL and
  screenshot path. `PORTFOLIO_BASE_URL` env var overrides the default
  `https://vishal.pa.thak.io`.

#### Cost profile

- Ashby / Greenhouse / Lever: $0 in form-fill tokens (pure DOM).
- Vision-agent fallback: ~$3/job, OCR removed for identity fields.
- `form_answers` generation: one Sonnet call per `ready_for_review`
  job (~2k output tokens), gated on score >= 6.

### 2026-04-27 — Career-ops integration (tailor side)

Adopted the high-leverage patterns from `santifer/career-ops` while
keeping this repo's database-backed, real-submission architecture.

- **[tailor] J-2**: Schema migration `003_legitimacy.sql` adds
  posting-legitimacy columns (the scorer that writes them lives in
  `../job-hunter`). 275 pre-existing rows backfilled to
  `proceed_with_caution`.
- **[tailor] J-3**: STAR+R interview-prep accumulator. New
  `interview_prep/` module generates 3-5 stories per tailored job;
  `005_star_stories.sql` table accumulates them with archetype + tag
  indices for browsing.
- **[tailor] J-4**: Archetype routing for tailoring.
  `tailor/archetype.py` classifies each JD into one of five lanes;
  downstream prompts inject the chosen
  framing/emphasis/tone/bullet-template via a new
  `{archetype_block}` slot. `004_archetype.sql` persists the chosen
  key + confidence on the job row.
- **[tailor] J-5**: `tailor/normalize.py::normalize_for_ats()` runs
  on every cover-letter + LaTeX bullet before PDF render.
  Banned-phrases list, specificity rule, and Unicode hygiene live in
  `prompts/_shared.md`.
- **[tailor] J-6**: `scripts/analyze_patterns.py` walks the jobs
  table, computes response/interview/offer rates per group, flags
  effect-size patterns, and writes both a markdown report and a row
  to `pattern_analyses` (migration `006_pattern_analyses.sql`).
- **[tailor] J-7**: Every inline triple-quoted prompt extracted into
  `prompts/*.md`. `prompts/__init__.py::load_prompt()` joins multiple
  named prompts with `---` separators and prepends `_shared.md`.
- **[tailor] J-9**: `scripts/cv_sync_check.py` does anchor-based
  cross-comparison of numeric facts across `cv.md`,
  `article-digest.md`, `BASE_RESUME`, and `CLAUDE.md`.
  `tailor/resume.py` calls `warn_if_drift()` on import — surfaces a
  one-line warning, never blocks tailoring.
- **[tailor] J-10**: User-layer reads from sibling
  `../job-hunter/profile/` via `prompts.load_profile()` with legacy
  CLAUDE.md fallback.
- **[tailor] J-11**: Prompt loader recognizes the new
  `learned-insights.md` file written by the dashboard's Match Agent
  → "Save to profile" flow.

### 2026-04-27 — Career-ops integration (hunt side)

Adopted the high-leverage patterns from `santifer/career-ops` while
keeping this repo's database-backed architecture.

- **[hunt] J-1**: Zero-token ATS API scanners. Greenhouse, Lever,
  Ashby, and a new Workday module read company lists from
  `profile/portals.yml` and apply a cheap title pre-filter before
  the LLM scorer touches a posting.
- **[hunt] J-2**: Posting Legitimacy axis (`high_confidence` /
  `proceed_with_caution` / `suspicious`) added to the scorer
  alongside fit. Stored separately (`jobs.legitimacy`,
  `jobs.legitimacy_reasoning`); never affects the fit score.
- **[hunt] J-4**: Archetype definitions added to
  `profile/profile.yml` — five lanes covering Tier 1A/1B/1C, Tier 2,
  and Tier 3.
- **[hunt] J-7**: Inline scorer prompt extracted into
  `prompts/scorer.md`; `prompts/_shared.md` carries global rules
  (anti-slop, ethics, specificity, Unicode hygiene).
- **[hunt] J-8**: New `scripts/check_liveness.py` transitions
  stale-and-dead postings to `expired`. Polite, jittered, runs
  nightly via cron.
- **[hunt] J-10**: User-layer ground truth carved out of `CLAUDE.md`
  into structured files in `profile/` (`profile.yml`,
  `disqualifiers.yml`, `cv.md`, `article-digest.md`).
  `DATA_CONTRACT.md` documents the boundary.
- **[hunt] J-11**: User-layer loader recognizes
  `profile/learned-insights.md` so the dashboard's Match Agent →
  "Save to profile" writeback flows into future tailoring runs
  automatically.

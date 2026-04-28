# Changelog

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

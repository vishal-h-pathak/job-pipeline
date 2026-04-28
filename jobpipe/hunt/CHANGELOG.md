# Changelog

## 2026-04-27 — Career-ops integration

Adopted the high-leverage patterns from `santifer/career-ops` while
keeping this repo's database-backed architecture.

- **J-1**: Zero-token ATS API scanners. Greenhouse, Lever, Ashby, and a
  new Workday module read company lists from `profile/portals.yml` and
  apply a cheap title pre-filter before the LLM scorer touches a posting.
- **J-2**: Posting Legitimacy axis (high_confidence / proceed_with_caution
  / suspicious) added to the scorer alongside fit. Stored separately
  (`jobs.legitimacy`, `jobs.legitimacy_reasoning`); never affects the
  fit score.
- **J-4**: Archetype definitions added to `profile/profile.yml` —
  five lanes covering Tier 1A/1B/1C, Tier 2, and Tier 3.
- **J-7**: Inline scorer prompt extracted into `prompts/scorer.md`;
  `prompts/_shared.md` carries global rules (anti-slop, ethics,
  specificity, Unicode hygiene).
- **J-8**: New `scripts/check_liveness.py` transitions stale-and-dead
  postings to `expired`. Polite, jittered, runs nightly via cron.
- **J-10**: User-layer ground truth carved out of `CLAUDE.md` into
  structured files in `profile/` (profile.yml, disqualifiers.yml,
  cv.md, article-digest.md). `DATA_CONTRACT.md` documents the boundary.
- **J-11**: User-layer loader recognizes `profile/learned-insights.md`
  so the dashboard's Match Agent → "Save to profile" writeback flows
  into future tailoring runs automatically.

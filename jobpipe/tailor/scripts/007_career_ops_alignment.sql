-- 007_career_ops_alignment.sql — Career-ops alignment (M-1..M-3).
--
-- This migration unfolds across three commit phases:
--   M-1  form_answers JSONB column (this section)
--   M-2  status-flow simplification + stop-at-submit support columns
--   M-3  per-ATS DOM-handler support (no schema change beyond M-2)
--
-- Run in Supabase Dashboard > SQL Editor or via the MCP
-- `apply_migration` tool. Subsequent phase sections will be appended
-- below as they land.
--
-- ── M-1: form-answer drafts (career-ops "Block H") ───────────────────────
-- The tailor pipeline produces a structured JSON of standard
-- application-form fields (identity from profile.yml + four LLM-drafted
-- narrative fields). Persisted here so the per-ATS DOM handlers (M-3)
-- and the dashboard cockpit (M-6) can read the same source of truth.
--
-- Nullable on purpose: only generated for jobs with score >= 6, and
-- generation failures are non-fatal so the row may legitimately have
-- no form_answers even after tailoring.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS form_answers JSONB;


-- ── M-2: simplified status flow ──────────────────────────────────────────
-- New canonical lifecycle:
--   discovered (alias: new) → approved → preparing → ready_for_review →
--   prefilling → awaiting_human_submit → applied
-- Plus terminals: failed, skipped, expired, ignored.
--
-- The old ready_to_submit / submit_confirmed / submitting trio collapses
-- to a single ready_for_review state. All in-flight rows in those three
-- statuses migrate to ready_for_review so they remain reviewable in the
-- dashboard cockpit (M-6) without losing tailored materials.

UPDATE jobs SET status = 'ready_for_review'
  WHERE status IN ('ready_to_submit', 'submit_confirmed', 'submitting');

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_status_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_status_check
  CHECK (status IN (
    'discovered', 'new', 'approved', 'preparing',
    'ready_for_review', 'prefilling', 'awaiting_human_submit',
    'applied', 'failed', 'skipped', 'expired', 'ignored'
  ));


-- ── M-3: stop-at-submit support columns ──────────────────────────────────
-- The per-ATS DOM handlers (Greenhouse, Lever, Ashby) and the prepare-
-- only vision agent fill the form, take a screenshot, mark the row
-- awaiting_human_submit, and block in the terminal until the human has
-- reviewed in the visible browser. The columns below carry the artifacts
-- and timestamps that flow surfaces.
--
--   submission_url            real ATS apply URL post-resolution; the
--                             cockpit's "Open Application Manually"
--                             button links here.
--   prefill_screenshot_path   Supabase Storage object key for the post-
--                             prefill screenshot rendered in the cockpit.
--   prefill_completed_at      when the per-ATS handler finished filling.
--   submitted_at              when the HUMAN clicked "Mark Applied" in
--                             the cockpit. The system never sets this
--                             on its own — clicking a Submit button is
--                             not the source of truth; this field is.
--   submission_notes          free-text notes the human added on Mark
--                             Applied (e.g. "had to fix the salary
--                             field manually, ATS rejected my number").

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS submission_url TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS prefill_screenshot_path TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS prefill_completed_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS submission_notes TEXT;


-- ── Verify ───────────────────────────────────────────────────────────────
-- Sanity counts: form_answers populated, status enum populated, new
-- columns present.

SELECT
  COUNT(*) FILTER (WHERE form_answers IS NULL) AS no_form_answers,
  COUNT(*) FILTER (WHERE form_answers IS NOT NULL) AS with_form_answers
FROM jobs;

SELECT status, COUNT(*) AS n FROM jobs GROUP BY status ORDER BY n DESC;

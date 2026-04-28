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

-- Verify
SELECT
  COUNT(*) FILTER (WHERE form_answers IS NULL) AS no_form_answers,
  COUNT(*) FILTER (WHERE form_answers IS NOT NULL) AS with_form_answers
FROM jobs;

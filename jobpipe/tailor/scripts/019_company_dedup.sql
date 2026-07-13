-- 019_company_dedup.sql — 30-day company dedup window (P2, feat/callback-feedback-loop)
--
-- Consolidation of "The AI Hiring Machine" doc analysis (see
-- reviews/REVIEW_2026-07-13_hiring_machine_consolidation.md §2, item 3).
-- Dedup today is by job id only (jobpipe.shared.jobid hashes URL/company/
-- title), so a reposting under a new URL — or simply a second role at a
-- company we're already deep into — re-surfaces as a clean new lead. At
-- hunt upsert (jobpipe/hunt/agent.py), a new posting is checked against
-- company-level recent history:
--
--   duplicate_recent_company — true when a row for the same
--       (normalized) company has status IN ('applied',
--       'awaiting_human_submit') and was updated within the last 30
--       days. Soft flag only — a different role at the same company can
--       still be worth it, so the row still surfaces, just demoted/
--       flagged in the cockpit rather than hard-dropped.
--   reposting_of_job_id — set instead of (not in addition to) the flag
--       above when the new posting is the SAME role (same normalized
--       title + company) as an existing row within the window; links
--       to that prior row's id so the cockpit can render "reposting of
--       <link>". jobs.id is a text hash (jobpipe.shared.jobid), not a
--       uuid, so this FK is TEXT.
--
-- Apply via the MCP `apply_migration` tool or Supabase Dashboard > SQL
-- Editor. Idempotent — additive; NULL/false defaults so pre-existing
-- rows are unaffected until the next hunt run touches related rows.

ALTER TABLE public.jobs
  ADD COLUMN IF NOT EXISTS duplicate_recent_company BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS reposting_of_job_id TEXT
    REFERENCES public.jobs (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_company_status_updated
  ON public.jobs (company, status, status_updated_at);

-- Verify
SELECT column_name, data_type, column_default, is_nullable
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = 'jobs'
    AND column_name IN ('duplicate_recent_company', 'reposting_of_job_id');

SELECT indexname FROM pg_indexes
  WHERE schemaname = 'public' AND tablename = 'jobs'
    AND indexname = 'idx_jobs_company_status_updated';

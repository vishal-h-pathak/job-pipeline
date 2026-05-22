-- 009_runs_tailor_manual.sql — manual job-URL tailor (PR-tailor-manual-url).
--
-- Adds the runs.kind enum value 'tailor_manual' (used by the dashboard's
-- new /api/dashboard/runs/tailor-manual route, step ④) and a runs.result
-- jsonb column so jobpipe-tailor-one can write back the scraped job_id +
-- final status + dashboard URL for the form to poll.
--
-- The CLI's result-write payload (see jobpipe/tailor/manual/cli.py):
--
--     {
--       "job_id":        "<sha1[:16]>",
--       "status":        "discovered" | "ready_for_review" | <other lifecycle status>,
--       "confidence":    "high" | "low",
--       "title":         "<scraped title>",
--       "company":       "<scraped company or null>",
--       "review_url":    "/dashboard/review/<job_id>"   -- low-confidence path
--       "materials_url": "/dashboard/review/<job_id>"   -- high-confidence path
--     }
--
-- Apply in Supabase Dashboard > SQL Editor or via the MCP
-- `apply_migration` tool. Idempotent — re-runs are safe.
--
-- Both ALTERs are additive (no data loss, no row rewrites):
--   - extending an IN (...) CHECK is a definition swap; rows already
--     valid stay valid.
--   - adding a nullable column with no default rewrites no tuples.

BEGIN;

-- ── 1. Extend runs.kind to admit 'tailor_manual' ───────────────────────
ALTER TABLE public.runs DROP CONSTRAINT IF EXISTS runs_kind_check;
ALTER TABLE public.runs
  ADD CONSTRAINT runs_kind_check
  CHECK (kind IN ('hunt', 'tailor', 'tailor_manual'));

-- ── 2. Add runs.result for the dashboard form to poll ──────────────────
ALTER TABLE public.runs
  ADD COLUMN IF NOT EXISTS result jsonb;

COMMIT;

-- ── Verify ─────────────────────────────────────────────────────────────
-- The constraint definition should now list all three kinds; the result
-- column should appear in the column listing.

SELECT conname, pg_get_constraintdef(oid) AS def
  FROM pg_constraint
  WHERE conrelid = 'public.runs'::regclass
    AND conname = 'runs_kind_check';

SELECT column_name, data_type, is_nullable
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = 'runs'
    AND column_name = 'result';

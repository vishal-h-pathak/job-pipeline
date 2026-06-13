-- 012_response_outcomes.sql — Application outcomes (Session I, feat/tailor-thesis)
--
-- The canonical jobs.status enum (migration 011) deliberately ends the
-- pipeline lifecycle at 'applied' — what the EMPLOYER did next never
-- belonged in the pipeline state machine. This migration gives outcomes
-- their own axis so the portfolio's "log response" feature (built
-- separately) has a column to write and the closed-loop pattern
-- analyzer (analyze_patterns.py) can compute real response / interview
-- / offer rates instead of reading retired status values.
--
--   response_status: none → rejected | screen | interview | offer
--   responded_at:    when the response landed (stamped by the dashboard)
--
-- Apply via the MCP `apply_migration` tool or Supabase Dashboard > SQL
-- Editor. Idempotent — both ALTERs are additive; the default backfills
-- existing rows to 'none' without a long lock at this table size.

ALTER TABLE public.jobs
  ADD COLUMN IF NOT EXISTS response_status TEXT NOT NULL DEFAULT 'none'
    CHECK (response_status IN ('none', 'rejected', 'screen', 'interview', 'offer')),
  ADD COLUMN IF NOT EXISTS responded_at TIMESTAMPTZ;

-- Verify
SELECT column_name, data_type, column_default, is_nullable
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = 'jobs'
    AND column_name IN ('response_status', 'responded_at');

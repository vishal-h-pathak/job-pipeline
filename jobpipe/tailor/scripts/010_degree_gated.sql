-- 010_degree_gated.sql — Degree gate + rescore stamp (Session G, feat/hunt-thesis)
--
-- The hunting thesis (profile/thesis.md) adds a degree-gate rule: when a
-- JD hard-requires an MS/PhD with no "or equivalent experience" escape
-- hatch, the scorer sets degree_gated=true so the dashboard can surface
-- the gate up front instead of letting the role read as a top pick.
--
-- rescored_at is stamped by `jobpipe-hunt --rescore` (re-scores existing
-- rows against the current thesis) so a re-scored row is distinguishable
-- from a freshly-hunted one.
--
-- Apply in Supabase Dashboard > SQL Editor or via the MCP
-- `apply_migration` tool. Idempotent — re-runs are safe. Both ALTERs are
-- additive; the boolean default backfills existing rows to false without
-- a long lock at this table size (~600 rows).

ALTER TABLE public.jobs
  ADD COLUMN IF NOT EXISTS degree_gated BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS rescored_at TIMESTAMPTZ;

-- Verify
SELECT column_name, data_type, column_default, is_nullable
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = 'jobs'
    AND column_name IN ('degree_gated', 'rescored_at');

-- 003_legitimacy.sql — Posting Legitimacy dimension (J-2)
--
-- Adds a separate legitimacy axis to the jobs table. The scorer
-- (job-hunter/scorer.py) emits high_confidence / proceed_with_caution /
-- suspicious in addition to the fit score. These are STORED SEPARATELY
-- so legitimacy doesn't leak into the existing score/tier columns —
-- downstream callers can render or filter on legitimacy without
-- corrupting fit-based queries.
--
-- Run in Supabase Dashboard > SQL Editor.

-- ── 1. New columns ────────────────────────────────────────────────────────
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS legitimacy TEXT,
  ADD COLUMN IF NOT EXISTS legitimacy_reasoning TEXT;

-- Constrain legitimacy to the three known categories. Use a CHECK rather
-- than an enum so adding a new category later is just a CHECK update.
DO $$
BEGIN
  ALTER TABLE jobs
    ADD CONSTRAINT jobs_legitimacy_check
    CHECK (legitimacy IS NULL
           OR legitimacy IN ('high_confidence', 'proceed_with_caution', 'suspicious'));
EXCEPTION WHEN duplicate_object THEN
  NULL;  -- constraint already exists
END $$;

-- ── 2. Backfill existing rows ─────────────────────────────────────────────
-- Existing rows predate the legitimacy axis. Mark them proceed_with_caution
-- so the dashboard renders them with the neutral pill rather than a green
-- "high_confidence" claim we never made.
UPDATE jobs
SET legitimacy = 'proceed_with_caution',
    legitimacy_reasoning = 'Backfilled — predates legitimacy scoring (J-2).'
WHERE legitimacy IS NULL;

-- ── 3. Index for filtering ────────────────────────────────────────────────
-- Cheap; lets the dashboard filter "show me suspicious-only" without a
-- full table scan once volume grows.
CREATE INDEX IF NOT EXISTS idx_jobs_legitimacy ON jobs (legitimacy);

-- ── 4. Verify ─────────────────────────────────────────────────────────────
SELECT legitimacy, COUNT(*)
FROM jobs
GROUP BY legitimacy
ORDER BY 2 DESC;

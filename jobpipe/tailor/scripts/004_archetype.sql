-- 004_archetype.sql — Archetype routing column (J-4)
--
-- The tailor classifies each JD into one of the archetypes defined in
-- profile.yml (tier_1a_compneuro, tier_1b_neuromorphic, tier_1c_bci,
-- tier_2_ai_se, tier_3_mission_ml, ...). The chosen key is stored here
-- for analytics — pattern_analysis (J-6) groups by archetype to surface
-- response-rate differences across lanes.
--
-- Run in Supabase Dashboard > SQL Editor.

ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS archetype TEXT,
  ADD COLUMN IF NOT EXISTS archetype_confidence REAL;

-- Index so /dashboard/insights group-bys are cheap.
CREATE INDEX IF NOT EXISTS idx_jobs_archetype ON jobs (archetype);

-- Verify
SELECT archetype, COUNT(*) FROM jobs GROUP BY archetype ORDER BY 2 DESC;

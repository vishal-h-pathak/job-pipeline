-- 006_pattern_analyses.sql — Pattern-analysis output table (J-6)
--
-- `analyze_patterns.py` runs over the jobs table, groups by the
-- (archetype, status, company_size, comp_band, ats) tuple, computes
-- response/interview/offer rates, and writes the full analysis here as
-- a JSONB blob plus a human-readable markdown summary. The /dashboard
-- /insights page reads the most recent row and renders bar charts.

CREATE TABLE IF NOT EXISTS pattern_analyses (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Total rows that informed the analysis. Stored separately so the
  -- dashboard can show it without parsing payload.
  num_jobs_analyzed INT NOT NULL,
  -- Comma-separated list of group-by dimensions, in order. e.g.
  -- "archetype,ats,company_size".
  dimensions TEXT NOT NULL,
  -- Full analysis: groups + counts + rates + flagged patterns.
  payload JSONB NOT NULL,
  -- Markdown report identical to what's written under reports/.
  summary_md TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pattern_analyses_created
  ON pattern_analyses (created_at DESC);

-- Verify
SELECT id, created_at, num_jobs_analyzed, dimensions
FROM pattern_analyses
ORDER BY created_at DESC
LIMIT 3;

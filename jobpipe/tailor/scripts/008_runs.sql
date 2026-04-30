-- 008_runs.sql — Dashboard-triggered pipeline runs (Phase 3).
--
-- Records every "Run hunt" / "Run tailor" the dashboard dispatches via
-- GitHub Actions workflow_dispatch. The portfolio dashboard's RunsPanel
-- inserts a row here at click time (status='pending'), passes the row id
-- as the workflow's `run_id` input, and the GHA job (via
-- scripts/mark_run.py) updates status → running → completed/failed and
-- attaches the GitHub Actions run URL + a tail-of-log excerpt so the
-- panel can link out and surface failures inline.
--
-- The "Run submit" button is intentionally NOT in scope here: visible-
-- browser pre-fill needs a human at the keyboard, so the existing per-
-- row "Pre-fill" cockpit at /dashboard/review/[job_id] remains the only
-- entry to the submit phase.
--
-- Run in Supabase Dashboard > SQL Editor or via the MCP
-- `apply_migration` tool.
--
-- ── runs table ───────────────────────────────────────────────────────────
-- One row per dashboard-triggered run. RLS is enabled with no policies:
-- the dashboard's API routes use the service-role key, so they bypass
-- RLS, and no anon/authenticated client should ever read this directly.

CREATE TABLE IF NOT EXISTS public.runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind            TEXT NOT NULL CHECK (kind IN ('hunt', 'tailor')),
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'running',
                                    'completed', 'failed')),
  triggered_by    TEXT NOT NULL DEFAULT 'dashboard',
  args            JSONB,
  started_at      TIMESTAMPTZ,
  ended_at        TIMESTAMPTZ,
  log_excerpt     TEXT,
  failure_reason  TEXT,
  github_run_url  TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS runs_kind_status_idx
  ON public.runs (kind, status);
CREATE INDEX IF NOT EXISTS runs_created_at_idx
  ON public.runs (created_at DESC);

ALTER TABLE public.runs ENABLE ROW LEVEL SECURITY;


-- ── Verify ───────────────────────────────────────────────────────────────
-- Sanity counts: table empty on first run, indexes present, RLS on.

SELECT COUNT(*) AS runs_count FROM public.runs;

SELECT indexname FROM pg_indexes
  WHERE schemaname = 'public' AND tablename = 'runs'
  ORDER BY indexname;

SELECT relname, relrowsecurity
  FROM pg_class
  WHERE relname = 'runs';

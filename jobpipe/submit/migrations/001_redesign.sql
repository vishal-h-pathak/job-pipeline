-- Migration 001: Submission Pipeline Redesign — Schema additions
-- Run this in Supabase Dashboard > SQL Editor (or via CLI).
--
-- Purpose: Add columns + audit table required by the new job-submitter agent.
-- Does NOT rename existing status values — those are handled in a later
-- migration once the tailor agent transitions to the new state vocabulary.
-- Fully additive and idempotent (IF NOT EXISTS everywhere).
--
-- Context: JOB_APPLICATION_REDESIGN.md §4 (data model changes).

-- ── 1. New columns on the `jobs` table ────────────────────────────────────

-- Classification of the target ATS for this job. Set once during tailoring
-- (after url_resolver + detector) and consumed by the submitter's router.
-- Valid values (enforced at application layer, not DB): 'greenhouse',
-- 'lever', 'ashby', 'workday', 'icims', 'smartrecruiters', 'linkedin',
-- 'indeed', 'generic'. Null for rows that pre-date this column.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ats_kind TEXT;

-- sha256 over (resume_pdf bytes + cover_letter text). Lets the submitter
-- refuse to submit if materials have drifted since approval, and lets the
-- tailor skip work when inputs haven't changed. Null until first tailor.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS materials_hash TEXT;

-- Structured event log from the submitter's last attempt for this job.
-- Schema (application-enforced):
--   {
--     "attempt_n": int,
--     "adapter": "greenhouse" | "lever" | ...,
--     "filled_fields": [{"label": str, "value": str, "confidence": float}],
--     "skipped_fields": [{"label": str, "reason": str}],
--     "screenshots": [{"label": str, "storage_path": str}],
--     "confirmation_evidence": {"kind": str, "detail": str},
--     "stagehand_session_id": str,
--     "browserbase_replay_url": str,
--     "agent_reasoning": str (optional — only if generic_stagehand adapter),
--     "error": str (optional — only on failure)
--   }
-- Each attempt overwrites this column; historical attempts live in
-- application_attempts (below).
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS submission_log JSONB;

-- Submitter's self-assessed confidence at the "ready to click submit"
-- decision point (0.0–1.0). Drives auto-submit vs needs_review routing.
-- Null until first submit attempt.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS confidence REAL;

-- Soft range check (application should already enforce).
ALTER TABLE jobs
  DROP CONSTRAINT IF EXISTS jobs_confidence_range;
ALTER TABLE jobs
  ADD CONSTRAINT jobs_confidence_range
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0));

-- ── 2. application_attempts audit table ──────────────────────────────────

-- One row per submit attempt. Lets us retry failed submits without losing
-- prior-attempt context, and gives forensic reference links back to the
-- Browserbase session replay.
CREATE TABLE IF NOT EXISTS application_attempts (
  id                       BIGSERIAL PRIMARY KEY,
  job_id                   TEXT NOT NULL,
  attempt_n                INTEGER NOT NULL,
  started_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at                 TIMESTAMPTZ,
  outcome                  TEXT,  -- 'submitted' | 'needs_review' | 'failed' | 'in_progress'
  adapter                  TEXT,  -- 'greenhouse' | 'lever' | 'ashby' | 'generic_stagehand' | ...
  stagehand_session_id     TEXT,
  browserbase_replay_url   TEXT,
  confidence               REAL,
  notes                    JSONB,
  CONSTRAINT attempt_n_positive CHECK (attempt_n > 0),
  CONSTRAINT attempt_confidence_range
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
  CONSTRAINT attempt_outcome_valid
    CHECK (outcome IS NULL OR outcome IN ('submitted','needs_review','failed','in_progress')),
  UNIQUE (job_id, attempt_n)
);

-- FK deferred: jobs.id is TEXT and we can't reliably CREATE FK without
-- first verifying the jobs PK. Application-enforced integrity is fine for
-- a single-tenant hobby setup. Add FK later if/when we harden.

CREATE INDEX IF NOT EXISTS idx_attempts_job_id    ON application_attempts (job_id);
CREATE INDEX IF NOT EXISTS idx_attempts_outcome   ON application_attempts (outcome);
CREATE INDEX IF NOT EXISTS idx_attempts_started   ON application_attempts (started_at DESC);

-- ── 3. Verification ──────────────────────────────────────────────────────

-- Confirm new columns exist on jobs.
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'jobs'
  AND column_name IN ('ats_kind','materials_hash','submission_log','confidence')
ORDER BY column_name;

-- Confirm audit table exists.
SELECT
  tablename,
  (SELECT count(*) FROM application_attempts) AS row_count
FROM pg_tables
WHERE tablename = 'application_attempts';

-- Confirm indexes.
SELECT indexname
FROM pg_indexes
WHERE tablename = 'application_attempts'
ORDER BY indexname;

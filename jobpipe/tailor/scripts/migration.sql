
-- Job Applicant Pipeline: Schema Migration
-- Run this in Supabase Dashboard > SQL Editor

-- Add status workflow column
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'discovered';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMPTZ;

-- Application tracking columns
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS resume_path TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cover_letter_path TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS application_url TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS application_notes TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS failure_reason TEXT;

-- Backfill: mark disqualified jobs as rejected, everything else as discovered
UPDATE jobs SET status = 'rejected' WHERE action = 'disqualify' AND (status IS NULL OR status = 'discovered');
UPDATE jobs SET status = 'discovered' WHERE status IS NULL;

-- Index for the applicant agent's polling query
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);

-- Verify
SELECT status, COUNT(*) FROM jobs GROUP BY status;

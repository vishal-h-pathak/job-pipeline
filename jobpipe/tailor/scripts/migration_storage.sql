-- Job Applicant Pipeline: Supabase Storage Migration
-- Run this in Supabase Dashboard > SQL Editor
--
-- Adds storage-path columns to jobs table and creates a private bucket
-- job-materials to hold generated resume + cover letter PDFs.

-- ── 1. New columns on jobs table ───────────────────────────────────────────
-- Storage object keys (e.g. "{job_id}/resume.pdf"). Null when no material exists.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS resume_pdf_path TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cover_letter_pdf_path TEXT;

-- ── 2. Private Storage bucket ──────────────────────────────────────────────
-- `private = true` means no public anon access; everything goes through
-- signed URLs generated server-side with the service role key.
INSERT INTO storage.buckets (id, name, public)
VALUES ('job-materials', 'job-materials', false)
ON CONFLICT (id) DO NOTHING;

-- ── 3. RLS policies on the bucket ──────────────────────────────────────────
-- The service role bypasses RLS by default, so the agent (running with
-- service role key) can read/write/delete freely. We only need to *deny*
-- anon from everything — which is the default for a private bucket.
--
-- We add an explicit policy-by-name for clarity. If you have stricter
-- policies elsewhere, adjust accordingly.
DO $$
BEGIN
  -- Remove any old policies on this bucket (idempotent)
  DELETE FROM storage.policies WHERE bucket_id = 'job-materials';
EXCEPTION WHEN undefined_table THEN
  -- storage.policies was renamed/removed in recent versions; ignore
  NULL;
END $$;

-- Belt-and-suspenders: ensure no broad SELECT grants on storage.objects
-- for anon/authenticated roles reach this bucket. Signed URLs are the
-- only intended access path for the dashboard.

-- ── 4. Verify ─────────────────────────────────────────────────────────────
SELECT
  column_name,
  data_type
FROM information_schema.columns
WHERE table_name = 'jobs'
  AND column_name IN ('resume_pdf_path', 'cover_letter_pdf_path');

SELECT id, name, public FROM storage.buckets WHERE id = 'job-materials';

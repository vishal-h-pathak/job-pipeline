-- Migration 002: application_attempts.outcome — add 'prefilled', backfill
-- historical 'submitted' rows from the Path-A pre-fill close.
--
-- NOT YET APPLIED TO SUPABASE — review before running.
--
-- Context: P0 submit-truth-gate (reviews/REVIEW_2026-07-13_hiring_machine_
-- consolidation.md §3, PROMPT_submit_truth_and_verify.md item #2). The live
-- pre-fill path (tailor/pipeline.py::_prefill_one_job) closed every clean
-- adapter fill with outcome='submitted' — semantically wrong, since nothing
-- in that code path ever clicks Submit on the ATS; the human does that
-- themselves, and only jobs.status='applied' (a human click) means the
-- application was actually submitted. Renaming the Path-A close outcome to
-- 'prefilled' makes application_attempts.outcome legible on its own: a
-- 'prefilled' row means "the adapter finished filling cleanly", not
-- "this got submitted".
--
-- 'submitted' stays a valid value (extending, not narrowing, the enum) —
-- it remains legitimate vocabulary for the retired Browserbase Path B
-- (confirm.py / router.py, kept as reference only) and for any historical
-- row this migration doesn't touch.

ALTER TABLE application_attempts DROP CONSTRAINT IF EXISTS attempt_outcome_valid;
ALTER TABLE application_attempts
  ADD CONSTRAINT attempt_outcome_valid
    CHECK (
      outcome IS NULL OR outcome IN (
        'submitted', 'prefilled', 'needs_review', 'failed', 'in_progress'
      )
    );

-- Backfill: every historical Path-A clean-pre-fill close was recorded as
-- 'submitted' by the pre-rename code. Relabel to the new canonical value so
-- the audit trail reads correctly retroactively, not just going forward.
UPDATE application_attempts SET outcome = 'prefilled' WHERE outcome = 'submitted';

-- ── Verification ──────────────────────────────────────────────────────────

SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'application_attempts'::regclass
  AND conname = 'attempt_outcome_valid';

SELECT outcome, count(*) FROM application_attempts GROUP BY outcome ORDER BY outcome;

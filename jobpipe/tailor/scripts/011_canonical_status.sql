-- 011_canonical_status.sql — Canonical-only jobs.status (Session E)
--
-- M-2 defined the canonical 12-status lifecycle; migration 007 collapsed
-- the legacy in-flight rows and tightened jobs_status_check to the
-- canonical enum. This migration is the Session E re-assertion of that
-- end state, kept in the repo as the single SQL record of the
-- legacy -> canonical mapping:
--
--   ready_to_submit  -> ready_for_review       (materials ready, human
--                                               review pending — matches
--                                               how 007 collapsed it and
--                                               how the dashboard renders it)
--   tailored         -> ready_for_review       (pre-M-2 synonym)
--   needs_review     -> ready_for_review       (review queue alias)
--   submit_confirmed -> awaiting_human_submit  (human said go; browser step)
--   submitting       -> prefilling             (in-flight rename)
--   submitted        -> applied                (terminal-positive rename)
--
-- Inventory before this migration (2026-06-12): new=560, skipped=8,
-- approved=7 — zero legacy rows, so the UPDATEs below are no-ops kept
-- for idempotency on any straggler row restored from backup.
--
-- Reverse mapping (if a rollback ever needs to resurrect legacy values,
-- loosen the CHECK first, then invert): ready_for_review rows cannot be
-- split back into {ready_to_submit, tailored, needs_review} without the
-- pre-007 audit trail, so the inverse is lossy by design:
--   ready_for_review      -> ready_to_submit
--   awaiting_human_submit -> submit_confirmed
--   prefilling            -> submitting
--   applied               -> submitted
--
-- Apply in Supabase Dashboard > SQL Editor or via the MCP
-- `apply_migration` tool. Idempotent — re-runs are safe.

BEGIN;

UPDATE public.jobs SET status = 'ready_for_review',      status_updated_at = now() WHERE status IN ('ready_to_submit', 'tailored', 'needs_review');
UPDATE public.jobs SET status = 'awaiting_human_submit', status_updated_at = now() WHERE status = 'submit_confirmed';
UPDATE public.jobs SET status = 'prefilling',            status_updated_at = now() WHERE status = 'submitting';
UPDATE public.jobs SET status = 'applied',               status_updated_at = now() WHERE status = 'submitted';

-- Tighten (re-assert) the CHECK constraint to canonical-only. The list
-- must stay in lockstep with jobpipe/shared/status.json — that file is
-- the cross-repo source of truth the contract tests pin.
ALTER TABLE public.jobs DROP CONSTRAINT IF EXISTS jobs_status_check;
ALTER TABLE public.jobs ADD CONSTRAINT jobs_status_check CHECK (
  status = ANY (ARRAY[
    'discovered',
    'new',
    'approved',
    'preparing',
    'ready_for_review',
    'prefilling',
    'awaiting_human_submit',
    'applied',
    'failed',
    'skipped',
    'expired',
    'ignored'
  ]::text[])
);

COMMIT;

-- Verify: must return zero rows outside the canonical set.
SELECT status, count(*)
  FROM public.jobs
  WHERE status NOT IN (
    'discovered', 'new', 'approved', 'preparing', 'ready_for_review',
    'prefilling', 'awaiting_human_submit', 'applied', 'failed',
    'skipped', 'expired', 'ignored'
  )
  GROUP BY status;

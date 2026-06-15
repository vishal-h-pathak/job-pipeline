-- 013_link_status.sql — Direct-listing discovery gate (feat/hunt-direct-listings)
--
-- The hunter now resolves aggregator links to their real ATS URL and
-- checks posting openness AT DISCOVERY TIME (jobpipe.hunt.agent._execute),
-- instead of leaving that to the tailor/submit path and the weekly
-- liveness cron. link_status records how a surfaced row was classified:
--
--   direct                — resolved to a known ATS posting (greenhouse /
--                           lever / ashby / workday / …); application_url +
--                           ats_kind are set.
--   aggregator_unverified — aggregator link that could NOT be resolved to an
--                           ATS, but isn't a positive dead/suspicious drop;
--                           surfaced with a "⚠ unverified link" flag in the
--                           digest so direct vs aggregated is legible.
--   expired               — a positive dead signal (HTTP 404/410 or a
--                           closed-phrase match) at discovery time; the row
--                           is recorded (status='expired') so cross-source
--                           dedup won't re-surface it, and never notified.
--
-- Defaults NULL so pre-existing rows (and the ~6 already-queued aggregator
-- jobs) are simply "unclassified" until a future run touches them.
--
-- application_url + ats_kind already exist on public.jobs (application_url
-- from tailor/scripts/migration.sql; ats_kind from
-- submit/migrations/001_redesign.sql) — historically written by the
-- tailor's resolve step. The hunt gate now populates them earlier. This
-- migration only adds link_status.
--
-- Apply in Supabase Dashboard > SQL Editor or via the MCP
-- `apply_migration` tool. Idempotent — re-runs are safe. The ALTER is
-- additive and backfills existing rows to NULL without a long lock.

ALTER TABLE public.jobs
  ADD COLUMN IF NOT EXISTS link_status TEXT;

-- Verify
SELECT column_name, data_type, column_default, is_nullable
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = 'jobs'
    AND column_name = 'link_status';

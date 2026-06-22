-- 015_watcher_coordination.sql — dual-machine submit-watcher coordination
-- (feat/dual-machine-watcher)
--
-- The submit watcher (`jobpipe-submit --watch`) runs on TWO machines at once
-- (a MacBook via launchd and a Windows 11 PC via Task Scheduler). Both stay
-- alive permanently; correctness comes from a single toggle that names which
-- machine is allowed to *claim* prefilling jobs. The other machine keeps its
-- websocket/poll loop running but does nothing each cycle — "dormant" — so the
-- two never race to drive the same `jobs` row to a browser.
--
-- This migration adds the coordination state the watcher and dashboard share:
--
--   * watcher_config      — a single-row table holding `active_watcher_id`
--                           (which machine may claim). One row, enforced by a
--                           BOOLEAN primary key fixed to true.
--   * watcher_heartbeats  — one row per machine, refreshed every poll cycle, so
--                           the dashboard can show liveness ("desktop hasn't
--                           checked in for 3m") and warn before toggling to a
--                           dead machine.
--
-- Both tables are service-role-only (RLS enabled, no policies): the watcher and
-- the dashboard API routes use the service-role key and bypass RLS; no
-- anon/authenticated client should read them directly. (Same pattern as
-- public.runs in 008_runs.sql.)
--
-- Apply in Supabase Dashboard > SQL Editor or via the MCP `apply_migration`
-- tool. Idempotent — IF NOT EXISTS + ON CONFLICT DO NOTHING make a re-run a
-- no-op. *** NOT YET APPLIED to the live project — the human must apply it. ***

-- ── watcher_config (singleton) ─────────────────────────────────────────────
-- `id` is a BOOLEAN pinned to true: the CHECK + PRIMARY KEY allow exactly one
-- row, so `active_watcher_id` is a true singleton. NULL means "no machine is
-- active" → every watcher is dormant by default (safer than both acting).
CREATE TABLE IF NOT EXISTS public.watcher_config (
  id                 BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
  active_watcher_id  TEXT,
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed the singleton row (active_watcher_id NULL = nobody active yet).
INSERT INTO public.watcher_config (id, active_watcher_id)
VALUES (true, NULL)
ON CONFLICT (id) DO NOTHING;

-- ── watcher_heartbeats ─────────────────────────────────────────────────────
-- One row per machine. `state` reflects what the watcher decided last cycle.
-- `last_seen` drives the dashboard's live/stale indicator (stale = no heartbeat
-- in ~2× the poll interval).
CREATE TABLE IF NOT EXISTS public.watcher_heartbeats (
  watcher_id  TEXT PRIMARY KEY,
  last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
  state       TEXT CHECK (state IN ('active', 'dormant'))
);

CREATE INDEX IF NOT EXISTS watcher_heartbeats_last_seen_idx
  ON public.watcher_heartbeats (last_seen DESC);

ALTER TABLE public.watcher_config     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.watcher_heartbeats ENABLE ROW LEVEL SECURITY;


-- ── Verify ─────────────────────────────────────────────────────────────────
-- watcher_config has exactly one row; heartbeats empty on first run; RLS on.
SELECT COUNT(*) AS watcher_config_rows FROM public.watcher_config;
SELECT active_watcher_id FROM public.watcher_config WHERE id = true;
SELECT COUNT(*) AS heartbeat_rows FROM public.watcher_heartbeats;

SELECT relname, relrowsecurity
  FROM pg_class
 WHERE relname IN ('watcher_config', 'watcher_heartbeats')
 ORDER BY relname;

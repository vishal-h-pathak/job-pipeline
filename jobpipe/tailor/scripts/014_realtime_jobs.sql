-- 014_realtime_jobs.sql — add public.jobs to the Realtime publication
-- (feat/submit-watch)
--
-- The local submit watcher (`jobpipe-submit --watch`) subscribes to Supabase
-- Realtime `postgres_changes` on public.jobs, UPDATE events filtered to
-- status='prefilling'. The dashboard's "Pre-fill Form" click flips that row to
-- 'prefilling'; the watcher receives the event over its websocket and drives
-- the local browser on the click — no busy-polling.
--
-- A table only streams Realtime changes if it belongs to the
-- `supabase_realtime` publication. New Supabase projects ship that publication
-- empty, so this is REQUIRED for the watcher to receive any events. Until it is
-- applied the websocket connects and subscribes but never fires, and the
-- watcher silently behaves as if nothing is ever queued (its startup / reconnect
-- catch-up still picks rows up, but only on (re)connect — not on click).
--
-- Realtime only needs the status change streamed; no extra columns are
-- required. (REPLICA IDENTITY is left at its default — the new row's id +
-- status are enough for the watcher.)
--
-- Apply in Supabase Dashboard > SQL Editor or via the MCP `apply_migration`
-- tool. Idempotent — the guard makes a re-run a no-op.

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM pg_publication_tables
     WHERE pubname = 'supabase_realtime'
       AND schemaname = 'public'
       AND tablename = 'jobs'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.jobs;
  END IF;
END
$$;

-- Verify: jobs should now appear in the publication.
SELECT schemaname, tablename
  FROM pg_publication_tables
 WHERE pubname = 'supabase_realtime'
   AND schemaname = 'public'
   AND tablename = 'jobs';

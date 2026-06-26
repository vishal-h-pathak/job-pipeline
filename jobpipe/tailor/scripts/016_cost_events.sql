-- 016_cost_events.sql ── per-call billable event ledger + run rollup
--
-- Cost Tracker (S1, foundation). One row per billable call in
-- public.cost_events; a denormalized public.runs.cost_usd rollup powers
-- the dashboard ledger. Dollars are attributed only to the API-key path —
-- OAuth/subscription calls record token counts at cost_usd = 0.
--
-- Idempotent, applied via the project's Supabase migration tooling
-- (project sbmsxerwgylpfkkkjtku), same as every prior 0NN_*.sql. The
-- run_id FK references public.runs(id) (008_runs.sql), with
-- ON DELETE SET NULL so a deleted run leaves its cost events orphaned but
-- intact for all-time totals.
--
-- Run in Supabase Dashboard > SQL Editor or via the MCP
-- `apply_migration` tool.
--
-- ── cost_events table ────────────────────────────────────────────────────
-- One row per billable call. RLS is enabled with no policies, matching the
-- existing jobs/runs convention: the pipeline writes with the service-role
-- key (which bypasses RLS), and no anon/authenticated client should ever
-- read this directly.

create table if not exists public.cost_events (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz not null default now(),
  run_id      uuid null references public.runs(id) on delete set null,
  stage       text not null,            -- hunt|tailor|submit|chat|interview_prep|discovery
  service     text not null,            -- anthropic|serpapi|jsearch|browserbase|github_actions
  model       text null,                -- anthropic only
  auth_path   text null,                -- api_key|oauth  (oauth => cost_usd 0)
  job_id      text null,                -- jobs.job_id when call is job-scoped
  units       jsonb not null default '{}'::jsonb,  -- {input_tokens,output_tokens,cache_read,cache_creation} | {searches} | {requests}
  cost_usd    numeric(10,4) not null default 0
);

create index if not exists cost_events_run_id_idx     on public.cost_events(run_id);
create index if not exists cost_events_created_at_idx  on public.cost_events(created_at);
create index if not exists cost_events_stage_idx       on public.cost_events(stage);

alter table public.cost_events enable row level security;

-- ── runs rollup column ───────────────────────────────────────────────────
-- Denormalized sum of this run's cost_events.cost_usd, maintained by
-- jobpipe.shared.cost.rollup_run() at end-of-run.

alter table public.runs add column if not exists cost_usd numeric(10,4) not null default 0;


-- ── Verify ───────────────────────────────────────────────────────────────
-- Sanity counts: table empty on first run, indexes present, RLS on, and the
-- runs.cost_usd column exists.

select count(*) as cost_events_count from public.cost_events;

select indexname from pg_indexes
  where schemaname = 'public' and tablename = 'cost_events'
  order by indexname;

select relname, relrowsecurity
  from pg_class
  where relname = 'cost_events';

select column_name, data_type
  from information_schema.columns
  where table_schema = 'public' and table_name = 'runs' and column_name = 'cost_usd';

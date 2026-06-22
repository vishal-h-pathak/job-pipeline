# job-submitter

Second half of the split job-application pipeline. Consumes jobs tailored by
`job-applicant/` (soon to be renamed `job-tailor/`) and drives form submission
against the target ATS via Browserbase + Stagehand.

See `../JOB_APPLICATION_REDESIGN.md` for the design doc and `CLAUDE.md` here
for the per-service contract.

## Local setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in keys: Supabase, Browserbase, Anthropic
```

## Running the polling loop

```bash
jobpipe-submit
```

Polls Supabase every `POLL_INTERVAL_SECONDS` for jobs at `status=ready_to_submit`,
dispatches to the appropriate ATS adapter, and transitions the row to one of
`submitted` / `needs_review` / `failed`. The console script is wired by
`pyproject.toml` to `jobpipe.submit.runner:run`. The legacy
`python jobpipe/submit/runner.py` invocation also works.

## Running a single job (debugging)

```bash
python scripts/submit_one.py --job-id <id> [--headed] [--no-submit]
```

- `--headed` opens a visible browser on the Browserbase live-view URL
- `--no-submit` fills but stops before confirm.py's click-submit step

## Local submit watcher (`jobpipe-submit --watch`)

The dashboard is hosted (Vercel) and **cannot open a browser on your machine** —
clicking "Pre-fill Form" only flips a `jobs` row to `status='prefilling'`. The
watcher is the local half: a long-lived process on **your MacBook** that holds
**one persistent visible browser open** and drives it the instant a row hits
`prefilling`.

```bash
# Leave this running; it idles on a websocket and acts on each dashboard click.
jobpipe-submit --watch
# Polling fallback (no Realtime): re-scan the prefilling queue every N seconds.
jobpipe-submit --poll 15
```

How it behaves:

- **Acts on click.** Subscribes to Supabase Realtime `postgres_changes` on
  `public.jobs` (UPDATE → `status='prefilling'`). On an event it opens a tab in
  the shared window, fills via the per-ATS handler (or the universal fallback),
  hands off to assisted-manual on any failure, and blocks on the stop-and-wait
  advance until you flip the row to `applied`/`skipped` in the dashboard.
- **Startup + reconnect catch-up.** On launch and on every websocket
  (re)connect it first processes anything already sitting in `prefilling`, so
  clicks made while it was starting, asleep, or disconnected are not missed.
- **One job at a time.** A small internal queue serialises work — tabs don't
  stack and you submit sequentially.

### Browser: Chrome only (not Safari)

The driven browser is **Chromium-family via Playwright** — **Safari / WebKit
cannot be driven.** By default it uses a persistent profile with Playwright's
bundled Chromium (`~/.jobpipe/chrome-profile`, override with
`JOBPIPE_BROWSER_PROFILE`). To drive your **real Google Chrome** instead:

```bash
export JOBPIPE_BROWSER_CHANNEL=chrome   # or msedge / chrome-beta
```

**Log into your ATS accounts once** in that persistent profile (the first time a
tab opens, log in; cookies persist across runs). `JOBPIPE_BROWSER_CDP` still
lets you attach to an already-running Chrome you launched with
`--remote-debugging-port`.

### Threading note

Realtime in supabase-py 2.x is **async-only** (the sync realtime client raises
`NotImplementedError`). The watcher therefore runs an `AsyncRealtimeClient` on a
daemon thread that only drops job-ids / catch-up signals onto a thread-safe
queue; the **sync** Playwright context and all Supabase reads stay on the main
thread. See `watch.py` for details.

### Auto-start on macOS (launchd — set up once)

Package it as a per-user **LaunchAgent** (NOT a LaunchDaemon — it needs your GUI
login session to show a visible Chrome window) so it runs autonomously across
reboots/logins:

```bash
scripts/install_submit_watcher.sh                 # uses .venv (or venv), channel=chrome
scripts/install_submit_watcher.sh --channel chromium   # bundled Chromium instead
scripts/uninstall_submit_watcher.sh               # stop + remove
```

The installer renders `deploy/launchd/io.thak.jobpipe.submit-watch.plist` with
your repo path + venv + Node bin (launchd inherits no shell PATH; Node is needed
for `channel=chrome`), copies it to `~/Library/LaunchAgents/`, and loads it
(`launchctl bootstrap`, falling back to `load`). Logs go to
`~/Library/Logs/jobpipe-submit-watch.log`.

**Real constraint:** the Mac must be **awake and logged in** for a click to fire
a browser. Clicks while it's asleep/off simply queue in `prefilling`; the
watcher's startup/reconnect catch-up picks them up on next wake. Use
`caffeinate -s` (or Energy Saver) to keep it awake during a session.

## Database migrations

```bash
# Apply migration 001 in Supabase SQL editor, or via psql:
psql "$SUPABASE_DB_URL" -f migrations/001_redesign.sql
```

> **Required for the watcher:** apply
> `../tailor/scripts/014_realtime_jobs.sql` to the live project — it adds
> `public.jobs` to the `supabase_realtime` publication. Without it the websocket
> connects and subscribes but **never receives events** (the watcher then only
> acts via its startup/reconnect catch-up, not on click). Apply via the Supabase
> SQL editor or the MCP `apply_migration` tool; it's idempotent.

## Tests

```bash
pytest tests/
```

## Status — April 2026

Scaffold only. No working adapters yet. See the milestone checklist in
`JOB_APPLICATION_REDESIGN.md §9`.

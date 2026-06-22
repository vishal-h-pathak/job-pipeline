# Runbook — dual-machine submit watcher (MacBook + Windows PC)

The submit watcher (`jobpipe-submit --watch`) holds one visible browser open and
fills a job application the instant the dashboard flips a row to
`status='prefilling'`. This runbook sets up the watcher to run **always-on** on
**two machines at once** — a MacBook (launchd) and a Windows 11 PC (Task
Scheduler) — with a toggle that decides which single machine actually acts.

## Golden rule

> **Exactly one machine is "active" at a time. Both watchers stay running.**

Both machines keep their watch loop alive permanently. Only the machine whose id
equals `watcher_config.active_watcher_id` *claims* prefilling jobs and drives the
browser; the other stays alive but **dormant** (one cheap config read + one
heartbeat write per cycle, no browser, no claim). The toggle is what makes
"both auto-started" safe — they can't race because only one is ever active.

If `active_watcher_id` is unset (NULL), **every** machine is dormant by default
(safer than both acting) and each logs a one-time line telling you to pick one.

A job already in progress finishes its stop-and-wait cycle even if you flip the
toggle mid-job — the active-check only gates *claiming a new* job, never an
in-flight one.

---

## Machine ids

Each machine has a stable id from `JOBPIPE_WATCHER_ID` (defaults to the OS
hostname). The auto-start on each machine sets it explicitly:

| Machine    | `JOBPIPE_WATCHER_ID` | Auto-start              |
|------------|----------------------|-------------------------|
| MacBook    | `macbook`            | launchd LaunchAgent     |
| Windows PC | `desktop`            | Task Scheduler (at log on) |

Use whatever ids you like, as long as they match what you toggle to.

---

## One-time setup per machine

Do this once on **each** machine (Mac and Windows):

1. **Clone + venv + install**
   ```sh
   git clone <repo> job-pipeline && cd job-pipeline
   python -m venv .venv
   # macOS/Linux:  source .venv/bin/activate
   # Windows:      .venv\Scripts\Activate.ps1
   pip install -e .
   ```
2. **Browser driver** — `playwright install chromium` (or rely on real Chrome
   via `JOBPIPE_BROWSER_CHANNEL=chrome`, set by the auto-start).
3. **LaTeX (for tailor PDFs)** — MiKTeX on Windows, MacTeX/BasicTeX on macOS.
   Only needed if this machine also runs the tailor; the submit watcher itself
   does not render PDFs.
4. **`.env`** — copy the project `.env` to the repo root on this machine
   (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, etc.). `load_dotenv()` reads it
   from the working directory the auto-start sets to the repo root.
5. **ATS login in the persistent Chrome profile** — run `jobpipe-submit --watch`
   by hand once, click "Pre-fill Form" on a job, and log into your ATS accounts
   (Greenhouse / Lever / Ashby / Workday) when the tab appears. The persistent
   profile keeps you logged in for every later run.

---

## Apply the coordination migration (once, to the live DB)

> ⚠️ **Not applied automatically.** A human must apply
> `jobpipe/tailor/scripts/015_watcher_coordination.sql` to the live Supabase
> project (Dashboard → SQL Editor, or the MCP `apply_migration` tool). It is
> idempotent — re-running is a no-op.

It creates:
- `watcher_config` — singleton row holding `active_watcher_id` (seeded NULL).
- `watcher_heartbeats` — one row per machine for the dashboard's liveness view.

Until it is applied, the watcher's coordination reads fail and every machine
stays dormant (safe), so apply it before expecting either machine to claim jobs.

---

## Install the auto-start

### macOS (launchd)

```sh
scripts/install_submit_watcher.sh            # default channel=chrome
```
The LaunchAgent (`deploy/launchd/io.thak.jobpipe.submit-watch.plist`) runs
`jobpipe-submit --watch --poll 15` at login, restarts on failure, and sets
`JOBPIPE_WATCHER_ID=macbook`. It is a **LaunchAgent** (per-user GUI session),
not a LaunchDaemon, because the watcher opens a *visible* Chrome window that
needs your Aqua login session.

Tail the log: `tail -f ~/Library/Logs/jobpipe-submit-watch.log`

### Windows 11 (Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_submit_watcher.ps1
# or, explicit:
.\scripts\install_submit_watcher.ps1 -WatcherId desktop -Channel chrome
```
Registers a Scheduled Task that runs `jobpipe-submit --watch --poll 15` **at log
on** of the current user (the interactive desktop session is required to show a
visible Chrome window — the same reason the Mac uses a LaunchAgent, not a
LaunchDaemon). It restarts every minute on failure and keeps running. Env
(`JOBPIPE_BROWSER_CHANNEL=chrome`, `JOBPIPE_WATCHER_ID=desktop`) is set by a
generated wrapper `%LOCALAPPDATA%\jobpipe\run-submit-watch.cmd` that the task
invokes, because Task Scheduler can't set per-action env vars.

- Preview without registering: append `-WhatIf`.
- Start now without logging out: `Start-ScheduledTask -TaskName io.thak.jobpipe.submit-watch`
- Tail the log: `Get-Content -Wait "$env:LOCALAPPDATA\jobpipe\submit-watch.log"`
- Remove: `.\scripts\uninstall_submit_watcher.ps1` (add `-Purge` to delete the
  wrapper + log too).

---

## Toggle which machine is active

Pick the machine that should claim jobs. Flip it from **either** place:

### Dashboard (any device)
On `/dashboard`, the **Active watcher** control lists the known machines with a
live/stale dot, highlights the current active one, and switches it on one click.
If you pick a machine whose heartbeat is stale, it warns you first ("desktop
hasn't checked in for 3m — is its watcher running?") so you don't toggle to a
dead machine and wonder why nothing opens.

### CLI (any terminal on either machine)
```sh
jobpipe-submit --set-active macbook     # make the Mac the claimer
jobpipe-submit --set-active desktop     # make the Windows PC the claimer
jobpipe-submit --who-is-active          # print current active + heartbeats
jobpipe-submit --set-active             # (no value) also prints current
```

After flipping, the newly-active machine claims the next prefilling job; the
other goes dormant on its next cycle. With `--poll 15` that handoff takes up to
~15s. Stale = no heartbeat in roughly 2× the poll interval (~30s).

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Click does nothing on either machine | `active_watcher_id` is NULL — set one. Check the watcher log for the "No active watcher is set" line. |
| Click does nothing on the machine you expect | That machine isn't the active one — `jobpipe-submit --who-is-active` and re-toggle. |
| Dashboard shows a machine stale | Its watcher isn't running (logged out / asleep / crashed). Check that machine's log. |
| Migration not applied | Both machines stay dormant; apply `015_watcher_coordination.sql`. |
| Mac asleep at click time | Clicks queue in `prefilling`; the watcher's catch-up picks them up on wake. Use `caffeinate -s` to stay awake. |

# job-hunter

Autonomous job search agent for Vishal Pathak. Runs daily, searches multiple
job boards, scores listings against a profile using Claude, and writes results
to Supabase where they appear in the dashboard at vishal.pa.thak.io/dashboard.

---

## What this does

1. Pulls job listings from Indeed RSS, SerpAPI (Google Jobs), and RemoteOK
2. Deduplicates against jobs already in Supabase
3. Scores each new job against `CLAUDE.md` (the candidate profile) using Claude
4. Writes all scored jobs to Supabase
5. Logs everything to `agent.log`

The dashboard (companion repo: `portfolio`) reads from the same Supabase instance
and presents jobs in a swipe/browse interface.

---

## Project structure

```
job_agent.py          # Main orchestration — runs the full pipeline
scorer.py             # Scores jobs against CLAUDE.md using Claude API
db.py                 # Supabase read/write (upsert_job, get_seen_ids)
notifier.py           # Resend email notifier (legacy — dashboard preferred)
DATA_CONTRACT.md      # User-layer / system-layer file boundary (J-10)
profile/              # USER LAYER — hand-edited, never overwritten by code
  profile.yml         # Identity, comp, tiers, archetypes, form defaults
  disqualifiers.yml   # Hard disqualifiers + soft concerns
  cv.md               # Master CV (mirrors latex_resume.py BASE_RESUME)
  article-digest.md   # Curated proof points + metrics
  portals.yml         # ATS company → slug map + title pre-filter (J-1)
  learned-insights.md # Generalizable preferences captured by Match Agent (J-11)
prompts/              # Versioned task prompts (J-7)
  _shared.md          # Global rules — anti-slop, ethics, specificity (J-5)
  scorer.md           # Job-fit + posting-legitimacy scoring (J-2)
sources/
  _portals.py         # Shared YAML loader + title pre-filter (J-1)
  greenhouse.py       # Direct Greenhouse boards-api scanner
  lever.py            # Direct Lever postings-api scanner
  ashby.py            # Direct Ashby posting-api scanner
  workday.py          # Direct Workday job-search scanner (J-1)
  hn_whoshiring.py    # Algolia HN search
  eighty_thousand_hours.py  # 80k Hours mission-driven board
  remoteok.py         # RemoteOK public API
  jsearch.py, serpapi.py    # Paid aggregators
scripts/
  check_liveness.py   # Stale-posting rechecker (J-8)
utils/
  validator.py        # URL validation before notifying
CLAUDE.md             # Narrative profile aggregator (compat fallback)
run_agent.sh          # Shell script for cron execution
seen_jobs.json        # Local backup of processed job IDs
agent.log             # Run logs with timestamps
```

## What changed (career-ops integration, 2026-04-27)

- **J-1**: Discovery now reads from `profile/portals.yml`. Each ATS lives
  in its own source module; new modules added for Lever and Workday.
  Cheap title pre-filter rejects obvious leadership/intern/recruiter
  titles before the LLM scorer.
- **J-2**: Scorer also emits posting-legitimacy
  (high_confidence/proceed_with_caution/suspicious). Stored separately;
  never affects fit. Surfaces as a colored pill in the dashboard.
- **J-7**: Every inline prompt extracted into `prompts/*.md` with a
  global `_shared.md` (anti-slop banned phrases, ethics, specificity,
  Unicode hygiene).
- **J-10**: `profile/` is the user-layer source of truth. CLAUDE.md
  remains as a narrative aggregator + compat fallback.
- **J-8**: Liveness rechecker runs nightly via cron, transitions dead
  postings to `expired` status.
- **J-11**: Companion Match Agent → profile writeback flow appends
  generalizable insights to `profile/learned-insights.md`; the loader
  picks them up automatically.

---

## The profile (CLAUDE.md)

`CLAUDE.md` is the single most important file in this repo. It contains Vishal's
background, job search priorities, disqualifiers, compensation expectations, and
portfolio goals. Every scoring decision is made against this document.

Update it as priorities change. The scorer reads it fresh on every run.

### Job tiers
- **Tier 1** — Computational neuroscience, neuromorphic engineering, connectomics,
  embodied simulation, BCI. Notify if score >= 7.
- **Tier 2** — Sales engineering in genuinely interesting AI/LLM domains.
  Notify if score >= 7.
- **Tier 3** — Mission-driven ML/CV engineering. Notify if score >= 8.
- **Disqualify** — DoD/defense, government, academic positions (postdoc, professor,
  PhD programs), roles with no clear mission.

---

## Scoring

Each job is sent to `claude-sonnet-4-6` with the full CLAUDE.md profile and the
job title, company, location, and description. The model returns:

```json
{
  "score": 8,
  "tier": 1,
  "reasoning": "2-3 sentence explanation",
  "recommended_action": "notify"
}
```

Jobs scoring below threshold are still written to Supabase (for browsing) but
not flagged for notification.

---

## Sources

| Source             | Method                                  | Cost     | Notes |
|--------------------|-----------------------------------------|----------|-------|
| Greenhouse + Lever | Public ATS JSON, curated company list   | Free     | Tier 1 dense; expand in `sources/greenhouse.py` |
| Ashby              | Public posting API, curated list        | Free     | AI-startup heavy; expand in `sources/ashby.py` |
| HN Who's Hiring    | Algolia HN search → monthly thread      | Free     | Best signal for fresh AI/ML startup roles |
| 80,000 Hours       | Public Algolia (`jobs_prod` index)      | Free     | Mission-driven — alignment, biosec, neuro |
| RemoteOK           | Public JSON API                         | Free     | Broad remote-only coverage |
| JSearch            | RapidAPI (Indeed + LinkedIn + ZipRecruiter etc.) | Paid (~$10/mo) | Capped at `JSEARCH_MAX_REQUESTS_PER_RUN` (default 8). Replaces the dead Indeed RSS + LinkedIn-via-SerpAPI sources. |
| SerpAPI            | Google Jobs API                         | Paid     | Capped at `SERPAPI_MAX_SEARCHES` (default 15) |

Sources kept on disk but excluded from the live pipeline:

- `sources/indeed.py` — Indeed RSS is gated for unauthenticated callers as
  of 2026-04-26. Re-add to `SOURCES` in `job_agent.py` if you obtain
  authenticated access.
- `sources/linkedin.py` — `site:linkedin.com/jobs` queries via SerpAPI
  return zero results across multiple runs. JSearch covers LinkedIn
  postings with a different mechanism.
- `sources/wellfound.py` — Stub. No public API; placeholder.

---

## Environment variables

```
ANTHROPIC_API_KEY=     # For scoring via Claude API
SERPAPI_KEY=           # SerpAPI key (100 free searches/month)
SUPABASE_URL=          # Supabase project URL
SUPABASE_KEY=          # Supabase anon key
RESEND_API_KEY=        # Resend email API (legacy)
NOTIFY_FROM=           # From address for email notifications (legacy)
NOTIFY_TO=             # Your email address (legacy)
```

Copy `.env.example` to `.env` and fill in values.

---

## Running manually

```bash
pip3 install -r requirements.txt
cp .env.example .env   # fill in your keys
jobpipe-hunt --once                  # local_remote (default): Atlanta + Remote
jobpipe-hunt --once --mode us_wide   # also pull non-remote US roles
```

Output:
```
done. mode=local_remote new jobs: 41, enriched: 6, dead links skipped: 2, notified: 5
```

### Operating modes

| Mode           | Sources include                                                 |
|----------------|-----------------------------------------------------------------|
| `local_remote` | Atlanta-area + remote-only roles. Greenhouse boards filtered.   |
| `us_wide`      | Adds national-US roles across SerpAPI / LinkedIn / Indeed.      |

Mode resolves from (in priority order) `--mode`, the `HUNTER_MODE` env var,
then `local_remote`.

### Cost guards

- `SERPAPI_MAX_SEARCHES` (default 30): hard cap on SerpAPI calls per run.
- `LINKEDIN_MAX_SEARCHES` (default 15): independent cap for the LinkedIn
  variant so it doesn't crowd out the main SerpAPI source.

---

## Automated daily runs

A cron job runs the agent every day at 8am:

```
0 8 * * * /Users/jarvis/dev/jarvis/job-hunter/run_agent.sh
```

`run_agent.sh` logs timestamped output to `agent.log`. Check it with:

```bash
tail -50 ~/dev/jarvis/job-hunter/agent.log
```

---

## Supabase schema

Jobs are written to a `jobs` table. See the companion `portfolio` repo README
for the full schema. The `created_at` field is set on first insert and never
overwritten — it reflects when the job was first discovered.

---

## Companion repo

**portfolio** — the Next.js dashboard at vishal.pa.thak.io that reads from the
same Supabase instance and presents jobs in swipe/browse modes with a
Claude-powered Match Agent for application tailoring.
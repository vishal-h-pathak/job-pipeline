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
sources/
  indeed.py           # Indeed RSS feed fetcher
  serpapi.py          # SerpAPI Google Jobs fetcher
  remoteok.py         # RemoteOK public API fetcher
  wellfound.py        # Stub — no public API available
utils/
  validator.py        # URL validation before notifying
CLAUDE.md             # Candidate profile — ground truth for all scoring
run_agent.sh          # Shell script for cron execution
seen_jobs.json        # Local backup of processed job IDs
agent.log             # Run logs with timestamps
```

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
python3 job_agent.py                 # local_remote (default): Atlanta + Remote
python3 job_agent.py --mode us_wide  # also pull non-remote US roles
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
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
python main.py
```

Polls Supabase every `POLL_INTERVAL_SECONDS` for jobs at `status=ready_to_submit`,
dispatches to the appropriate ATS adapter, and transitions the row to one of
`submitted` / `needs_review` / `failed`.

## Running a single job (debugging)

```bash
python scripts/submit_one.py --job-id <id> [--headed] [--no-submit]
```

- `--headed` opens a visible browser on the Browserbase live-view URL
- `--no-submit` fills but stops before confirm.py's click-submit step

## Database migrations

```bash
# Apply migration 001 in Supabase SQL editor, or via psql:
psql "$SUPABASE_DB_URL" -f migrations/001_redesign.sql
```

## Tests

```bash
pytest tests/
```

## Status — April 2026

Scaffold only. No working adapters yet. See the milestone checklist in
`JOB_APPLICATION_REDESIGN.md §9`.

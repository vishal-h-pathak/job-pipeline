# job-submitter — Agent Profile

This service is the second half of the split job-application pipeline. The first
half (job-tailor, currently still called `job-applicant/`) owns LaTeX resume
generation and cover letter authorship. This service picks up jobs that the
tailor has marked ready and drives the actual form submission.

## Contract with the tailor (input)

A job is eligible for this service when the `jobs` row has:

- `status = 'ready_to_submit'` (legacy) OR `status = 'tailored'` (future)
- `resume_pdf_path` — Supabase Storage key under `job-materials/{job_id}/resume.pdf`
- `cover_letter_pdf_path` — Storage key for the cover letter PDF
- `cover_letter_path` — plain-text cover letter body (for form-paste fields)
- `application_url` — canonical ATS URL (aggregator-resolved)
- `ats_kind` — one of: greenhouse, lever, ashby, workday, icims, smartrecruiters, linkedin, generic
- `materials_hash` — sha256 of the resume PDF + CL text at approval time

If any required field is missing, the submitter does not proceed; it flips the
job to `needs_review` with a reason.

## Contract with the dashboard (output)

On each attempt, the submitter writes:

- `submission_log` (jsonb): structured events from this attempt
- `confidence` (real 0–1): submitter's self-assessed readiness at submit time
- A row in `application_attempts` with outcome + Browserbase replay URL
- `status`: `submitted`, `needs_review`, or `failed`

The portfolio dashboard renders `/review/[job_id]` from these fields.

## Architecture

```
main.py (poll loop)
  └── router.py  (dispatch by ats_kind)
        ├── adapters/greenhouse.py       deterministic Stagehand act() sequence
        ├── adapters/lever.py            deterministic
        ├── adapters/ashby.py            deterministic
        └── adapters/generic_stagehand.py  Stagehand Agent fallback
              │
              ▼
        browser/session.py  (Browserbase + Stagehand session)
              │
              ▼
        confirm.py  (decide auto-submit vs needs_review, verify success)
              │
              ▼
        review/packet.py  (build review packet if needs_review)
```

## Design rules

- **Adapters fill. confirm.py decides whether to submit.** Adapters NEVER click
  the final submit button; they return a `SubmissionResult` with evidence and
  a recommendation. `confirm.py` applies the uniform auto-vs-review policy.
- **One Browserbase session per attempt.** Always record. Hard-cap session
  budget via env var.
- **LLM use is bounded.** Only two places: (1) confirm.py's post-submit page
  analysis, (2) adapters/generic_stagehand.py fallback. Deterministic adapters
  use zero LLM calls.
- **Every state transition writes a row to application_attempts.** Never update
  `jobs.status` to `submitted` without a corresponding attempts row showing
  the evidence.

## Design doc

Full background, rationale, and milestone plan:
`../JOB_APPLICATION_REDESIGN.md`

## Candidate profile

The candidate (Vishal) is documented in `../job-applicant/CLAUDE.md`. The
submitter doesn't need this for routine fills (materials already carry the
content) but it's the reference when extending the generic fallback to answer
free-form screening questions.

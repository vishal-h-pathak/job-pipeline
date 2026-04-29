# Vishal Pathak — Agent Profile

> Structured truth lives in `profile/profile.yml`; voice rules in `profile/voice-profile.md`.
> This file is the human-readable narrative aggregator the tailor reads as
> LLM prompt context (`jobpipe.tailor.paths.CANDIDATE_PROFILE_PATH`).
> Edit the structured files in `profile/` first; mirror to this file when
> the tailor needs the prose form.

PR-9 consolidated the three previous repo-level `CLAUDE.md` files (one
each under `jobpipe/{hunt,tailor,submit}/`) into this single file. The
identity prose lives here once; subpackage-specific design rules live in
each subpackage's `README.md`.

## Identity

EE background with a long-running focus on neuromorphic hardware and
brain-inspired computing. The through-line is emergence — the
Hodgkin-Huxley model in college (ion channels as RC circuits scaling to
cognition) pointed him toward this and he has not wandered far since.
Rain Neuromorphics at 19 as employee #5, building memristive LIF neuron
PCBs by hand. Four years at GTRI doing SNN deployment on Intel Kapoho
Bay, VHDL neuron modeling, and eventually broader computer vision and
embedded ML. Now looking to get back closer to the neuroscience end of
the spectrum.

## What he's looking for

**Tier 1:** Computational neuroscience, neuromorphic engineering,
connectomics, embodied simulation, BCI. eon.systems is the
reference-point role.
**Tier 2:** Sales engineering in AI/LLM. Strong communicator, rare
technical depth, no formal sales experience but has pitched to DoD
sponsors. Would need to be a domain he finds genuinely interesting.
**Tier 3:** ML/CV engineering at mission-driven organizations. Heavily
dependent on the company.

**Disqualifiers:**
- DoD/defense contracts, government, roles with no clear mission.
- Academic positions (postdoc, professor, PhD programs) — no PhD.

## Location & compensation

- Atlanta, GA. Open to fully remote.
- Open to relocation only if mission + comp are both exceptional.
  eon.systems is the bar.
- Current: ~$110k. Target: $120–140k. Will consider same comp for the
  right role.

## How he works

Good communicator, creative problem-solver, works best with clear
direction and a compelling reason to solve the problem. Self-aware about
needing external structure to stay focused. Strong once pointed at
something.

## Key technical skills

FlyGym, MuJoCo, Brian2, Gymnasium API, VHDL SNN implementation, Intel
Kapoho Bay (Loihi 1/2), memristive hardware, DNN→SNN conversion,
PyTorch, TensorFlow, HPC training, RT-DETRv2, embedded ML (Jetson Orin),
PCB design (EagleCAD/Altium), PyQt6 desktop GUI development, serial
protocol integration (RS-232, RS-485), ruggedized sensor + cable
deployment, AFSIM surrogate modeling, C++, Python.

## Portfolio goal

`vishal.pa.thak.io` should feel like a person with a specific
long-running obsession, not a generated candidate page. The thread from
Hodgkin-Huxley → memristors → spiking networks → connectomics should be
legible. Prioritize personality and genuine content over polish.

## Personal

From Cape Canaveral, FL. Moved to Atlanta April 2022. Runs a book club
(papercuts.org). Into cooking, audiobooks, agentic AI projects.

## Application form defaults

Canonical answers the submitter's three-tier classifier reads (see
`jobpipe/submit/adapters/_common.py::applicant_fields`). When the tailor
populates `applicant_profile` on each `jobs` row, these values flow
through verbatim.

- `work_authorization`: `us_citizen`
- `visa_sponsorship_needed`: `no`
- `earliest_start_date`: as early as possible; typical notice is two
  weeks after offer acceptance
- `relocation_willingness`: based in Atlanta, GA and strongly prefers
  remote or local roles; open to relocation only if remote/local options
  are exhausted and the role + compensation are both exceptional
- `in_person_willingness`: remote or hybrid acceptable; fully remote
  strongly preferred
- `ai_policy_ack`: "I am transparent about my use of AI assistance in
  my work. I use AI tools (including LLMs) to accelerate drafting,
  research, and exploration, but I always keep a human in the loop: I
  review, validate, and take responsibility for all work I produce."
- `previous_interview_with_company`: `{ "anthropic": false }` (extend
  per company as history accumulates)

---

# jobpipe — unified hunt → tailor → submit pipeline

Three console scripts live in `pyproject.toml::[project.scripts]`:

| Script | Entry point | Role |
|---|---|---|
| `jobpipe-hunt`   | `jobpipe.hunt.agent:run`                         | discover roles, score, upsert |
| `jobpipe-tailor` | `jobpipe.tailor.pipeline:run_tailor_only`        | tailor resume / cover letter / form answers (no browser) |
| `jobpipe-submit` | `jobpipe.tailor.pipeline:run_submit_only`        | visible-browser pre-fill for rows the cockpit enqueued |

PR-13 split the tailor's combined cycle into two narrower entry points
so an automated trigger (CI, cron) can hit `jobpipe-tailor` without
opening a visible browser. The retired Browserbase + Stagehand runner
lives at `jobpipe/submit/runner_legacy.py` and has no console-script
binding; PR-13 reused the `jobpipe-submit` script name on purpose for
the local-Playwright pre-fill phase.

## Cross-cutting modules (canonical, no per-subtree shims after PR-9)

- `jobpipe.config` — every env-driven knob and helper. Soft defaults
  (empty string for secrets) so the module imports without credentials.
  `jobpipe.submit.config` re-promotes the submit-required secrets via
  `require_env` (fail-loud at import).
- `jobpipe.db` — Supabase data layer. Lazy module-level singleton; the
  `client` / `service_client` attributes resolve via module
  `__getattr__` so the import is side-effect-free.
- `jobpipe.notify` — Resend digest (hunt) + Supabase notifications table
  (tailor / submit). Canonical `send_*` names plus deprecated `notify_*`
  aliases that emit a one-shot deprecation warning.
- `jobpipe.shared.*` — `jobid`, `validator`, `html`, `storage`,
  `ats_detect`. Pure helpers used by ≥2 subtrees.

## Hunt subtree (`jobpipe/hunt/`)

Discovery + scoring + upsert. Two modes:

- `local_remote` (default): Atlanta + remote roles only.
- `us_wide`: also pulls non-remote US roles.

Sources are pure HTTP/JSON or RSS — no LLM tokens spent on discovery.
Each posting is title-pre-filtered against
`jobpipe/hunt/profile/portals.yml::title_filter` before the LLM scorer
sees it. `jobpipe.db.get_seen_ids()` deduplicates against past runs in
Supabase (PR-3 retired the JSON-state file).

## Tailor subtree (`jobpipe/tailor/`)

Polls Supabase for approved jobs, generates a tailored resume +
LaTeX-rendered PDF + cover letter + (optional) form answers, marks the
row `ready_to_submit` (or `ready_for_review` if human approval is
required). Materials live in Supabase Storage (`job-materials/{job_id}/`)
— never on disk except for ephemeral diagnostics in
`jobpipe.tailor.paths.OUTPUT_DIR`.

## Submit subtree (`jobpipe/submit/`)

### Contract with the tailor (input)

A job is eligible when the `jobs` row has:
- `status = 'ready_to_submit'` (legacy) OR `status = 'tailored'`
- `resume_pdf_path` + `cover_letter_pdf_path` (Storage keys)
- `cover_letter_path` — plain-text body for form-paste fields
- `application_url` — canonical ATS URL (aggregator-resolved)
- `ats_kind` — one of: greenhouse, lever, ashby, workday, icims,
  smartrecruiters, linkedin, generic
- `materials_hash` — sha256 of resume PDF + CL text at approval time

If any required field is missing the submitter does not proceed; it
flips the row to `needs_review` with a reason.

### Contract with the dashboard (output)

Per attempt:
- `submission_log` (jsonb): structured events
- `confidence` (0–1): submitter's self-assessed readiness at submit time
- A row in `application_attempts` with outcome + Browserbase replay URL
- `status`: `submitted`, `needs_review`, or `failed`

### Architecture

```
runner.py (poll loop)
  └── router.py  (dispatch by ats_kind)
        ├── adapters/deterministic/greenhouse.py    deterministic Stagehand act() sequence
        ├── adapters/deterministic/lever.py         deterministic
        ├── adapters/deterministic/ashby.py         deterministic
        └── adapters/generic_stagehand.py           Stagehand Agent fallback
              │
              ▼
        browser/session.py  (Browserbase + Stagehand session)
              │
              ▼
        confirm.py  (decide auto-submit vs needs_review, verify success)
              │
              ▼
        review_packet.py  (build review packet if needs_review)
```

### Design rules

- **Adapters fill. `confirm.py` decides whether to submit.** Adapters
  NEVER click the final submit button; they return a `SubmissionResult`
  with evidence and a recommendation. `confirm.py` applies the uniform
  auto-vs-review policy.
- **One Browserbase session per attempt.** Always record. Hard-cap
  session budget via env var.
- **LLM use is bounded.** Only two places: (1) `confirm.py`'s
  post-submit page analysis, (2) `adapters/generic_stagehand.py`
  fallback. Deterministic adapters use zero LLM calls.
- **Every state transition writes a row to `application_attempts`.**
  Never update `jobs.status` to `submitted` without a corresponding
  attempts row showing the evidence.

## Where the data lives

- `jobs` table — main pipeline row (hunt writes; tailor / submit
  transition status)
- `application_attempts` — audit trail per submit attempt
- `notifications` — written by `jobpipe.notify`
- `star_stories` — interview prep stories (written by tailor's
  `interview_prep` module)
- `pattern_analyses` — closed-loop pattern analysis output
  (`jobpipe/tailor/scripts/analyze_patterns.py`)
- Supabase Storage `job-materials/{job_id}/` — resume.pdf,
  cover_letter.pdf, prefill.png, review/*.png

# Architecture

Two diagrams for this repo: the **pipeline** (hunt → tailor → submit) and the
**system context** (how this repo and the `portfolio` repo connect). Both are
plain-text Mermaid so they render on GitHub, in any markdown viewer, and stay
easy to update alongside the code. Polished standalone HTML renders (Mermaid
via CDN, light theme) live in [`docs/diagrams/`](diagrams/):
[`pipeline.html`](diagrams/pipeline.html) and
[`system-context.html`](diagrams/system-context.html).

## 1. Pipeline architecture

**Legend:** yellow subgraphs are in-repo stages (`jobpipe/hunt`,
`jobpipe/tailor`, `jobpipe/submit`, `jobpipe/shared`); grey boxes are external
systems (Supabase, Anthropic, GitHub Actions, SerpAPI/JSearch, Resend); the
amber box is the canonical `status.py` enum every stage transition keys off.
Green boxes mark the two most recent additions to submit: the post-decision
browser-truth gate and the fill-rate drift aggregator.

```mermaid
flowchart TD
    classDef external fill:#eef0f2,stroke:#7a7a7a,stroke-width:1px,color:#333;
    classDef spine fill:#fff6da,stroke:#b8860b,stroke-width:2px,color:#333;
    classDef added fill:#dff5e1,stroke:#2f7a3d,stroke-width:2px,color:#333;

    GHA["GitHub Actions<br/>hunt.yml / tailor.yml<br/>cron + dashboard dispatch"]:::external

    subgraph HUNT["HUNT — discovery (jobpipe/hunt/)"]
        H1["Sources<br/>direct-ATS pollers + free search + paid search"]
        H2["Enricher<br/>fetch JD text, resolve aggregator to real ATS URL"]
        H3["Scorer (LLM)<br/>fit tier + legitimacy"]
    end

    subgraph TAILOR["TAILOR — material generation (jobpipe/tailor/)"]
        T1["Poll approved jobs"]
        T2["Classify archetype"]
        T3["Generate resume (LLM)"]
        T4["Render LaTeX resume PDF"]
        T5["Generate cover letter + form answers"]
        T6["Upload materials to Storage"]
    end

    subgraph SUBMIT["SUBMIT — local Playwright pre-fill (jobpipe/submit/) — human's machine only"]
        S1["Prefill queue<br/>status = prefilling"]
        S2["Deterministic adapters<br/>Greenhouse / Lever / Ashby"]
        S3["Universal agent fallback<br/>Claude tool-use"]
        S4["DOM re-read + verify<br/>scan_required_fields / build_prefill_verification"]
        S5["Assisted-manual hand-off<br/>tab open + materials staged + checklist"]
        S6["Human review + submit<br/>status = awaiting_human_submit"]
        S7["Browser-truth gate<br/>page_truth.py capture_truth"]:::added
        S8["Fill-rate drift aggregator<br/>detect_fill_drift.py"]:::added
    end

    subgraph SHARED["SHARED (jobpipe/shared/, jobpipe/notify.py)"]
        SH1["status.py<br/>canonical JobStatus enum"]:::spine
        SH3["notify.py<br/>Resend digest + notifications table"]
    end

    PG[("Supabase Postgres<br/>jobs / runs / application_attempts / notifications")]:::external
    ST[("Supabase Storage<br/>job-materials/{job_id}/")]:::external
    ANT["Anthropic<br/>API + Max-plan OAuth fallback"]:::external
    PAID["SerpAPI / JSearch"]:::external
    RS["Resend"]:::external

    GHA -->|"cron 14:00 UTC / dispatch: run hunt"| H1
    PAID -->|"paid search results"| H1
    H1 -->|"raw postings"| H2
    H2 -->|"JD text + resolved URL"| H3
    ANT -->|"LLM scoring call"| H3
    H3 -->|"upsert: status = discovered/new"| PG

    GHA -->|"cron / dispatch: run tailor"| T1
    PG -->|"status = approved"| T1
    T1 -->|"job row"| T2
    T2 -->|"archetype"| T3
    ANT -->|"LLM generation call"| T3
    T3 -->|"resume content"| T4
    T3 -->|"resume content"| T5
    T4 -->|"resume.pdf"| T6
    T5 -->|"cover_letter.pdf + form answers"| T6
    T6 -->|"job-materials/{job_id}/*"| ST
    T6 -->|"status = ready_for_review"| PG

    PG -->|"status = prefilling (cockpit Pre-fill click)"| S1
    S1 -->|"job + field_maps.yml (known ATS)"| S2
    S1 -->|"job (no ATS map)"| S3
    ANT -->|"tool-use fill calls"| S3
    S2 -->|"fill attempt"| S4
    S3 -->|"fill attempt"| S4
    S4 -->|"required fields still empty"| S5
    S4 -->|"all required fields filled"| S6
    S5 -->|"status = awaiting_human_submit"| S6
    S4 -->|"application_attempts.notes.fill_report"| PG
    PG -->|"fill_report history per ATS"| S8
    S8 -->|"15pp fill-rate drop"| SH3
    S6 -->|"status = applied / skipped / failed / expired"| PG
    S6 -->|"terminal decision reached"| S7
    S7 -->|"application_attempts.notes.truth"| PG
    S7 -->|"success-signal mismatch"| SH3

    SH1 -.->|"defines canonical statuses"| PG
    SH3 -->|"email digest"| RS
```

## 2. System context — job-pipeline ⇄ portfolio

**This diagram is canonical here.** The copy committed in
`portfolio/docs/ARCHITECTURE.md` is a reference mirror — update this one
first, then sync the copy.

**Legend:** yellow subgraphs are each repo's collapsed internals; blue is the
human operator; grey boxes are external systems (Supabase, GitHub Actions,
Anthropic, SerpAPI/JSearch, Resend, Vercel); the amber box is the shared
status-lifecycle contract. **Supabase is the only bus** — job-pipeline and
portfolio never call each other directly, they meet at the database.
**Submit is the one exception to CI**: it runs locally on the human's machine
and is only ever given intent (a status row), never dispatched by GitHub
Actions.

```mermaid
flowchart TD
    classDef external fill:#eef0f2,stroke:#7a7a7a,stroke-width:1px,color:#333;
    classDef spine fill:#fff6da,stroke:#b8860b,stroke-width:2px,color:#333;
    classDef human fill:#e6f0ff,stroke:#3060a8,stroke-width:1.5px,color:#333;

    subgraph JOBPIPE["job-pipeline (Python)"]
        JP_HUNT["hunt<br/>GitHub Actions cron 14:00 UTC + dispatch"]
        JP_TAILOR["tailor<br/>GitHub Actions cron + dispatch"]
        JP_SUBMIT["submit<br/>local Playwright, human's machine — never CI"]
    end

    subgraph PORTFOLIO["portfolio (Next.js on Vercel)"]
        PF_MKT["Marketing site<br/>vishal.pa.thak.io"]
        PF_CONSOLE["Console · Jobs tab<br/>the operator cockpit"]
        PF_API["api/console/dashboard/*<br/>service-role routes"]
    end

    HUMAN["Human operator"]:::human

    SUPA[("Supabase<br/>jobs / runs / application_attempts / notifications + Storage")]:::external
    GHA[("GitHub Actions<br/>workflow_dispatch")]:::external
    ANT["Anthropic<br/>API + Max-plan OAuth"]:::external
    PAID["SerpAPI / JSearch"]:::external
    RESEND["Resend"]:::external
    VERCEL["Vercel"]:::external
    STATUS["Job status lifecycle spine<br/>jobpipe/shared/status.py"]:::spine

    GHA -->|"cron 14:00 UTC: hunt.yml"| JP_HUNT
    PAID -->|"paid search"| JP_HUNT
    ANT -->|"LLM scoring"| JP_HUNT
    JP_HUNT -->|"upsert: status=discovered/new"| SUPA
    JP_HUNT -->|"digest email"| RESEND

    PF_API -->|"workflow_dispatch(hunt.yml / tailor.yml / tailor-manual.yml)"| GHA
    GHA -->|"triggers: tailor.yml"| JP_TAILOR
    PF_CONSOLE -->|"approve job: status=approved"| SUPA
    SUPA -->|"poll status=approved"| JP_TAILOR
    ANT -->|"LLM resume + cover-letter generation"| JP_TAILOR
    JP_TAILOR -->|"materials + status=ready_for_review"| SUPA

    SUPA -->|"render review + materials"| PF_CONSOLE
    PF_CONSOLE -->|"click Pre-fill: status=prefilling"| SUPA
    SUPA -->|"poll status=prefilling (local watcher — NOT CI)"| JP_SUBMIT
    ANT -->|"universal-agent fill fallback"| JP_SUBMIT
    HUMAN -->|"reviews pre-fill, clicks Submit on the ATS"| JP_SUBMIT
    JP_SUBMIT -->|"status=awaiting_human_submit + browser-truth capture"| SUPA

    SUPA -->|"render Submit / Mark Applied controls"| PF_CONSOLE
    HUMAN -->|"clicks Mark Applied / Skip"| PF_CONSOLE
    PF_CONSOLE -->|"status=applied / skipped / failed / expired"| SUPA

    PF_CONSOLE -->|"jobs CRUD, run buttons, materials, chat"| PF_API
    PF_API -->|"service-role reads/writes"| SUPA
    PF_MKT -->|"public sanitized telemetry"| SUPA
    VERCEL -->|"hosts + serves"| PF_MKT
    VERCEL -->|"hosts + serves"| PF_CONSOLE

    STATUS -.->|"canonical enum source"| SUPA
    STATUS -.->|"mirrored: portfolio lib/job-status.generated.ts"| PF_CONSOLE
```

## Keeping this current

These diagrams are hand-maintained, not generated, so they will drift the
moment a stage, route, or external service changes without a matching edit
here. Treat a diagram update as part of the same PR whenever you: add or
remove a hunt source, tailor stage, or submit adapter; add a new external
service dependency; change a status value in
`jobpipe/shared/status.py` (the lifecycle spine both diagrams key off); or
change how job-pipeline and portfolio talk to each other (a new API route,
a new GitHub Actions dispatch target, or anything that changes the
Supabase-as-bus contract). When in doubt, regenerate by re-reading this file,
`jobpipe/shared/status.py`, and the portfolio repo's `app/api`, `app/console`,
`app/lib`, and `middleware.ts`, then re-validate every Mermaid block with
`npx @mermaid-js/mermaid-cli` (or a headless render of the HTML pages) before
committing — a diagram that doesn't render is worse than no diagram.

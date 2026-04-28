"""interview_prep/ — STAR+R interview-prep accumulator (J-3).

`generator.py` runs a single LLM call per tailored job to emit 3-5
STAR+R stories. `bank.py` reads/writes/searches the `star_stories`
Supabase table. The orchestrator hooks into both as a side effect of
`process_approved_jobs` so interview prep accumulates for free as
applications go out.
"""

-- 017_resume_variant_company_type.sql — Callback feedback loop (P2, feat/callback-feedback-loop)
--
-- Consolidation of "The AI Hiring Machine" doc analysis (see
-- reviews/REVIEW_2026-07-13_hiring_machine_consolidation.md §2, item 1).
-- To learn which materials win, the pipeline needs two axes recorded on
-- the row at the time each material is chosen:
--
--   resume_variant — the LaTeX style/archetype lane actually rendered
--                     for this job (tailor/tailor/latex_resume.py::STYLES
--                     — "classic" | "modern" | "compact"). Written by
--                     pipeline.py's mark_ready_for_review call once
--                     generate_tailored_latex() has picked a style.
--   company_type   — a coarse taxonomy assigned by the hunt scorer at
--                     discovery time (jobpipe/hunt/prompts/scorer.md),
--                     the same one LLM call that already produces
--                     score/tier/legitimacy — no extra cost.
--
-- analyze_patterns.py (J-6) groups by these to surface "which resume
-- version wins where" per company_type.
--
-- Apply via the MCP `apply_migration` tool or Supabase Dashboard > SQL
-- Editor. Idempotent — both ALTERs are additive; NULL defaults so
-- pre-existing rows are simply "unclassified" until touched.

ALTER TABLE public.jobs
  ADD COLUMN IF NOT EXISTS resume_variant TEXT
    CHECK (resume_variant IS NULL OR resume_variant IN ('classic', 'modern', 'compact')),
  ADD COLUMN IF NOT EXISTS company_type TEXT
    CHECK (company_type IS NULL OR company_type IN (
      'frontier_lab', 'ai_startup', 'enterprise', 'consultancy', 'other'
    ));

CREATE INDEX IF NOT EXISTS idx_jobs_resume_variant ON public.jobs (resume_variant);
CREATE INDEX IF NOT EXISTS idx_jobs_company_type ON public.jobs (company_type);

-- Verify
SELECT column_name, data_type, column_default, is_nullable
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = 'jobs'
    AND column_name IN ('resume_variant', 'company_type');

SELECT indexname FROM pg_indexes
  WHERE schemaname = 'public' AND tablename = 'jobs'
    AND indexname IN ('idx_jobs_resume_variant', 'idx_jobs_company_type')
  ORDER BY indexname;

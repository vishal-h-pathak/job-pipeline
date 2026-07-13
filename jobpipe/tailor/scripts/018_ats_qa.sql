-- 018_ats_qa.sql — Post-tailor ATS keyword gate + humanizer (P2, feat/callback-feedback-loop)
--
-- Consolidation of "The AI Hiring Machine" doc analysis (see
-- reviews/REVIEW_2026-07-13_hiring_machine_consolidation.md §2, items 2
-- and 4). A new tailor QA step (jobpipe/tailor/tailor/ats_qa.py) runs one
-- Sonnet call over (JD, rendered resume text) after resume render and
-- before mark_ready_for_review, and writes its verdict here for the
-- cockpit to render next to the materials-review gate. Rewrites are
-- never auto-applied — the human reviews at the existing gate.
--
-- Shape (see ats_qa.py for the authoritative schema):
--   {
--     "top_keywords": [...15 ranked JD keywords...],
--     "missing": [...keywords absent from the resume...],
--     "ats_score": 0-100,
--     "highest_impact_fix": "one sentence",
--     "robotic_bullets": [{"bullet": "...", "humanized_rewrite": "..."}]
--   }
--
-- Apply via the MCP `apply_migration` tool or Supabase Dashboard > SQL
-- Editor. Idempotent — additive, NULL default so pre-existing rows are
-- simply un-QA'd until the tailor touches them.

ALTER TABLE public.jobs
  ADD COLUMN IF NOT EXISTS ats_qa JSONB;

-- Verify
SELECT column_name, data_type, is_nullable
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = 'jobs'
    AND column_name = 'ats_qa';

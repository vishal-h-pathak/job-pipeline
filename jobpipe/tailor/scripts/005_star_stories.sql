-- 005_star_stories.sql — STAR+R interview-prep accumulator (J-3)
--
-- Each tailoring run also generates 3-5 STAR+R stories grounded in
-- Vishal's profile + the JD. Stories accumulate across applications so
-- before any interview he can pull the 5-10 best master stories tagged
-- to the role's archetype + skills.
--
-- The +R is *Reflection*: what he'd do differently, what he learned —
-- the part that turns a STAR answer into a real one.

CREATE TABLE IF NOT EXISTS star_stories (
  id BIGSERIAL PRIMARY KEY,
  -- Job linkage. Nullable on purpose: orphan stories (e.g. hand-edited
  -- master stories) are allowed.
  job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
  archetype TEXT,
  company TEXT,
  role TEXT,
  -- The four-plus-one fields. All TEXT, no length cap — stories vary.
  situation TEXT NOT NULL,
  task TEXT NOT NULL,
  action TEXT NOT NULL,
  result TEXT NOT NULL,
  reflection TEXT NOT NULL,
  -- Tags drive /dashboard/stories filtering. Free-form array; the
  -- generator emits archetype + skill keywords here so they're
  -- searchable without parsing the body.
  tags TEXT[] DEFAULT '{}'::TEXT[],
  -- Marked when the user picks a story into the master 5-10 set.
  is_master BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_star_stories_archetype ON star_stories (archetype);
CREATE INDEX IF NOT EXISTS idx_star_stories_job_id ON star_stories (job_id);
CREATE INDEX IF NOT EXISTS idx_star_stories_is_master ON star_stories (is_master) WHERE is_master;
CREATE INDEX IF NOT EXISTS idx_star_stories_tags ON star_stories USING GIN (tags);

-- Verify
SELECT COUNT(*) AS story_count FROM star_stories;

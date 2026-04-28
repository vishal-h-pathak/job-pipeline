"""
Supabase Schema Migration for Job Applicant Pipeline
=====================================================
Adds status workflow columns to the jobs table.

Run once:
    cd ~/dev/jarvis/job-applicant
    python scripts/migrate_supabase.py

Requires: SUPABASE_URL and SUPABASE_KEY in ../.env or ../job-hunter/.env
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Try loading from job-hunter's .env first, then local
job_hunter_env = Path(__file__).parent.parent.parent / "job-hunter" / ".env"
local_env = Path(__file__).parent.parent / ".env"

if job_hunter_env.exists():
    load_dotenv(job_hunter_env)
    print(f"Loaded env from {job_hunter_env}")
elif local_env.exists():
    load_dotenv(local_env)
    print(f"Loaded env from {local_env}")
else:
    print("ERROR: No .env found. Create one with SUPABASE_URL and SUPABASE_KEY")
    sys.exit(1)

from supabase import create_client

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

if not url or not key:
    print("ERROR: SUPABASE_URL and SUPABASE_KEY must be set")
    sys.exit(1)

client = create_client(url, key)

# ── Step 1: Check current schema ────────────────────────────────────────────
print("\n=== Step 1: Checking current schema ===")
result = client.table("jobs").select("*").limit(1).execute()

if result.data:
    current_cols = sorted(result.data[0].keys())
    print(f"Current columns: {current_cols}")

    if "status" in current_cols:
        print("\n'status' column already exists. Migration may have already run.")
        response = input("Continue anyway? (y/n): ").strip().lower()
        if response != "y":
            print("Aborted.")
            sys.exit(0)
else:
    print("Table exists but is empty.")

# ── Step 2: Add new columns via RPC or direct SQL ────────────────────────────
# Note: Supabase anon key can't run raw SQL. We'll add columns by upserting
# a test row, or the user needs to run SQL in the Supabase dashboard.

MIGRATION_SQL = """
-- Job Applicant Pipeline: Schema Migration
-- Run this in Supabase Dashboard > SQL Editor

-- Add status workflow column
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'discovered';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMPTZ;

-- Application tracking columns
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS resume_path TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cover_letter_path TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS application_url TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS application_notes TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS failure_reason TEXT;

-- Backfill: mark disqualified jobs as rejected, everything else as discovered
UPDATE jobs SET status = 'rejected' WHERE action = 'disqualify' AND (status IS NULL OR status = 'discovered');
UPDATE jobs SET status = 'discovered' WHERE status IS NULL;

-- Index for the applicant agent's polling query
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);

-- Verify
SELECT status, COUNT(*) FROM jobs GROUP BY status;
"""

print("\n=== Step 2: Migration SQL ===")
print("The anon key can't run DDL statements directly.")
print("Please run the following SQL in your Supabase Dashboard > SQL Editor:\n")
print(MIGRATION_SQL)

# Save to file for convenience
sql_path = Path(__file__).parent / "migration.sql"
with open(sql_path, "w") as f:
    f.write(MIGRATION_SQL)
print(f"\nSQL also saved to: {sql_path}")

# ── Step 3: Verify after manual SQL run ──────────────────────────────────────
print("\n=== Step 3: Verification ===")
response = input("Have you run the SQL in Supabase dashboard? (y/n): ").strip().lower()

if response == "y":
    result = client.table("jobs").select("*").limit(1).execute()
    if result.data:
        new_cols = sorted(result.data[0].keys())
        print(f"Updated columns: {new_cols}")

        added = set(new_cols) - set(current_cols) if 'current_cols' in dir() else set()
        if added:
            print(f"New columns added: {added}")

        if "status" in new_cols:
            # Check status distribution
            all_jobs = client.table("jobs").select("status").execute()
            status_counts = {}
            for row in all_jobs.data:
                s = row.get("status", "NULL")
                status_counts[s] = status_counts.get(s, 0) + 1
            print(f"Status distribution: {status_counts}")
            print("\nMigration complete!")
        else:
            print("WARNING: 'status' column not found. SQL may not have run correctly.")
    else:
        print("Table is empty — migration should still be applied.")
else:
    print("Run the SQL when ready, then re-run this script to verify.")

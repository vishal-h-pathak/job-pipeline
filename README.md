# job-pipeline

Unified Python pipeline consolidating the previously-separate
`job-hunter`, `job-applicant`, and `job-submitter` repos into a single
codebase.

## Status

Mid-migration. The three sub-repos were merged here in PR-0a via
`git filter-repo --to-subdirectory-filter` followed by
`git merge --allow-unrelated-histories`, which preserves full commit
history per file (`git log --follow` works across the merge).

PR-0 (this commit) adds the top-level skeleton. PR-1..PR-10 reorganize
and consolidate the code; see the migration plan for details.

## Layout during migration

```
jobpipe/
├── hunt/    # was job-hunter   — sources, scorer, agent, profile
├── tailor/  # was job-applicant — form_answers, prompts, interview_prep
└── submit/  # was job-submitter — adapters, router, confirm
```

The post-migration target layout is documented in the migration plan.

## History walkback

`git log --follow` walks back into the original repos' history. Note
the file renames performed by PR-3 / PR-4 / PR-5; pass the post-rename
paths to walk through the merge:

```
git log --follow jobpipe/hunt/agent.py        # was jobpipe/hunt/job_agent.py (PR-3)
git log --follow jobpipe/tailor/pipeline.py   # was jobpipe/tailor/main.py    (PR-4)
git log --follow jobpipe/submit/runner.py     # was jobpipe/submit/main.py    (PR-5)
```

`git blame` attributes lines to their original pre-merge commits.

## Development

```
pip install -e '.[dev]'
pytest
```

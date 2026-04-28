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

`git log --follow` walks back into the original repos' history:

```
git log --follow jobpipe/hunt/job_agent.py
git log --follow jobpipe/tailor/main.py
git log --follow jobpipe/submit/main.py
```

`git blame` attributes lines to their original pre-merge commits.

## Development

```
pip install -e '.[dev]'
pytest
```

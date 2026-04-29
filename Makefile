# jobpipe Makefile — convenience targets for local + CI workflows.
#
# `make test` runs the pytest suite (the default ``-m 'not legacy'``
# filter in pyproject.toml excludes the retired Path-B forensic
# scaffold tests under tests/legacy/). The pre-consolidation
# import-wiring smoke harness was the principal user of the
# Browserbase + Stagehand path; with that path retired, the harness
# is parked at ``scripts/smoke_legacy.py`` and is exposed here as
# ``make legacy-smoke`` for forensics only — it is NOT a CI target.

PYTHON ?= python

.PHONY: test legacy-smoke help

help:
	@echo "Targets:"
	@echo "  test          Run the pytest suite (default-suite; legacy excluded)"
	@echo "  legacy-smoke  Run scripts/smoke_legacy.py — Path B forensics only"

test:
	@$(PYTHON) -m pytest -v

legacy-smoke:
	@$(PYTHON) scripts/smoke_legacy.py

# jobpipe Makefile — convenience targets for local + CI workflows.
#
# `make smoke` exercises the hunt → tailor → submit import surface and
# data flow without hitting Supabase/Browserbase/Anthropic. Designed to
# run in <60 s alongside (in parallel with) the unit-test job in CI.
#
# `make test` runs the pytest suite. CI runs both jobs in parallel; this
# target exists so a developer can run them sequentially with a single
# `make` invocation when desired (`make all`).

PYTHON ?= python

.PHONY: smoke test all help

help:
	@echo "Targets:"
	@echo "  smoke   Run scripts/smoke.py (hunt → tailor → submit imports + data flow)"
	@echo "  test    Run the pytest suite"
	@echo "  all     Run test then smoke"

smoke:
	@$(PYTHON) scripts/smoke.py

test:
	@$(PYTHON) -m pytest -v

all: test smoke

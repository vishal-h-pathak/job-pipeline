"""jobpipe.tailor — the resume / cover-letter / form-answer half of the pipeline.

Module wiring is unusual here: the modules in this package use unprefixed
imports (``from config import ...``, ``from db import ...``, ``from prompts
import ...``, ``from storage import ...``, ``from notify import ...``,
``from applicant.X import ...``, ``from tailor.Y import ...``) inherited
from when this code lived in its own repo at
``/Users/jarvis/dev/jarvis/job-applicant`` and ran with the tailor
directory as the working directory. ``jobpipe.tailor.pipeline`` bootstraps
``sys.path`` so those imports resolve when the package is loaded via the
``jobpipe-tailor`` console script as well as via the legacy
``python main.py`` invocation (now ``python pipeline.py``).

A future PR can rewrite the imports to be package-qualified
(``from jobpipe.tailor.config import X``) and remove the bootstrap. PR-4
deliberately kept the unprefixed style so the diff stayed scoped to the
moves described in the refactor plan, mirroring the pattern PR-3 / PR-5
established for ``jobpipe.hunt`` and ``jobpipe.submit``.
"""

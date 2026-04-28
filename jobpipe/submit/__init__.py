"""jobpipe.submit — the form-submission half of the pipeline.

Module wiring is unusual here: the modules in this package use unprefixed
imports (``import db``, ``from adapters.base import X``, ``import router``,
``from browser.session import Y``, ``from review_packet import build_packet``)
inherited from when this code lived in its own repo at
``/Users/jarvis/dev/jarvis/job-submitter`` and ran with the submit
directory as the working directory. ``jobpipe.submit.runner`` bootstraps
``sys.path`` so those imports resolve when the package is loaded via the
``jobpipe-submit`` console script as well as via the legacy
``python main.py`` invocation (now ``python runner.py``).

A future PR can rewrite the imports to be package-qualified
(``from jobpipe.submit.adapters.base import X``) and remove the bootstrap.
PR-5 deliberately kept the unprefixed style so the diff stayed scoped to
the moves described in the refactor plan, mirroring the pattern PR-3
established for ``jobpipe.hunt``.
"""

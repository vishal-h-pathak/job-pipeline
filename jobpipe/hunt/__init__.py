"""jobpipe.hunt — the job-discovery half of the pipeline.

Module wiring is unusual here: the modules in this package use unprefixed
imports (``from sources import X``, ``import config``,
``from utils.jobid import X``) inherited from when this code lived in its
own repo at ``/Users/jarvis/dev/jarvis/job-hunter`` and ran with the hunt
directory as the working directory. ``jobpipe.hunt.agent`` bootstraps
``sys.path`` so those imports resolve when the package is loaded via the
``jobpipe-hunt`` console script as well as via the legacy
``python -m agent`` invocation.

A future PR can rewrite the imports to be package-qualified
(``from jobpipe.hunt.sources import X``) and remove the bootstrap. PR-3
deliberately kept the unprefixed style so the diff stayed scoped to the
moves and helper extractions described in the refactor plan.
"""

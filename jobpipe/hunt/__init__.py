"""jobpipe.hunt — the job-discovery half of the pipeline.

Module wiring is unusual here: the modules in this package use unprefixed
imports (``from sources import X``, ``from sources._http import …``)
inherited from when this code lived in its own repo and ran with the
hunt directory as the working directory. ``jobpipe.hunt.agent``
bootstraps ``sys.path`` so those intra-subtree imports resolve when the
package is loaded via the ``jobpipe-hunt`` console script.

PR-9 rewrote the cross-cutting bare imports (``import config``,
``from db import …``, ``from notifier import …``, ``from utils.jobid
import …``) to package-qualified paths against the canonical
``jobpipe.config`` / ``jobpipe.db`` / ``jobpipe.notify`` /
``jobpipe.shared.*`` modules and removed the per-subtree shims they used
to resolve through. The bootstrap stays only for the remaining
intra-subtree bare imports above; a future PR can rewrite those to
``from jobpipe.hunt.sources import X`` and remove it entirely.
"""

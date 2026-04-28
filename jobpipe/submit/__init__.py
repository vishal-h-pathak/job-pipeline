"""jobpipe.submit — the form-submission half of the pipeline.

Module wiring is unusual here: the modules in this package use unprefixed
imports (``import router``, ``import storage``, ``import confirm``,
``from adapters.base import X``, ``from browser.session import Y``,
``from review_packet import build_packet``) inherited from when this
code lived in its own repo and ran with the submit directory as the
working directory. ``jobpipe.submit.runner`` bootstraps ``sys.path`` so
those intra-subtree imports resolve when the package is loaded via the
``jobpipe-submit`` console script.

PR-9 rewrote the cross-cutting bare imports (``import db``,
``from config import ...``) to package-qualified paths: runtime knobs
come from ``jobpipe.config`` directly, and submit-only fail-loud
secrets come from ``jobpipe.submit.config`` (whose shim re-export
plumbing was removed but whose ``require_env`` block was kept per the
PR-8 split-policy decision). The bootstrap stays only for the remaining
intra-subtree bare imports above; a future PR can rewrite those to
``from jobpipe.submit.adapters.base import X`` etc. and remove it
entirely.
"""

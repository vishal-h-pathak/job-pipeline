"""jobpipe.submit — the form-submission half of the pipeline.

Module wiring is unusual here: the modules in this package use unprefixed
imports (``import router``, ``import storage``, ``import confirm``,
``from adapters.base import X``, ``from browser.session import Y``,
``from review_packet import build_packet``) inherited from when this
code lived in its own repo and ran with the submit directory as the
working directory. ``jobpipe.submit.runner_legacy`` bootstraps
``sys.path`` so those intra-subtree imports resolve when that legacy
entry point is imported. PR-11 retired the original ``runner``;
PR-13 rebound ``jobpipe-submit`` to
``jobpipe.tailor.pipeline:run_submit_only``, which uses fully-
qualified imports and does not rely on this bootstrap.

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

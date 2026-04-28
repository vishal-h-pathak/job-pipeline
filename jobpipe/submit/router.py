"""
router.py — Dispatch a job to the right ATS adapter based on ats_kind.

Lookup table only. Any routing logic more interesting than "read the field
and pick an adapter" belongs in the adapter itself.
"""

from __future__ import annotations

import logging
from typing import Type

from adapters.base import Adapter, AtsKind

logger = logging.getLogger("submitter.router")


# Populated lazily to avoid import-time cycles and so missing-adapter errors
# surface with useful messages rather than crashing at module load.
_REGISTRY: dict[AtsKind, Type[Adapter]] = {}


def register(kind: AtsKind):
    """Decorator each adapter module uses to register itself."""
    def wrap(cls: Type[Adapter]) -> Type[Adapter]:
        _REGISTRY[kind] = cls
        return cls
    return wrap


def _import_adapters() -> None:
    """Trigger adapter module imports so their @register() decorators run."""
    # Imported for side effects. Missing modules are tolerated at this stage
    # of the rollout — Milestones 3–6 add them. PR-5 moved the deterministic
    # adapters under adapters/deterministic/; the generic Stagehand fallback
    # stays at adapters/generic_stagehand.py.
    for mod in (
        "adapters.deterministic.greenhouse",
        "adapters.deterministic.lever",
        "adapters.deterministic.ashby",
        "adapters.generic_stagehand",
    ):
        try:
            __import__(mod)
        except ImportError as exc:
            logger.debug("adapter %s not yet implemented (%s)", mod, exc)


def get_adapter(ats_kind: str) -> Adapter:
    """Return an adapter instance for the given ATS kind.

    Falls back to the generic Stagehand adapter for any kind not in the
    deterministic set. Raises LookupError if even the fallback isn't wired up
    (i.e., pre-Milestone-6).
    """
    if not _REGISTRY:
        _import_adapters()

    adapter_cls = _REGISTRY.get(ats_kind)  # type: ignore[arg-type]
    if adapter_cls is None:
        # Fall back to generic
        adapter_cls = _REGISTRY.get("generic")  # type: ignore[arg-type]
        if adapter_cls is None:
            raise LookupError(
                f"No adapter registered for ats_kind={ats_kind!r} and no "
                "generic fallback available yet."
            )
        logger.info("no adapter for %s — using generic fallback", ats_kind)

    return adapter_cls()

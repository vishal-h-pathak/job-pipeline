"""Deterministic ATS adapters (M-3): Greenhouse, Lever, Ashby.

Each adapter follows the survey -> fill -> score skeleton from
``adapters._common`` and uses zero LLM calls beyond the survey + the
custom-question classifier. Lookup happens via ``router.register``
decorators loaded by ``router._import_adapters`` at first ``get_adapter``
call.
"""

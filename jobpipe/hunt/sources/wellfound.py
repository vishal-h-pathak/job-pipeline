"""Wellfound (formerly AngelList Talent) source.

Wellfound does not expose a stable public API or RSS feed; scraping their
React-rendered job pages reliably requires a headless browser and is against
their ToS. This module is a stub that returns no jobs but preserves the
interface so the orchestrator can call it uniformly. Replace `fetch` with a
real implementation (e.g. via an authenticated API key, a scraping service,
or a third-party aggregator) when one becomes available.
"""


def fetch():
    return iter(())

"""tests/test_submit_config.py — PR-15 lazy Browserbase config.

Two contracts pinned:
  1. ``jobpipe.submit.config`` imports cleanly with ``BROWSERBASE_*``
     unset (the tailor-only Phase 3 path reaches submit/config
     transitively via tailor/url_resolver).
  2. The getters raise ``RuntimeError`` when called without the var
     set (the legacy submit path's loud-fail behavior, deferred from
     import time to first use).
"""
from __future__ import annotations

import importlib
import sys

import pytest


def _reload_submit_config(monkeypatch: pytest.MonkeyPatch):
    """Re-import submit.config with BROWSERBASE_* unset, others set."""
    for k, v in {
        "SUPABASE_URL": "https://x.example.supabase.co",
        "SUPABASE_KEY": "anon-test",
        "SUPABASE_SERVICE_ROLE_KEY": "service-test",
        "ANTHROPIC_API_KEY": "sk-test",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    monkeypatch.delenv("BROWSERBASE_PROJECT_ID", raising=False)
    # Defang load_dotenv so a developer's local jobpipe/submit/.env
    # doesn't repopulate the env vars we just cleared.
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: None)
    sys.modules.pop("jobpipe.submit.config", None)
    return importlib.import_module("jobpipe.submit.config")


def test_imports_without_browserbase_env(monkeypatch: pytest.MonkeyPatch):
    cfg = _reload_submit_config(monkeypatch)
    assert callable(cfg.get_browserbase_api_key)
    assert callable(cfg.get_browserbase_project_id)


def test_getters_raise_when_unset(monkeypatch: pytest.MonkeyPatch):
    cfg = _reload_submit_config(monkeypatch)
    with pytest.raises(RuntimeError, match="BROWSERBASE_API_KEY"):
        cfg.get_browserbase_api_key()
    with pytest.raises(RuntimeError, match="BROWSERBASE_PROJECT_ID"):
        cfg.get_browserbase_project_id()

"""
adapters/base.py — Contracts every ATS adapter implements.

The single most important rule: adapters FILL but do not SUBMIT. They produce
a SubmissionResult describing their readiness and evidence. confirm.py reads
the result and decides whether to click submit or route to needs_review.

This separation is the architectural lesson from the previous system, which
conflated "can I fill this field" with "should I send this application" and
made failures hard to localize.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

AtsKind = Literal[
    "greenhouse", "lever", "ashby",
    "workday", "icims", "smartrecruiters",
    "linkedin", "indeed", "generic",
]


@dataclass
class SubmissionContext:
    """Everything an adapter needs to drive a single submission attempt."""

    job: dict                              # jobs row from Supabase
    resume_pdf_path: Path                  # local tmp path, downloaded from Storage
    cover_letter_pdf_path: Path            # local tmp path
    cover_letter_text: str                 # plain text for form-paste fields
    application_url: str                   # resolved canonical ATS URL
    page: Any                              # Stagehand Page / BrowserSession handle
    attempt_n: int


@dataclass
class FieldFill:
    label: str
    value: str
    confidence: float                      # 0–1, adapter's certainty this was the right field
    kind: Literal["text", "select", "file", "checkbox", "radio", "textarea", "other"] = "text"


@dataclass
class FieldSkipped:
    label: str
    reason: str                            # e.g. "optional demographic", "no mapping", "rejected by server"


@dataclass
class Screenshot:
    label: str                             # e.g. "form_filled", "post_submit", "error_state"
    storage_path: str                      # Supabase Storage key


@dataclass
class SubmissionResult:
    """What the adapter hands back to confirm.py."""

    filled_fields: list[FieldFill] = field(default_factory=list)
    skipped_fields: list[FieldSkipped] = field(default_factory=list)
    screenshots: list[Screenshot] = field(default_factory=list)
    confidence: float = 0.0                # adapter's self-assessed readiness to submit
    recommend: Literal["auto_submit", "needs_review", "abort"] = "needs_review"
    recommend_reason: str = ""             # human-readable rationale
    adapter_name: str = ""                 # populated by router
    agent_reasoning: str | None = None     # only set by generic_stagehand adapter
    error: str | None = None               # set on abort


class Adapter(ABC):
    """Base class all ATS adapters implement."""

    ats_kind: AtsKind

    @abstractmethod
    async def run(self, ctx: SubmissionContext) -> SubmissionResult:
        """
        Navigate the application form and fill every field the adapter
        recognizes. Do NOT click the final submit button; that's confirm.py's
        job. Return a structured result.
        """
        ...

    @property
    def name(self) -> str:
        return self.ats_kind

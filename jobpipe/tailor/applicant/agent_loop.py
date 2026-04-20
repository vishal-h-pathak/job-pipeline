"""
applicant/agent_loop.py — Claude tool-use loop for driving a job application.

Given a BrowserSession and a job dict, this runs Claude in a loop with a
browser toolkit until the agent finishes, queues for review, or submits.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CANDIDATE_PROFILE_PATH
from applicant.browser_tools import BrowserSession

logger = logging.getLogger("applicant.agent_loop")

# Use the model from config (sonnet by default) — override via env if needed
SUBMITTER_MODEL = CLAUDE_MODEL


# ── Tool schemas exposed to the model ──────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "screenshot",
        "description": "Take a screenshot of the current browser viewport. Returns an image you can see.",
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Short label for the screenshot file (e.g., 'after_fill')."}
            },
            "required": [],
        },
    },
    {
        "name": "get_page_info",
        "description": "Return the current page URL, title, and viewport size.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_form_fields",
        "description": (
            "Enumerate all visible form fields and buttons on the page. Assigns each a stable "
            "id (field_1, field_2, ...). Use these ids with fill_field, upload_file, click, and "
            "click_submit. Call this whenever the page changes (after navigation or a click that "
            "reveals new fields)."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "fill_field",
        "description": (
            "Fill a text/textarea/select/checkbox/radio field. For checkboxes and radios, "
            "pass value='true' or value='yes' to check. For native selects, pass the option's "
            "value or visible label."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field_id": {"type": "string", "description": "The field id from get_form_fields (e.g., 'field_7')."},
                "value": {"type": "string", "description": "The value to fill."},
            },
            "required": ["field_id", "value"],
        },
    },
    {
        "name": "upload_file",
        "description": (
            "Upload the tailored resume or cover letter to a file-input field. "
            "file_kind must be 'resume' or 'cover_letter'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field_id": {"type": "string"},
                "file_kind": {"type": "string", "enum": ["resume", "cover_letter"]},
            },
            "required": ["field_id", "file_kind"],
        },
    },
    {
        "name": "click",
        "description": (
            "Click a button, link, or combobox by its field id. Use this for opening "
            "dropdowns, navigating multi-step forms, accepting cookies, etc. "
            "Do NOT use this to submit the final application — use click_submit instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"field_id": {"type": "string"}},
            "required": ["field_id"],
        },
    },
    {
        "name": "click_submit",
        "description": (
            "Click the FINAL submit-application button. Only works in submit mode. "
            "After clicking, waits up to 30s for a confirmation message. Returns submission status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"field_id": {"type": "string"}},
            "required": ["field_id"],
        },
    },
    {
        "name": "scroll",
        "description": "Scroll the page by a pixel amount.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"]},
                "amount": {"type": "number", "description": "Pixels. Default 400."},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "wait",
        "description": "Pause for N seconds (0.1 to 10) to let the page settle.",
        "input_schema": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
            "required": ["seconds"],
        },
    },
    {
        "name": "queue_for_review",
        "description": (
            "Stop now and queue this application for human review. Use this when you're not "
            "confident how to fill a required field, when the form is unusual, when uploads fail, "
            "or when you can't find the submit button. The human will resolve and re-queue."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Short explanation of why review is needed."},
                "uncertain_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Field ids or descriptions you're unsure about.",
                },
            },
            "required": ["reason"],
        },
    },
    {
        "name": "finish_preparation",
        "description": (
            "Prepare mode only. Call this when you've filled the form completely and it's ready "
            "for the human to review before submission. Do NOT call this if you stopped early "
            "due to uncertainty — use queue_for_review for that."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"notes": {"type": "string"}},
            "required": [],
        },
    },
]


# ── System prompts ─────────────────────────────────────────────────────────

_COMMON_RULES = """You are helping Vishal Pathak apply to a job. You control a real web browser via
tools. Your goal is to fill the application form accurately using the candidate profile, the
job description, the resume PDF, and the cover letter text provided below.

Core rules:
- Every answer you write into a field must be consistent with the candidate profile. Never
  invent experience, skills, or credentials he doesn't have.
- For freeform questions ("why this company?", "describe a time..."), write 2-4 sentences
  max in Vishal's voice: direct, technical, no "passionate"/"thrilled"/"leverage"/
  "excited to apply", no exclamation marks, narrative not bullet points.
- Standard personal fields: use the profile below (name, email, phone, location, LinkedIn).
- Upload the provided resume PDF to the Resume/CV field. If a Cover Letter upload is
  offered AND the site has a separate text field for it, prefer pasting the cover letter
  text into the text field. If only an upload is offered and you don't have a cover letter
  PDF on disk, paste the cover letter text into a text field if one exists; otherwise skip.
- Demographic / EEO / disability / veteran questions are ALWAYS optional. Select
  "I don't wish to answer" / "Decline to self-identify" / equivalent for each. If the
  form forces an answer, queue_for_review.
- If you're unsure about any REQUIRED field, call queue_for_review with the details.
- You must work methodically: take a screenshot, call get_form_fields, think, fill,
  repeat. Do not spam clicks.
"""


def _prepare_prompt(job: dict) -> str:
    return _COMMON_RULES + f"""
MODE: PREPARE

Your job is to navigate to the application URL (already open), fill every field you can
confidently fill, and then call finish_preparation. You must NOT call click_submit. A human
will review before the actual submission.

The browser is already open at the application URL. Start by taking a screenshot and
enumerating form fields.

TARGET JOB:
  Title: {job.get('title')}
  Company: {job.get('company')}
  Application URL (final ATS): {job.get('application_url') or job.get('url')}
"""


def _submit_prompt(job: dict) -> str:
    return _COMMON_RULES + f"""
MODE: SUBMIT

The human has approved this application. Your job is to RE-FILL the entire form (browser
sessions don't persist), verify everything is filled correctly, and then call click_submit
on the final submit button. After submit, a confirmation message will appear on the page.

Rules specific to submit mode:
- You may click_submit exactly once and only after you've filled every required field.
- If anything goes wrong during re-fill (field missing, upload fails), call queue_for_review
  immediately. Do not submit with an incomplete form.

The browser is already open at the application URL. Start by taking a screenshot and
enumerating form fields.

TARGET JOB:
  Title: {job.get('title')}
  Company: {job.get('company')}
  Application URL (final ATS): {job.get('application_url') or job.get('url')}
"""


# ── Agent loop ─────────────────────────────────────────────────────────────


def _load_profile() -> str:
    if CANDIDATE_PROFILE_PATH.exists():
        return CANDIDATE_PROFILE_PATH.read_text(encoding="utf-8")
    return "(CLAUDE.md not found — run from a repo where it exists.)"


def _load_voice_profile() -> str:
    voice_path = Path(__file__).parent.parent / "templates" / "VOICE_PROFILE.md"
    if voice_path.exists():
        return voice_path.read_text(encoding="utf-8")
    return ""


def _run_tool(session: BrowserSession, name: str, tool_input: dict):
    """Dispatch a tool call to the session. Returns (content_block, is_image)."""
    if name == "screenshot":
        path, data = session.tool_screenshot(label=tool_input.get("label", "state"))
        b64 = base64.b64encode(data).decode("ascii")
        return (
            [
                {"type": "text", "text": f"Screenshot saved to {path}. URL: {session.page.url}"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64},
                },
            ],
            True,
        )
    if name == "get_page_info":
        return (session.tool_get_page_info(), False)
    if name == "get_form_fields":
        return (session.tool_get_form_fields(), False)
    if name == "fill_field":
        return (session.tool_fill_field(tool_input["field_id"], tool_input["value"]), False)
    if name == "upload_file":
        return (session.tool_upload_file(tool_input["field_id"], tool_input["file_kind"]), False)
    if name == "click":
        return (session.tool_click(tool_input["field_id"]), False)
    if name == "click_submit":
        return (session.tool_click_submit(tool_input["field_id"]), False)
    if name == "scroll":
        return (
            session.tool_scroll(
                tool_input.get("direction", "down"), int(tool_input.get("amount", 400))
            ),
            False,
        )
    if name == "wait":
        return (session.tool_wait(float(tool_input.get("seconds", 1.0))), False)
    if name == "queue_for_review":
        return (
            session.tool_queue_for_review(
                tool_input.get("reason", "(no reason given)"),
                tool_input.get("uncertain_fields", []),
            ),
            False,
        )
    if name == "finish_preparation":
        return (session.tool_finish_preparation(tool_input.get("notes", "")), False)
    return (json.dumps({"ok": False, "error": f"unknown tool: {name}"}), False)


def run_submission_agent(
    session: BrowserSession,
    job: dict,
    cover_letter_text: str = "",
    max_turns: int = 40,
) -> dict:
    """
    Drive the agent until it calls finish_preparation, queue_for_review, or click_submit
    (or max_turns is hit).
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    profile = _load_profile()
    voice = _load_voice_profile()

    if session.mode == "submit":
        mode_instructions = _submit_prompt(job)
    else:
        mode_instructions = _prepare_prompt(job)

    system_prompt = f"""{mode_instructions}

========== CANDIDATE PROFILE (CLAUDE.md) ==========
{profile}

========== VOICE PROFILE (for any freeform answers) ==========
{voice}

========== JOB DESCRIPTION ==========
{job.get('description', '')[:5000]}

========== COVER LETTER TEXT (paste into a text field if the form has one; do not upload) ==========
{cover_letter_text or '(no cover letter text provided)'}
"""

    # The first user message kicks things off.
    messages: list = [
        {
            "role": "user",
            "content": (
                "Start by taking a screenshot, then enumerate form fields, then fill in order. "
                "When done, call finish_preparation (prepare mode) or click_submit (submit mode)."
            ),
        }
    ]

    for turn in range(max_turns):
        if session.finished:
            break
        try:
            response = client.messages.create(
                model=SUBMITTER_MODEL,
                max_tokens=2048,
                system=system_prompt,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
        except Exception as e:
            logger.error(f"Claude API error on turn {turn}: {e}")
            return {
                "success": False,
                "submitted": False,
                "needs_review": True,
                "review_reason": f"Claude API error: {e}",
                "screenshots": session.screenshots,
                "filled_fields": session.filled_fields,
            }

        # Append the assistant turn verbatim
        messages.append({"role": "assistant", "content": response.content})

        # Collect tool calls in this turn and run them
        tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            # Model stopped without calling a tool; treat as finished
            logger.info(f"turn {turn}: no tool use, stop_reason={response.stop_reason}")
            break

        tool_results_content = []
        for tu in tool_uses:
            logger.info(f"turn {turn}: tool={tu.name} input={json.dumps(tu.input)[:200]}")
            result_content, _is_image = _run_tool(session, tu.name, tu.input or {})
            if isinstance(result_content, list):
                # Screenshot returned list of blocks
                tool_results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result_content,
                })
            else:
                tool_results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result_content,
                })
            # If the tool flipped a terminal state, stop after this batch
            if session.finished:
                break

        messages.append({"role": "user", "content": tool_results_content})

        if response.stop_reason == "end_turn" and not tool_uses:
            break

    # Final state
    result = {
        "success": session.finished and not session.needs_review,
        "submitted": session.submitted,
        "needs_review": session.needs_review,
        "review_reason": session.review_reason,
        "uncertain_fields": session.review_uncertain,
        "submit_confirmation_text": session.submit_confirmation_text,
        "screenshots": session.screenshots,
        "filled_fields": session.filled_fields,
        "turns_used": turn + 1 if session.finished else max_turns,
        "final_url": session.page.url,
    }
    return result

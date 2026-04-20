"""
applicant/browser_tools.py — Playwright-backed tools exposed to the Claude agent.

The Claude agent drives a job application form by calling these tools. Each tool
returns a string (for tool_result); screenshot also returns image bytes inline.

Design:
- A BrowserSession wraps a Playwright Page. It's mode-aware (prepare/submit)
  so click_submit() is only permitted in submit mode.
- Every form field is assigned a stable id via an injected JS snippet on each
  get_form_fields() call, so field_1, field_2... remain stable within a turn.
- Labels are extracted in priority order: aria-label → <label for> →
  aria-labelledby → placeholder → nearest preceding text.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from config import OUTPUT_DIR

logger = logging.getLogger("applicant.browser_tools")


# ── JS snippet to enumerate + tag form fields ──────────────────────────────

_ENUMERATE_JS = r"""
(() => {
    const out = [];
    const seen = new Set();
    let counter = 0;

    const getLabel = (el) => {
        // 1. aria-label
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();

        // 2. <label for="id">
        if (el.id) {
            const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (lbl) return lbl.textContent.trim();
        }

        // 3. parent <label>
        const parentLabel = el.closest('label');
        if (parentLabel) {
            const text = parentLabel.textContent.trim();
            if (text) return text;
        }

        // 4. aria-labelledby
        const lbBy = el.getAttribute('aria-labelledby');
        if (lbBy) {
            const refs = lbBy.split(/\s+/).map(id => document.getElementById(id));
            const text = refs.filter(r => r).map(r => r.textContent.trim()).join(' ').trim();
            if (text) return text;
        }

        // 5. placeholder
        if (el.placeholder) return el.placeholder.trim();

        // 6. preceding heading/legend/span within 200px
        let walker = el.previousElementSibling;
        let hops = 0;
        while (walker && hops < 5) {
            const t = (walker.textContent || '').trim();
            if (t && t.length < 200 && t.length > 2) return t;
            walker = walker.previousElementSibling;
            hops++;
        }

        // 7. name attribute
        return el.name || el.id || el.tagName.toLowerCase();
    };

    const isVisible = (el) => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        const s = window.getComputedStyle(el);
        if (s.visibility === 'hidden' || s.display === 'none') return false;
        return true;
    };

    // Inputs, textareas
    document.querySelectorAll('input, textarea').forEach(el => {
        if (!isVisible(el) && el.type !== 'file') return;
        if (seen.has(el)) return;
        seen.add(el);
        counter++;
        const id = `field_${counter}`;
        el.setAttribute('data-ja-id', id);
        out.push({
            id,
            type: el.type || 'text',
            tag: el.tagName.toLowerCase(),
            label: getLabel(el),
            required: el.required || el.getAttribute('aria-required') === 'true',
            current_value: el.type === 'file'
                ? (el.files && el.files.length ? el.files[0].name : '')
                : (el.value || ''),
            checked: (el.type === 'checkbox' || el.type === 'radio') ? !!el.checked : null,
            name: el.name || null,
        });
    });

    // Native selects
    document.querySelectorAll('select').forEach(el => {
        if (!isVisible(el)) return;
        if (seen.has(el)) return;
        seen.add(el);
        counter++;
        const id = `field_${counter}`;
        el.setAttribute('data-ja-id', id);
        const options = Array.from(el.options).slice(0, 80).map(o => ({
            value: o.value, label: o.textContent.trim()
        }));
        out.push({
            id,
            type: 'select',
            tag: 'select',
            label: getLabel(el),
            required: el.required || el.getAttribute('aria-required') === 'true',
            current_value: el.value || '',
            options,
            name: el.name || null,
        });
    });

    // Custom comboboxes / selects (role="combobox", role="listbox", data-react-select, etc.)
    const comboSel =
        '[role="combobox"], [role="listbox"], [data-react-select-container="true"], ' +
        '.select__control, .select2-container, [class*="Select-control"]';
    document.querySelectorAll(comboSel).forEach(el => {
        if (!isVisible(el)) return;
        if (seen.has(el)) return;
        seen.add(el);
        counter++;
        const id = `field_${counter}`;
        el.setAttribute('data-ja-id', id);
        out.push({
            id,
            type: 'combobox',
            tag: el.tagName.toLowerCase(),
            label: getLabel(el),
            required: el.getAttribute('aria-required') === 'true',
            current_value: (el.textContent || '').trim().slice(0, 120),
            name: el.getAttribute('data-name') || null,
        });
    });

    // Buttons and links (so the agent can click Apply / Continue / Submit)
    document.querySelectorAll('button, a[role="button"], [type="submit"]').forEach(el => {
        if (!isVisible(el)) return;
        if (seen.has(el)) return;
        seen.add(el);
        counter++;
        const id = `field_${counter}`;
        el.setAttribute('data-ja-id', id);
        out.push({
            id,
            type: 'button',
            tag: el.tagName.toLowerCase(),
            label: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 120),
            is_submit: el.type === 'submit'
                || (el.textContent || '').toLowerCase().includes('submit application')
                || (el.textContent || '').toLowerCase() === 'submit',
            href: el.tagName === 'A' ? el.href : null,
        });
    });

    return out;
})();
"""


@dataclass
class BrowserSession:
    page: Page
    mode: str = "prepare"  # "prepare" or "submit"
    resume_path: Optional[str] = None
    cover_letter_path: Optional[str] = None   # may be a file path OR inline text in caller
    cover_letter_text: Optional[str] = None
    job_slug: str = "unknown"

    # Agent-triggered stop state
    needs_review: bool = False
    review_reason: Optional[str] = None
    review_uncertain: list = field(default_factory=list)
    finished: bool = False
    submitted: bool = False
    submit_confirmation_text: Optional[str] = None

    # Logs for debugging
    filled_fields: dict = field(default_factory=dict)
    screenshots: list = field(default_factory=list)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _ts(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _find_element(self, field_id: str):
        loc = self.page.locator(f'[data-ja-id="{field_id}"]')
        return loc

    # ── Tools (each returns a str for tool_result) ─────────────────────────

    def tool_screenshot(self, label: str = "state") -> tuple[str, bytes]:
        """Take a full-viewport screenshot. Returns (path, bytes)."""
        path = OUTPUT_DIR / f"apply_{self.job_slug}_{label}_{self._ts()}.png"
        data = self.page.screenshot(path=str(path), full_page=False)
        self.screenshots.append(str(path))
        logger.info(f"screenshot: {path}")
        return str(path), data

    def tool_get_page_info(self) -> str:
        return json.dumps({
            "url": self.page.url,
            "title": self.page.title(),
            "viewport": self.page.viewport_size,
        })

    def tool_get_form_fields(self) -> str:
        """Return a JSON list of all visible form fields and buttons with stable ids."""
        try:
            fields = self.page.evaluate(_ENUMERATE_JS)
        except Exception as e:
            return json.dumps({"error": f"JS eval failed: {e}"})
        return json.dumps({"count": len(fields), "fields": fields}, indent=2)

    def tool_fill_field(self, field_id: str, value: str) -> str:
        try:
            el = self._find_element(field_id)
            el.wait_for(state="visible", timeout=5000)
            tag = (el.evaluate("el => el.tagName.toLowerCase()"))
            el_type = (el.evaluate("el => el.type || ''"))

            if tag == "select":
                # Try by value, fall back to by label
                try:
                    el.select_option(value=value)
                except Exception:
                    el.select_option(label=value)
            elif el_type == "checkbox":
                want = value.lower() in ("true", "1", "yes", "on", "checked")
                is_checked = el.is_checked()
                if want != is_checked:
                    el.click()
            elif el_type == "radio":
                el.check()
            else:
                # text / textarea
                el.click()
                el.fill("")  # clear
                el.fill(value)
            self.filled_fields[field_id] = value
            return json.dumps({"ok": True, "id": field_id})
        except Exception as e:
            return json.dumps({"ok": False, "id": field_id, "error": str(e)})

    def tool_upload_file(self, field_id: str, file_kind: str) -> str:
        """file_kind ∈ {resume, cover_letter}"""
        if file_kind == "resume":
            path = self.resume_path
        elif file_kind == "cover_letter":
            path = self.cover_letter_path
        else:
            return json.dumps({"ok": False, "error": f"unknown file_kind={file_kind}"})

        if not path or not Path(path).exists():
            return json.dumps({"ok": False, "error": f"{file_kind} file not available"})

        try:
            el = self._find_element(field_id)
            el.set_input_files(path)
            self.filled_fields[field_id] = f"<uploaded {file_kind}: {Path(path).name}>"
            return json.dumps({"ok": True, "id": field_id, "uploaded": str(path)})
        except Exception as e:
            return json.dumps({"ok": False, "id": field_id, "error": str(e)})

    def tool_click(self, field_id: str) -> str:
        try:
            el = self._find_element(field_id)
            el.scroll_into_view_if_needed(timeout=3000)
            el.click(timeout=5000)
            return json.dumps({"ok": True, "id": field_id})
        except Exception as e:
            return json.dumps({"ok": False, "id": field_id, "error": str(e)})

    def tool_click_submit(self, field_id: str) -> str:
        """Click the final submit button. ONLY allowed in submit mode."""
        if self.mode != "submit":
            return json.dumps({
                "ok": False,
                "error": "click_submit is only available in submit mode; "
                         "call finish_preparation in prepare mode instead.",
            })
        click_res = self.tool_click(field_id)
        if '"ok": false' in click_res:
            return click_res

        # Poll for confirmation for up to 30s
        patterns = [
            "application submitted",
            "thanks for applying",
            "thank you for applying",
            "we've received your application",
            "we have received your application",
            "your application has been submitted",
            "application has been received",
            "submission successful",
        ]
        deadline = time.time() + 30
        confirmation = None
        while time.time() < deadline:
            try:
                body = (self.page.inner_text("body") or "").lower()
                for p in patterns:
                    if p in body:
                        confirmation = p
                        break
                if confirmation:
                    break
            except Exception:
                pass
            time.sleep(1)

        self.submitted = bool(confirmation)
        self.submit_confirmation_text = confirmation
        self.finished = True
        return json.dumps({
            "ok": True,
            "submitted": self.submitted,
            "confirmation_match": confirmation,
            "current_url": self.page.url,
        })

    def tool_queue_for_review(self, reason: str, uncertain_fields: list = None) -> str:
        self.needs_review = True
        self.review_reason = reason
        self.review_uncertain = uncertain_fields or []
        self.finished = True
        # Screenshot the current state so the human has context
        try:
            path, _ = self.tool_screenshot(label="needs_review")
        except Exception:
            path = None
        return json.dumps({
            "ok": True,
            "queued_for_review": True,
            "reason": reason,
            "uncertain_fields": self.review_uncertain,
            "screenshot": path,
        })

    def tool_finish_preparation(self, notes: str = "") -> str:
        if self.mode != "prepare":
            return json.dumps({
                "ok": False,
                "error": "finish_preparation is only for prepare mode; in submit mode use click_submit.",
            })
        try:
            path, _ = self.tool_screenshot(label="prepared")
        except Exception:
            path = None
        self.finished = True
        return json.dumps({
            "ok": True,
            "mode": "prepare",
            "screenshot": path,
            "notes": notes,
            "filled_fields_count": len(self.filled_fields),
        })

    def tool_scroll(self, direction: str = "down", amount: int = 400) -> str:
        dy = amount if direction == "down" else -amount
        self.page.mouse.wheel(0, dy)
        time.sleep(0.3)
        return json.dumps({"ok": True, "direction": direction, "amount": amount})

    def tool_wait(self, seconds: float = 1.0) -> str:
        seconds = min(max(seconds, 0.1), 10)
        time.sleep(seconds)
        return json.dumps({"ok": True, "waited": seconds})

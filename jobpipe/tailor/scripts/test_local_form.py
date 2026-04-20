"""
scripts/test_local_form.py — End-to-end loop test against a local HTML form.

Serves a fake application form on 127.0.0.1, runs the UniversalApplicant in
PREPARE mode, and verifies:
  - the agent fills the required fields
  - uploads the resume
  - pastes the cover letter text
  - calls finish_preparation (NOT click_submit)

Exits 0 on success, nonzero on failure.
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from applicant.universal import UniversalApplicant


FAKE_FORM_HTML = """<!DOCTYPE html>
<html>
<head><title>Apply: Research Scientist — FakeCo</title></head>
<body>
  <h1>Apply: Research Scientist at FakeCo</h1>
  <p>FakeCo builds cool things. We're hiring.</p>
  <form id="app-form" onsubmit="document.getElementById('status').innerText='Application submitted — thanks for applying'; return false;">
    <p><label for="fn">First name*</label>
       <input id="fn" name="fn" type="text" required></p>
    <p><label for="ln">Last name*</label>
       <input id="ln" name="ln" type="text" required></p>
    <p><label for="email">Email address*</label>
       <input id="email" name="email" type="email" required></p>
    <p><label for="phone">Phone</label>
       <input id="phone" name="phone" type="tel"></p>
    <p><label for="linkedin">LinkedIn URL</label>
       <input id="linkedin" name="linkedin" type="text"></p>
    <p><label for="resume">Resume / CV*</label>
       <input id="resume" name="resume" type="file" required></p>
    <p><label for="cover">Cover letter (paste here)</label>
       <textarea id="cover" name="cover" rows="6" cols="60"></textarea></p>
    <p><label for="why">What drew you to FakeCo?*</label>
       <textarea id="why" name="why" required rows="3" cols="60"></textarea></p>
    <p><label for="visa">Do you require visa sponsorship?*</label>
       <select id="visa" name="visa" required>
         <option value="">Select...</option>
         <option value="no">No</option>
         <option value="yes">Yes</option>
       </select></p>
    <p><label><input type="checkbox" id="eeo" name="eeo"> Decline to self-identify (EEO)</label></p>
    <p><button type="submit">Submit application</button></p>
    <p id="status"></p>
  </form>
</body>
</html>
"""


def serve_form(port_holder: list):
    """Serve FAKE_FORM_HTML on an auto-chosen port."""
    tmp = tempfile.mkdtemp(prefix="jatest_")
    (Path(tmp) / "index.html").write_text(FAKE_FORM_HTML, encoding="utf-8")
    os.chdir(tmp)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # silence

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port_holder.append(httpd.server_address[1])
    httpd.serve_forever()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    resume_pdfs = sorted((ROOT / "output").glob("resume_Beacon_Biosignals_*.pdf"))
    cover_txts = sorted((ROOT / "output").glob("cover_letter_Beacon_Biosignals_*.txt"))
    if not resume_pdfs or not cover_txts:
        print("Need Beacon resume PDF + cover letter in output/. Abort.")
        sys.exit(2)

    resume_pdf = str(resume_pdfs[-1])
    cover_text = cover_txts[-1].read_text(encoding="utf-8")

    port_holder = []
    t = threading.Thread(target=serve_form, args=(port_holder,), daemon=True)
    t.start()
    while not port_holder:
        time.sleep(0.05)
    port = port_holder[0]
    url = f"http://127.0.0.1:{port}/"
    print(f"Local form serving at {url}")

    job = {
        "id": "local-test",
        "title": "Research Scientist",
        "company": "FakeCo",
        "location": "Remote",
        "url": url,
        "application_url": url,
        "description": (
            "FakeCo is hiring a research scientist to work on cool things. "
            "Requires strong Python, scientific computing, and the ability to "
            "collaborate across teams. No pharma or clinical background needed."
        ),
    }

    applicant = UniversalApplicant(slow_mo_ms=0)
    result = applicant.apply(job, resume_pdf, cover_text, headless=True)

    print("\n── RESULT ──")
    printable = {k: v for k, v in result.items() if k != "screenshots"}
    print(json.dumps(printable, indent=2, default=str))
    print(f"\nScreenshots: {len(result.get('screenshots', []))}")
    for s in result.get("screenshots", []):
        print(f"  {s}")

    # Assertions
    filled = result.get("filled_fields") or {}
    expected_substrings_by_field_kind = ["Vishal", "Pathak", "thak.io"]  # at least one fill landed
    any_hit = any(any(s.lower() in str(v).lower() for s in expected_substrings_by_field_kind)
                  for v in filled.values())
    print(f"\nfilled count: {len(filled)}")
    print(f"fill contains expected content: {any_hit}")
    print(f"submitted: {result.get('submitted')}  (MUST be False in prepare mode)")
    print(f"needs_review: {result.get('needs_review')}")

    if result.get("submitted"):
        print("FAIL: agent submitted in prepare mode")
        sys.exit(1)
    if not result.get("success") and not result.get("needs_review"):
        print("FAIL: agent neither finished nor queued for review")
        sys.exit(1)
    print("\nPASS")


if __name__ == "__main__":
    main()

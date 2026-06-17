"""
tailor/latex_resume.py — Generate tailored LaTeX resumes compiled to PDF.

Takes the output of tailor_resume() and produces a LaTeX document matching
Vishal's existing resume style (Comp Neuroscience variant), then compiles to PDF.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from jobpipe.config import TAILOR_CLAUDE_MODEL as CLAUDE_MODEL
from jobpipe.profile_loader import load_resume_source
from jobpipe.shared import llm
from jobpipe.tailor.paths import CANDIDATE_PROFILE_PATH
from prompts import cached_system_blocks, load_task_prompt
from tailor.archetype import classify_archetype, render_archetype_block
from tailor.normalize import normalize_for_ats

logger = logging.getLogger("tailor.latex_resume")

# ── Base resume data (source of truth — never fabricate beyond this) ──────
#
# The professional experience (the resume's spine) now lives in the canonical
# source ``profile/resume_source.yml`` (transcribed from
# RESUME_CANONICAL_SOURCE.md), loaded via ``load_resume_source()`` and merged
# with the identity/education/skills constants below into ``BASE_RESUME``.
# Personal projects are NOT part of BASE_RESUME — they are surfaced
# conditionally by archetype (see ``projects_for_archetype``), so the resume is
# professional-first and the old generic "Personal Projects" block is gone.
#
# Identity / education / skills are not part of the canonical content doc, so
# they stay here as constants.

_IDENTITY = {
    "name": "Vishal Pathak",
    "email": "vishalp@thak.io",
    "location": "Atlanta, GA",
    "linkedin": "linkedin.com/in/vishalhpathak",
    "website": "vishal.pa.thak.io",
}

_EDUCATION = {
    "school": "Florida Institute of Technology",
    "degree": "B.S. Electrical Engineering, cum laude",
    "period": "2019--2021",
}

_SKILLS = {
    "Neuromorphic & Simulation": "Intel LavaSDK, NxSDK, Brian2, MuJoCo, Gymnasium API, FlyGym, VHDL, RTL design, AFSIM surrogate modeling",
    "Programming & ML": "Python, C/C++, PyTorch, TensorFlow, NumPy, Matplotlib, scikit-learn, PyQt6",
    "Systems & Hardware": "FPGA development, embedded systems (STM32), PCB design (EAGLE/Altium), serial protocols (RS-232/RS-485), ruggedized sensor deployment, HPC clusters",
    "Tools & Platforms": "Git, CI/CD (Jacamar-CI), pytest, Docker, Linux, MATLAB, LabVIEW",
}


# ── Resume styles (PR: one-page guarantee + a few ATS-safe styles) ─────────
#
# Every style MUST stay ATS-parseable: single-column body, standard fonts,
# selectable text, NO images/icons/graphics, NO multi-column body, NO color
# blocks behind text. "Style" = typography / section-heading / spacing /
# subtle-rule variation only. The shared base below carries the structure
# (header → education/skills → experience) and the defensive special-char
# macros; per-style tokens (%%FONT%%, %%SECTION%%, %%TITLESPACE%%, %%LIST%%,
# %%PTSIZE%%, %%MARGIN%%) get filled by _apply_style so the rendered string
# for each style still uses the identical <<...>> body placeholders.

_BASE_TEMPLATE = r"""
\documentclass[%%PTSIZE%%, letterpaper]{article}

\usepackage[T1]{fontenc}
\usepackage[margin=%%MARGIN%%]{geometry}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage{hyperref}
\usepackage{xcolor}
\usepackage{textcomp}
%%FONT%%
% Defensive macros for special-character commands the LLM occasionally
% emits instead of the unicode literals it sees in BASE_RESUME (e.g.
% rewriting "360°" as "360\degree"). _escape_latex_safe deliberately
% lets backslash sequences pass through (since the template uses many
% legitimately), so an undefined macro would otherwise crash compile.
% \providecommand only defines if not already defined, so this is safe
% even when a future package upgrade ships its own \degree.
\providecommand{\degree}{\ensuremath{^\circ}}
\providecommand{\micro}{\ensuremath{\mu}}
\providecommand{\celsius}{\ensuremath{^\circ}C}
\providecommand{\ohm}{\ensuremath{\Omega}}

% ── Formatting ─────────────────────────────────────────────────────────────
\pagestyle{empty}
\setlength{\parindent}{0pt}
\definecolor{linkblue}{HTML}{2563EB}

\hypersetup{
    colorlinks=true,
    urlcolor=linkblue,
    linkcolor=linkblue,
}

%%SECTION%%
%%TITLESPACE%%

%%LIST%%

\begin{document}

% ── Header ─────────────────────────────────────────────────────────────────
\begin{center}
{\LARGE \textbf{<<NAME>>}} \\[4pt]
\small <<EMAIL>> $\cdot$ <<LOCATION>> $\cdot$ \href{https://<<LINKEDIN>>}{<<LINKEDIN>>} $\cdot$ \href{https://<<WEBSITE>>}{<<WEBSITE>>}
\end{center}

% ── Education & Skills ─────────────────────────────────────────────────────
\section{Education \& Technical Skills}

<<EDU_AND_SKILLS>>

% ── Experience ─────────────────────────────────────────────────────────────
\section{Experience}

<<EXPERIENCE_BLOCKS>>

% ── Projects (conditional — rendered only when the archetype provides a
% non-empty project bank; otherwise this placeholder collapses to nothing,
% so tier_2 / tier_3 resumes carry no empty header) ─────────────────────────
<<PROJECTS_SECTION>>

\end{document}
"""


def _apply_style(
    *, ptsize: str, margin: str, font: str,
    section: str, titlespace: str, listspace: str,
) -> str:
    """Fill the per-style tokens in _BASE_TEMPLATE, leaving the <<...>> body
    placeholders for _render_latex."""
    out = _BASE_TEMPLATE
    out = out.replace("%%PTSIZE%%", ptsize)
    out = out.replace("%%MARGIN%%", margin)
    out = out.replace("%%FONT%%", font)
    out = out.replace("%%SECTION%%", section)
    out = out.replace("%%TITLESPACE%%", titlespace)
    out = out.replace("%%LIST%%", listspace)
    return out


# Section heading format shared by the serif styles (classic + compact):
# bold, link-blue, hairline rule under the title.
_SERIF_SECTION = (
    r"\titleformat{\section}{\large\bfseries\color{linkblue}}{}{0em}{}[\titlerule]"
)

STYLES: dict[str, str] = {
    # classic — serif headings, hairline rule, link-blue titles (the
    # original look). Default for the tier-1 neuro lanes + mission ML.
    "classic": _apply_style(
        ptsize="11pt", margin="0.5in", font="",
        section=_SERIF_SECTION,
        titlespace=r"\titlespacing*{\section}{0pt}{12pt}{6pt}",
        listspace=r"\setlist[itemize]{leftmargin=1.2em, itemsep=2pt, parsep=0pt, topsep=2pt}",
    ),
    # modern — clean sans, bold section titles, a touch more whitespace.
    # Used for the agentic-builder + AI-SE lanes. We use Latin Modern Sans
    # (lmodern, in texlive-latex-recommended) rather than helvet/Helvetica:
    # the URW Helvetica TFMs aren't in every texlive set (e.g. the basic
    # local install), and a missing-font compile failure would flip the row
    # to needs_review. lmodern ships with full T1 coverage, is ATS-safe
    # (standard selectable text), and renders a clean professional sans.
    "modern": _apply_style(
        ptsize="11pt", margin="0.5in",
        font="\\usepackage{lmodern}\n\\renewcommand\\familydefault{\\sfdefault}",
        section=_SERIF_SECTION,
        titlespace=r"\titlespacing*{\section}{0pt}{14pt}{8pt}",
        listspace=r"\setlist[itemize]{leftmargin=1.2em, itemsep=3pt, parsep=0pt, topsep=3pt}",
    ),
    # compact — serif, tighter spacing + smaller section gaps for
    # content-dense roles. Same single-column, selectable-text body.
    "compact": _apply_style(
        ptsize="11pt", margin="0.45in", font="",
        section=_SERIF_SECTION,
        titlespace=r"\titlespacing*{\section}{0pt}{8pt}{3pt}",
        listspace=r"\setlist[itemize]{leftmargin=1.1em, itemsep=1pt, parsep=0pt, topsep=1pt}",
    ),
}

# Backwards-compat alias for the retired single template.
LATEX_TEMPLATE = STYLES["classic"]


# Deterministic archetype → style map. A role always gets a fitting style.
# (For per-run variety instead, round-robin on a hash of the job id is a
# one-line swap — e.g. ``list(STYLES)[hash(job_id) % len(STYLES)]``.)
_STYLE_BY_ARCHETYPE: dict[str, str] = {
    "tier_1a_compneuro": "classic",
    "tier_1b_neuromorphic": "classic",
    "tier_1c_bci": "classic",
    "tier_3_mission_ml": "classic",
    "tier_1_5_agentic_builder": "modern",
    "tier_2_ai_se": "modern",
}


def _select_style(archetype_key: str) -> str:
    """Pick an ATS-safe style for a job's archetype; classic is the fallback."""
    return _STYLE_BY_ARCHETYPE.get((archetype_key or "").strip(), "classic")


# Characters that pdflatex treats as macro/special. Each must be escaped when
# it appears in body text generated by the LLM. Backslash and curly braces
# are intentionally NOT in this list — the rendered template already uses
# them deliberately, so escaping here would break the template.
_LATEX_UNSAFE = ("#", "%", "&", "_", "$", "~", "^")
_LATEX_REPL = {
    "#": r"\#",
    "%": r"\%",
    "&": r"\&",
    "_": r"\_",
    "$": r"\$",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _escape_latex_safe(text: str) -> str:
    """Escape LaTeX-unsafe characters in LLM-generated text.

    The previous implementation short-circuited on any backslash, so any
    bullet that already used ``\\&`` or math mode bailed before escaping
    later ``#``/``%``/``_``. This version walks the string and skips:

    - already-escaped pairs (``\\X``)
    - math-mode segments (``$...$``)

    Everything else gets the special-character substitutions from
    ``_LATEX_REPL``. Before any of that, we run ``normalize_for_ats`` so
    LLM-introduced em-dashes / smart quotes don't survive into the PDF.
    """
    if not text:
        return ""
    text = normalize_for_ats(text)
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            # Already-escaped sequence (\\&, \\#, \\textit, etc.) — pass through.
            out.append(text[i:i + 2])
            i += 2
            continue
        if ch == "$":
            # Math mode — copy through to the matching $ untouched. If there's
            # no closing $, fall back to escaping the remaining body so we
            # don't drop content.
            close = text.find("$", i + 1)
            if close == -1:
                out.append(_LATEX_REPL["$"])
                i += 1
                continue
            out.append(text[i:close + 1])
            i = close + 1
            continue
        if ch in _LATEX_UNSAFE:
            out.append(_LATEX_REPL[ch])
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# Backwards-compat alias for callers expecting the old name.
_escape_latex = _escape_latex_safe


# ── BASE_RESUME assembly from the canonical source ─────────────────────────


def _build_base_resume() -> dict:
    """Assemble ``BASE_RESUME`` from ``profile/resume_source.yml``.

    Merges the identity/education/skills constants with the canonical
    ``professional_experience`` (the resume's spine). GTRI's ordered
    ``programs`` become the employer's resume ``projects`` (the renderer's
    term), Rain follows. Personal projects are intentionally NOT included —
    they're surfaced conditionally via :func:`projects_for_archetype`.

    Employer-level ``org`` / ``title`` / ``location`` / ``period`` are
    LaTeX-escaped at build time because the renderer inserts them verbatim
    (so ``Algorithms & Analysis Engineer`` becomes ``Algorithms \\& ...``,
    matching the hand-escaped form the old hardcoded BASE_RESUME carried).
    Program names + bullets stay raw — the renderer escapes those itself.
    """
    src = load_resume_source()
    experience: list[dict] = []
    for org in src.get("professional_experience", []) or []:
        programs = [
            {
                "name": prog.get("name"),
                "period": prog.get("period"),
                "bullets": list(prog.get("bullets") or []),
            }
            for prog in (org.get("programs") or [])
        ]
        experience.append(
            {
                "org": _escape_latex_safe(org.get("org") or ""),
                "title": _escape_latex_safe(org.get("title") or ""),
                "location": _escape_latex_safe(org.get("location") or ""),
                "period": _escape_latex_safe(org.get("period") or ""),
                "projects": programs,
            }
        )
    return {
        **_IDENTITY,
        "education": _EDUCATION,
        "skills": _SKILLS,
        "experience": experience,
    }


# Module-level source of truth for the prompt + renderer. Other modules
# (e.g. scripts/cv_sync_check.py) import this name.
BASE_RESUME = _build_base_resume()


def projects_for_archetype(archetype_key: str) -> list[dict]:
    """Return the ordered project-bank subset to surface for an archetype.

    Reads ``archetype_projects[key]`` from the canonical source and resolves
    each key against ``project_bank``, preserving map order. An unknown
    archetype or an empty mapping returns ``[]`` — meaning an all-professional
    resume with no Projects section (tier_2 / tier_3 by design).
    """
    src = load_resume_source()
    keys = (src.get("archetype_projects") or {}).get(
        (archetype_key or "").strip(), []
    )
    bank = {p.get("key"): p for p in (src.get("project_bank") or [])}
    return [bank[k] for k in keys if k in bank]


def _render_project_bank_block(bank: list[dict]) -> str:
    """Render the archetype's project bank for the resume prompt.

    Empty bank ⇒ an explicit "no projects" instruction (tier_2 / tier_3
    all-professional resumes). Non-empty ⇒ the candidate projects the model
    may surface, with the order pre-sorted by relevance for the lane.
    """
    if not bank:
        return (
            "PROJECT BANK FOR THIS LISTING: none.\n"
            "This listing type gets an ALL-PROFESSIONAL resume. Output an "
            'empty "projects" list ([]) — do NOT invent a projects section.'
        )
    lines = [
        "PROJECT BANK FOR THIS LISTING (the ONLY personal projects you may "
        "surface — pick the 2-3 most relevant for THIS role, reframe each "
        "one-liner into the listing's language without fabricating, and return "
        'them in the "projects" output key. Never add a project not listed '
        "here):"
    ]
    for p in bank:
        lines.append(
            f"- key: {p.get('key')}\n"
            f"  name: {p.get('name')}\n"
            f"  one_liner: {p.get('one_liner')}"
        )
    return "\n".join(lines)


# Page typography constants used to fit the Education/Skills block. The
# document is letterpaper with 0.5in margins, so the usable text width is
# 8.5in - 2*0.5in = 7.5in ≈ 19.05cm. The original template used 4.5+12.7
# = 17.2cm and worked fine, so we keep that as the total width budget and
# vary how it gets split between the two columns.
_TOTAL_TWO_COL_CM = 17.2
# Hard cap on the left column. Anything wider than this leaves the right
# column too narrow to fit a sensible skills sentence; we fall back to a
# stacked layout instead of pushing past the cap.
_MAX_LABEL_CM = 7.0
_MIN_LABEL_CM = 3.5
# Approximate bold 11pt CMR character width in cm. Empirically, "Evaluation
# & Infrastructure" (27 chars) doesn't fit in 4.5cm but does in ~6.3cm,
# which calibrates to roughly 0.22 cm/char + small padding.
_LABEL_CM_PER_CHAR = 0.22
_LABEL_PADDING_CM = 0.20


def _measure_label_cm(label: str) -> float:
    """Approximate width of a bold 11pt CMR label in cm."""
    return len(label) * _LABEL_CM_PER_CHAR + _LABEL_PADDING_CM


def _decide_skills_layout(
    skills: dict, hint: str = "auto",
) -> tuple[str, float, float]:
    """Pick the layout and column widths for the Education/Skills block.

    Args:
        skills: Mapping of category-name → comma-separated skill string.
        hint: Optional override from the LLM. One of:
            - ``"auto"`` (default) — pick based on label lengths.
            - ``"compact"`` — force the original 4.5cm left column.
            - ``"wide"`` — force the maximum 7.0cm two-column layout.
            - ``"stacked"`` — force the stacked single-column fallback.

    Returns:
        Tuple of (layout_name, left_cm, right_cm). For ``"stacked"`` the
        widths are 0.0 since the renderer doesn't use them.
    """
    if hint not in ("auto", "compact", "wide", "stacked"):
        hint = "auto"
    if hint == "stacked":
        return ("stacked", 0.0, 0.0)
    if hint == "compact":
        return ("two_col", 4.5, _TOTAL_TWO_COL_CM - 4.5)
    if hint == "wide":
        return ("two_col", _MAX_LABEL_CM, _TOTAL_TWO_COL_CM - _MAX_LABEL_CM)

    # Auto: include the literal "Education" row label in the measurement
    # since it sits in the same column.
    labels = ["Education"] + list((skills or {}).keys())
    needed = max((_measure_label_cm(l) for l in labels), default=4.5)
    if needed > _MAX_LABEL_CM:
        return ("stacked", 0.0, 0.0)
    left = max(_MIN_LABEL_CM, min(_MAX_LABEL_CM, round(needed, 1)))
    return ("two_col", left, round(_TOTAL_TWO_COL_CM - left, 1))


def _build_edu_and_skills(
    skills: dict,
    school: str,
    degree: str,
    edu_period: str,
    layout_hint: str = "auto",
) -> str:
    """Render the entire Education + Technical Skills block.

    Replaces the old fixed-width tabular with a layout that adapts to the
    longest label the LLM picked. Education sits in the same column so its
    label width matches the skills labels.
    """
    layout, left_cm, right_cm = _decide_skills_layout(skills, layout_hint)
    edu_value = f"\\textbf{{{school}}} -- {degree} ({edu_period})"

    if layout == "two_col":
        rows = [
            f"\\textbf{{Education}} & {edu_value} \\\\[4pt]"
        ]
        for category, skill_list in (skills or {}).items():
            safe_cat = _escape_latex_safe(category)
            safe_skills = _escape_latex_safe(skill_list)
            rows.append(f"\\textbf{{{safe_cat}}} & {safe_skills} \\\\")
        body = "\n".join(rows)
        return (
            f"\\begin{{tabular}}{{@{{}}p{{{left_cm}cm}} "
            f"p{{{right_cm}cm}}@{{}}}}\n"
            f"{body}\n"
            f"\\end{{tabular}}"
        )

    # Stacked: each category on its own line, label bold then value indented.
    # Used when even the widest two-col layout would wrap a label.
    blocks = [
        f"\\textbf{{Education}}\\\\\n\\hspace*{{1em}}{edu_value}"
    ]
    for category, skill_list in (skills or {}).items():
        safe_cat = _escape_latex_safe(category)
        safe_skills = _escape_latex_safe(skill_list)
        blocks.append(
            f"\\textbf{{{safe_cat}}}\\\\\n\\hspace*{{1em}}{safe_skills}"
        )
    return "\n\n\\vspace{2pt}\n\n".join(blocks)


def _build_skill_rows(skills: dict) -> str:
    """Legacy two-column row builder. Kept as a thin wrapper around the new
    auto-sizing builder for any caller that still references this name.
    Use ``_build_edu_and_skills`` for new code."""
    rows = []
    for category, skill_list in (skills or {}).items():
        safe_cat = _escape_latex_safe(category)
        safe_skills = _escape_latex_safe(skill_list)
        rows.append(f"\\textbf{{{safe_cat}}} & {safe_skills} \\\\")
    return "\n".join(rows)


def _build_experience_block(exp: dict) -> str:
    """Build LaTeX block for one employer.

    Org / title / location / period come from BASE_RESUME and are hand-written
    LaTeX (e.g. ``Algorithms \\& Analysis Engineer``), so they're inserted
    verbatim. Project names and bullets may originate from Claude, so they
    pass through ``_escape_latex_safe`` to neutralise any stray ``#``/``%``/
    ``_`` etc. without breaking deliberate LaTeX commands.
    """
    lines = []
    org = exp["org"]
    title = exp["title"]
    location = exp["location"]
    period = exp["period"]

    lines.append(f"\\textbf{{\\large {org}}} \\hfill {location} \\\\")
    lines.append(f"\\textit{{{title}}} \\hfill \\textit{{{period}}}")

    for proj in exp["projects"]:
        if proj["name"]:
            safe_name = _escape_latex_safe(proj["name"])
            safe_period = _escape_latex_safe(str(proj.get("period") or ""))
            lines.append(
                f"\n\\hspace{{0.5em}}\\textbf{{{safe_name}}} "
                f"\\textit{{({safe_period})}}"
            )
        lines.append("\\begin{itemize}")
        for bullet in proj["bullets"]:
            safe_bullet = _escape_latex_safe(bullet)
            lines.append(f"  \\item {safe_bullet}")
        lines.append("\\end{itemize}")

    return "\n".join(lines)


def _build_projects_block(projects: list | None) -> str:
    """Render the optional, compact Projects section.

    Returns "" when there are no projects, so the renderer emits no empty
    header (tier_2 / tier_3 all-professional resumes). Each project is one
    tight line — bold name + its reframed one-liner — keeping the whole
    section to a few lines so it can never crowd out professional experience.
    Names + descriptions originate from the LLM, so both pass through
    ``_escape_latex_safe``.
    """
    items: list[str] = []
    for proj in projects or []:
        name = _escape_latex_safe(proj.get("name") or "")
        desc = _escape_latex_safe(
            proj.get("description") or proj.get("one_liner") or ""
        )
        if name and desc:
            items.append(f"  \\item \\textbf{{{name}}} -- {desc}")
        elif name or desc:
            items.append(f"  \\item {name or desc}")
    if not items:
        return ""
    body = "\n".join(items)
    return "\\section{Projects}\n\n\\begin{itemize}\n" + body + "\n\\end{itemize}"


def _render_latex(tailored: dict, style: str = "classic") -> str:
    """Build the full LaTeX source for ``tailored`` in the chosen style.

    Pure string assembly — no compile. The trim loop calls this repeatedly
    as it drops content, so it must be cheap and side-effect-free. Unknown
    styles fall back to ``classic``.
    """
    latex = STYLES.get(style) or STYLES["classic"]
    latex = latex.replace("<<NAME>>", BASE_RESUME["name"])
    latex = latex.replace("<<EMAIL>>", BASE_RESUME["email"])
    latex = latex.replace("<<LOCATION>>", BASE_RESUME["location"])
    latex = latex.replace("<<LINKEDIN>>", BASE_RESUME["linkedin"])
    latex = latex.replace("<<WEBSITE>>", BASE_RESUME["website"])

    # Education + Skills, with a column width that adapts to the labels the
    # LLM picked (see _decide_skills_layout). ``skills_layout`` is optional.
    skills_dict = tailored.get("skills") or BASE_RESUME["skills"]
    layout_hint = (tailored.get("skills_layout") or "auto").lower()
    edu_skills_block = _build_edu_and_skills(
        skills=skills_dict,
        school=BASE_RESUME["education"]["school"],
        degree=BASE_RESUME["education"]["degree"],
        edu_period=BASE_RESUME["education"]["period"],
        layout_hint=layout_hint,
    )
    latex = latex.replace("<<EDU_AND_SKILLS>>", edu_skills_block)

    exp_blocks = [
        _build_experience_block(exp)
        for exp in tailored.get("experience", BASE_RESUME["experience"])
    ]
    latex = latex.replace(
        "<<EXPERIENCE_BLOCKS>>", "\n\n\\vspace{6pt}\n\n".join(exp_blocks)
    )

    # Conditional Projects section — only when the tailored dict carries a
    # non-empty `projects` list (archetype-provided bank). Empty/absent ⇒ the
    # placeholder collapses to nothing (no empty header).
    latex = latex.replace(
        "<<PROJECTS_SECTION>>", _build_projects_block(tailored.get("projects"))
    )
    return latex


# ── One-page guarantee ─────────────────────────────────────────────────────
#
# pdflatex prints e.g. ``Output written on resume_X.pdf (1 page, 1234 bytes).``
# We parse N from there; if the marker is absent we fall back to counting
# ``/Type /Page`` objects in the PDF bytes.

_MAX_TRIM_ITERS = 12
# pdflatex's "Output written on <path> (N page[s], M bytes)." summary. The
# "<path>" can be long enough that pdflatex wraps it onto its own line, so
# anchor on the distinctive "(N page[s], M bytes)" tail instead of the path.
_PAGES_RE = re.compile(r"\((\d+)\s+pages?,\s*\d+\s*bytes?\)", re.IGNORECASE)


def _pdf_page_count(stdout: str, pdf_path) -> int | None:
    """Return the page count of a compiled PDF, or None if undetermined."""
    if stdout:
        m = _PAGES_RE.search(stdout)
        if m:
            return int(m.group(1))
    try:
        data = Path(pdf_path).read_bytes()
    except (OSError, TypeError, ValueError):
        return None
    # ``/Type /Page`` (not ``/Pages``) marks each page object.
    count = len(re.findall(rb"/Type\s*/Page[^s]", data))
    return count or None


def _experience_projects(tailored: dict) -> list[tuple[dict, dict]]:
    """Flatten to (employer, project) pairs in the LLM's order."""
    pairs: list[tuple[dict, dict]] = []
    for emp in tailored.get("experience", []) or []:
        for proj in emp.get("projects", []) or []:
            pairs.append((emp, proj))
    return pairs


def _trim_one_unit(tailored: dict) -> bool:
    """Drop exactly one unit of content from ``tailored`` in place.

    Order (the deterministic one-page guarantee). Personal projects are the
    lowest-priority content — the professional spine is never sacrificed to
    fit a project — so they trim FIRST:
      0. While a personal `projects` entry remains, drop the last one (each is
         a single line, lowest priority last).
      1. Then drop the last bullet of the longest-remaining professional
         entry (while any entry still has >1 bullet).
      2. Once every professional entry is at its floor (≤1 bullet), drop the
         lowest-priority whole entry — the last one in the LLM's order —
         removing its employer if that empties it.

    Returns True if something was trimmed, False if nothing remains to trim
    (no personal projects and ≤1 professional entry at the bullet floor).
    """
    # 0. Personal projects first — protect professional experience.
    personal = tailored.get("projects")
    if personal:
        personal.pop()
        return True

    pairs = _experience_projects(tailored)
    if not pairs:
        return False

    trimmable = [(e, p) for e, p in pairs if len(p.get("bullets") or []) > 1]
    if trimmable:
        _emp, proj = max(trimmable, key=lambda ep: len(ep[1]["bullets"]))
        proj["bullets"].pop()
        return True

    # All projects at the floor — drop a whole entry, lowest priority last.
    if len(pairs) <= 1:
        return False
    emp, proj = pairs[-1]
    emp["projects"].remove(proj)
    if not emp.get("projects"):
        tailored["experience"].remove(emp)
    return True


def _compile_once(tex_path: Path, pdf_path: Path, td_path: Path) -> tuple[bool, str, str]:
    """Run pdflatex once (second pass only if the first fails).

    Returns (success, stdout, error_log). On the success path error_log is
    "". Missing pdflatex / timeouts are reported via success=False rather
    than raising, so the trim loop degrades gracefully.
    """
    cmd = [
        "pdflatex", "-interaction=nonstopmode",
        "-output-directory", str(td_path), str(tex_path),
    ]

    def _out(result) -> str:
        # pdflatex can emit non-UTF-8 bytes (e.g. font encoding notes), so
        # capture bytes and decode defensively rather than text=True (which
        # raises UnicodeDecodeError mid-run).
        return (result.stdout or b"").decode("utf-8", "replace")

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        success = result.returncode == 0 and pdf_path.exists()
        if success:
            return (True, _out(result), "")
        logger.warning(f"LaTeX first pass issue: {_out(result)[-500:]}")
        result2 = subprocess.run(cmd, capture_output=True, timeout=30)
        success = result2.returncode == 0 and pdf_path.exists()
        if success:
            return (True, _out(result2), "")
        return (False, _out(result2), _out(result2)[-2000:])
    except subprocess.TimeoutExpired:
        msg = "pdflatex timed out after 30 seconds"
        logger.error(msg)
        return (False, "", msg)
    except FileNotFoundError:
        msg = "pdflatex not found — LaTeX not installed"
        logger.error(msg)
        return (False, "", msg)


def _compile_and_count_factory(td_path: Path, safe_company: str):
    """Build the compile+count callback the trim loop drives.

    The callback writes the .tex, compiles it, and returns
    (success, pages, pdf_bytes, error_log) so _fit_to_one_page stays pure
    logic and is trivially mockable in tests.
    """
    tex_path = td_path / f"resume_{safe_company}.tex"
    pdf_path = td_path / f"resume_{safe_company}.pdf"

    def _run(latex: str) -> tuple[bool, int | None, bytes | None, str]:
        tex_path.write_text(latex, encoding="utf-8")
        success, stdout, log = _compile_once(tex_path, pdf_path, td_path)
        if not success:
            return (False, None, None, log)
        pages = _pdf_page_count(stdout, pdf_path)
        return (True, pages, pdf_path.read_bytes(), "")

    return _run


def _trim_budget(tailored: dict) -> int:
    """Max trims needed to fully reduce ``tailored`` to its floor.

    Each iteration drops exactly one bullet or one project, so the number
    of trims to exhaust all content is (total bullets) + (total projects).
    Sizing the cap to this guarantees the loop can always reach a single
    page rather than bailing early on an unusually long input; the cap is
    still a finite backstop because _trim_one_unit makes monotonic progress.
    """
    pairs = _experience_projects(tailored)
    bullets = sum(len(p.get("bullets") or []) for _e, p in pairs)
    personal = len(tailored.get("projects") or [])
    return bullets + len(pairs) + personal + 2


def _fit_to_one_page(
    tailored: dict, style: str, compile_and_count, max_iters: int | None = None,
) -> dict:
    """Build → compile → count, trimming one unit per iteration until the
    PDF is a single page (or nothing remains to trim / the cap is hit).

    ``max_iters`` defaults to a content-sized budget (see _trim_budget) so
    the one-page guarantee holds even for an over-long input; pass an
    explicit value to bound it. Works on a deepcopy so the caller's
    ``tailored`` is never mutated; the trimmed copy that actually compiled
    is returned as ``tailored_data``. If the very first compile fails (e.g.
    pdflatex missing) it returns immediately without trimming — the loop is
    the production guarantee but must never crash when LaTeX is unavailable.
    """
    if max_iters is None:
        max_iters = max(_MAX_TRIM_ITERS, _trim_budget(tailored))
    work = copy.deepcopy(tailored)
    latex = _render_latex(work, style)
    success, pages, pdf_bytes, log = compile_and_count(latex)

    iterations = 0
    while success and pages is not None and pages > 1 and iterations < max_iters:
        if not _trim_one_unit(work):
            break
        iterations += 1
        latex = _render_latex(work, style)
        success, pages, pdf_bytes, log = compile_and_count(latex)

    return {
        "latex_source": latex,
        "pdf_bytes": pdf_bytes if success else None,
        "compile_success": success,
        "compile_log": log if not success else "",
        "pages": pages,
        "trim_iterations": iterations,
        "tailored_data": work,
    }


def generate_tailored_latex(job: dict, tailoring: dict) -> dict:
    """
    Use Claude to select and reorder resume content for a specific job,
    then compile to PDF.

    Args:
        job: Dict with job details
        tailoring: Output from tailor_resume() — emphasis_areas, keywords, etc.

    Returns:
        Dict with latex_source, pdf_path, and compilation status.
    """
    job_title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")
    job_desc = job.get("description", "")

    # Match Agent transcript (optional). When present, lets the LaTeX
    # selector lean into the projects + framing Vishal himself flagged in
    # the dashboard chat.
    match_chat = (job.get("match_chat_transcript") or "").strip()
    match_chat_block = (
        f"\n\nMATCH AGENT INTERVIEW (Vishal's own framing for THIS role — "
        f"use this to bias project selection, bullet emphasis, and skill "
        f"category ordering toward what he actually wants highlighted):\n"
        f"{match_chat}\n"
        if match_chat else ""
    )

    # Archetype (J-4). Reuse the upstream tailoring run's classification
    # if present; otherwise classify here. Same per-job idempotency as
    # resume.py.
    archetype_meta = (
        (tailoring or {}).get("_archetype")
        or job.get("_archetype")
        or classify_archetype(job)
    )
    job["_archetype"] = archetype_meta
    archetype_key = archetype_meta.get("archetype", "")
    archetype_block = render_archetype_block(archetype_key)

    # Archetype-conditional project bank. Only the lane's subset (or none)
    # reaches the model — projects can ONLY come from here, so the LLM can't
    # inject a generic "Personal Projects" block on its own.
    project_bank = projects_for_archetype(archetype_key)
    project_bank_block = _render_project_bank_block(project_bank)
    honesty = load_resume_source().get("honesty_constraints") or []
    honesty_block = (
        "HONESTY CONSTRAINTS (carry into every bullet + project line):\n"
        + "\n".join(f"- {c}" for c in honesty)
    ) if honesty else ""

    prompt = load_task_prompt(
        "tailor_latex_resume",
        base_resume_json=json.dumps(BASE_RESUME, indent=2),
        tailoring_json=json.dumps(tailoring, indent=2),
        job_title=job_title,
        company=company,
        job_desc=job_desc,
        match_chat_block=match_chat_block,
        archetype_block=archetype_block,
        project_bank_block=project_bank_block,
        honesty_block=honesty_block,
    )

    logger.info(
        "Resume project bank for archetype %r: %s",
        archetype_key,
        [p.get("key") for p in project_bank] or "none (all-professional)",
    )

    # Session I: static rules + profile + voice ride in the cached
    # system prefix; only the per-job prompt above goes uncached.
    # Credits-first with subscription-OAuth fallback — see jobpipe.shared.llm.
    response_text = llm.complete(
        system=cached_system_blocks(),
        prompt=prompt,
        model=CLAUDE_MODEL,
        max_tokens=4000,
    ).strip()

    # Parse JSON
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0]
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0]

    tailored = json.loads(response_text.strip())

    # ── Style selection (deterministic per archetype) ───────────────────
    style = _select_style(archetype_meta.get("archetype", ""))

    skills_dict = tailored.get("skills") or BASE_RESUME["skills"]
    layout_hint = (tailored.get("skills_layout") or "auto").lower()
    chosen_layout, _, _ = _decide_skills_layout(skills_dict, layout_hint)
    logger.info(
        f"Resume style={style!r}; Education/Skills layout: hint={layout_hint!r} "
        f"→ {chosen_layout!r} (longest label = "
        f"{max([len('Education')] + [len(k) for k in skills_dict.keys()])} chars)"
    )

    # ── Build → compile → count → trim, until one page (the guarantee) ──
    # The trim loop rebuilds the LaTeX from the (possibly trimmed) tailored
    # dict each iteration. If pdflatex is unavailable the loop returns after
    # the first failed compile without crashing.
    safe_company = "".join(c if c.isalnum() else "_" for c in company)

    with tempfile.TemporaryDirectory(prefix="latex_resume_") as td:
        fit = _fit_to_one_page(
            tailored, style, _compile_and_count_factory(Path(td), safe_company)
        )

    latex = fit["latex_source"]
    pdf_bytes = fit["pdf_bytes"]
    compile_success = fit["compile_success"]
    compile_log = fit["compile_log"]
    final_tailored = fit["tailored_data"]

    if not compile_success and "pdflatex not found" in compile_log:
        logger.warning(
            "pdflatex unavailable — skipped the one-page trim loop. "
            "Resume PDF will not be produced."
        )

    logger.info(
        f"LaTeX resume for {company}: style={style}, "
        f"compile={'OK' if compile_success else 'FAILED'}, "
        f"pages={fit['pages']}, trims={fit['trim_iterations']}, "
        f"bytes={len(pdf_bytes) if pdf_bytes else 0}"
    )

    return {
        "latex_source": latex,
        "pdf_bytes": pdf_bytes,
        "compile_success": compile_success,
        "compile_log": compile_log if not compile_success else "",
        "tailored_data": final_tailored,
        "style": style,
        "pages": fit["pages"],
    }

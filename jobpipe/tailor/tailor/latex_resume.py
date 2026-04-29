"""
tailor/latex_resume.py — Generate tailored LaTeX resumes compiled to PDF.

Takes the output of tailor_resume() and produces a LaTeX document matching
Vishal's existing resume style (Comp Neuroscience variant), then compiles to PDF.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import anthropic
from jobpipe.config import ANTHROPIC_API_KEY, TAILOR_CLAUDE_MODEL as CLAUDE_MODEL
from jobpipe.tailor.paths import CANDIDATE_PROFILE_PATH
from prompts import load_profile, load_prompt
from tailor.archetype import classify_archetype, render_archetype_block
from tailor.normalize import normalize_for_ats

logger = logging.getLogger("tailor.latex_resume")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Base resume data (source of truth — never fabricate beyond this) ──────

BASE_RESUME = {
    "name": "Vishal Pathak",
    "email": "vishalp@thak.io",
    "location": "Atlanta, GA",
    "linkedin": "linkedin.com/in/vishalhpathak",
    "website": "vishal.pa.thak.io",
    "education": {
        "school": "Florida Institute of Technology",
        "degree": "B.S. Electrical Engineering, cum laude",
        "period": "2019--2021",
    },
    "skills": {
        "Neuromorphic & Simulation": "Intel LavaSDK, NxSDK, Brian2, MuJoCo, Gymnasium API, FlyGym, VHDL, RTL design, AFSIM surrogate modeling",
        "Programming & ML": "Python, C/C++, PyTorch, TensorFlow, NumPy, Matplotlib, scikit-learn, PyQt6",
        "Systems & Hardware": "FPGA development, embedded systems (STM32), PCB design (EAGLE/Altium), serial protocols (RS-232/RS-485), ruggedized sensor deployment, HPC clusters",
        "Tools & Platforms": "Git, CI/CD (Jacamar-CI), pytest, Docker, Linux, MATLAB, LabVIEW",
    },
    "experience": [
        {
            "org": "Georgia Tech Research Institute",
            "title": "Algorithms \\& Analysis Engineer",
            "location": "Atlanta, GA",
            "period": "August 2021 -- Present",
            "projects": [
                {
                    "name": "SPARSE: Spiking Processing for Autonomous RF \\& Sensor Engineering",
                    "period": "Aug 2021 -- Jul 2024",
                    "bullets": [
                        "Developed VHDL models of CUBA and LIF neurons matching Intel's LavaSDK behavior, enabling seamless deployment of spiking neural networks from simulation to FPGA hardware",
                        "Deployed and benchmarked custom spiking networks on Intel's Kapoho Bay neuromorphic platform, evaluating power consumption and inference performance for edge applications",
                        "Contributed to DNN$\\to$SNN conversion pipeline using backpropagation in the spiking regime for overhead imagery and radar signal processing applications",
                        "Trained deep learning models on GTRI's ICEHAMMER HPC cluster using PyTorch and TensorFlow frameworks",
                    ],
                },
                {
                    # NOTE: Spynel band below is assumed MWIR based on HGH's Spynel-S/X
                    # line (the flagship MWIR panoramic thermal cameras); Vishal recalls
                    # the unit as "Spynel M" but wasn't sure of the band. Confirm and
                    # flip to LWIR if it was actually built around the Spynel-U.
                    "name": "360-SA: 360° Situational Awareness",
                    "period": "2023 -- Present",
                    "bullets": [
                        "Established comprehensive pytest-based unit test suite on HPC cluster, covering KITTI data ingestion, object detection, and tracking pipeline validation",
                        "Designed and deployed Jacamar-CI pipeline to automate build, test, and deployment workflows for vehicle-mounted 360° camera systems",
                        "Engineered hardware solution using TI's SD384EVK board to resolve impedance mismatch between cameras and Wolf Orin computing platform",
                        "Built a custom frame grabber for HGH's Spynel MWIR panoramic thermal camera, bridging its native output into the 360-SA vision pipeline so detection and tracking modules could consume the feed alongside the existing visible-band cameras",
                        "Modernized the 360-SA operator GUI by migrating the legacy tkinter application to PyQt6, adding collapsible and movable sub-windows, individually selectable UI elements, and a layout that matched the requested operator workflow",
                    ],
                },
                {
                    "name": "HACS: Hardware \\& Control System",
                    "period": "2024",
                    "bullets": [
                        "Managed complete lifecycle of custom thermal control PCB: hand-populated 0402 components on milled EagleCAD boards and delivered integrated system for vehicle demo",
                        "Developed C++ firmware for STM32 microcontroller to control thermal switches and stream status data over raw UDP/TCP protocols",
                    ],
                },
                {
                    "name": "GREMLIN: MWIR Video Processing",
                    "period": "2023",
                    "bullets": [
                        "Performed literature review to select optimal model architectures for post-processing of MWIR video datasets",
                        "Designed annotation-repair algorithm that re-labels mis-detections by running data through trained models, extracting metadata, and performing similarity comparison between detections",
                    ],
                },
                {
                    "name": "ENFIRE: Environmental Imaging",
                    "period": "2024 -- Present",
                    "bullets": [
                        "Assembled rugged, portable sensor enclosure housing Jetson Orin, Ouster LiDAR, DAGR receiver, power pack, and network switch/router",
                        "Conducted campus-scale SLAM and point-cloud mapping tests to validate environmental-imaging performance with and without enclosure",
                    ],
                },
                {
                    "name": "DRAGON: Drone Swarm Synchronization",
                    "period": "2024",
                    "bullets": [
                        "Implemented Chrony time synchronization across multi-drone swarm and profiled system resilience under simulated network disruptions",
                    ],
                },
                {
                    "name": "PAAM: AFSIM Simulation Surrogate Modeling",
                    "period": "2024",
                    "bullets": [
                        "Built visualizations and surrogate models for high-dimensional AFSIM simulation data, enabling exploratory analysis of sim outputs and faster iteration than re-running the full simulation for each parameter sweep",
                    ],
                },
                {
                    "name": "SHELAC: Rooftop Meteorological Sensor Deployment",
                    "period": "Nov 2025 -- Present",
                    "bullets": [
                        "Deployed two weather stations and three anemometers along the northern edge of the building roof, running communication cabling from the rooftop through an access hatch into the LIDAR lab machine downstairs",
                        "Sourced all cable stock, connectors, and converters for the install; fabricated and bench-tested the ruggedized Ethernet runs for the weather stations and the serial runs for the anemometers alongside a coworker before on-roof install",
                        "Converted the Young sonic anemometer from RS-232 to RS-485 with an in-line converter to preserve signal integrity over the long cable run, which would otherwise have degraded the serial signal past a usable threshold",
                    ],
                },
            ],
        },
        {
            "org": "Rain Neuromorphics",
            "title": "Electrical Engineering Intern",
            "location": "Gainesville, FL",
            "period": "May 2017 -- May 2018",
            "projects": [
                {
                    "name": None,
                    "period": None,
                    "bullets": [
                        "Designed and tested FPGA-based measurement system with Altera FPGA communicating with Arduino interface for characterizing in-house memristive devices",
                        "Developed and manufactured PCB in EAGLE to house 40 leaky integrate-and-fire neurons, integrating measurement system circuitry",
                        "Analyzed spiking behavior data output from measurement system to benchmark MNIST dataset performance on neuromorphic hardware",
                    ],
                },
            ],
        },
    ],
}


LATEX_TEMPLATE = r"""
\documentclass[11pt, letterpaper]{article}

\usepackage[T1]{fontenc}
\usepackage[margin=0.5in]{geometry}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage{hyperref}
\usepackage{xcolor}

% ── Formatting ─────────────────────────────────────────────────────────────
\pagestyle{empty}
\setlength{\parindent}{0pt}
\definecolor{linkblue}{HTML}{2563EB}

\hypersetup{
    colorlinks=true,
    urlcolor=linkblue,
    linkcolor=linkblue,
}

\titleformat{\section}{\large\bfseries\color{linkblue}}{}{0em}{}[\titlerule]
\titlespacing*{\section}{0pt}{12pt}{6pt}

\setlist[itemize]{leftmargin=1.2em, itemsep=2pt, parsep=0pt, topsep=2pt}

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

\end{document}
"""


import re as _re


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
    # Load voice profile
    voice_path = Path(__file__).parent.parent / "templates" / "VOICE_PROFILE.md"
    voice_profile = voice_path.read_text(encoding="utf-8") if voice_path.exists() else ""

    profile = load_profile()

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
    archetype_block = render_archetype_block(archetype_meta.get("archetype", ""))

    prompt = load_prompt(
        "tailor_latex_resume",
        voice_profile=voice_profile,
        profile=profile,
        base_resume_json=json.dumps(BASE_RESUME, indent=2),
        tailoring_json=json.dumps(tailoring, indent=2),
        job_title=job_title,
        company=company,
        job_desc=job_desc,
        match_chat_block=match_chat_block,
        archetype_block=archetype_block,
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = response.content[0].text.strip()

    # Parse JSON
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0]
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0]

    tailored = json.loads(response_text.strip())

    # ── Build LaTeX source ──────────────────────────────────────────────
    latex = LATEX_TEMPLATE
    latex = latex.replace("<<NAME>>", BASE_RESUME["name"])
    latex = latex.replace("<<EMAIL>>", BASE_RESUME["email"])
    latex = latex.replace("<<LOCATION>>", BASE_RESUME["location"])
    latex = latex.replace("<<LINKEDIN>>", BASE_RESUME["linkedin"])
    latex = latex.replace("<<WEBSITE>>", BASE_RESUME["website"])

    # Education + Skills, with a column width that adapts to the labels the
    # LLM picked. The ``skills_layout`` hint is optional — the LLM may pass
    # "auto" (default), "compact", "wide", or "stacked" to override the
    # auto-fit; anything else is treated as auto.
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
    chosen_layout, _, _ = _decide_skills_layout(skills_dict, layout_hint)
    logger.info(
        f"Education/Skills layout: hint={layout_hint!r} → {chosen_layout!r} "
        f"(longest label = {max([len('Education')] + [len(k) for k in skills_dict.keys()])} chars)"
    )

    # Experience
    exp_blocks = []
    for exp in tailored.get("experience", BASE_RESUME["experience"]):
        exp_blocks.append(_build_experience_block(exp))
    latex = latex.replace("<<EXPERIENCE_BLOCKS>>", "\n\n\\vspace{6pt}\n\n".join(exp_blocks))

    # ── Compile to PDF in a tempdir (nothing persists locally) ─────────
    safe_company = "".join(c if c.isalnum() else "_" for c in company)
    pdf_bytes: bytes | None = None
    compile_success = False
    compile_log = ""

    with tempfile.TemporaryDirectory(prefix="latex_resume_") as td:
        td_path = Path(td)
        tex_path = td_path / f"resume_{safe_company}.tex"
        pdf_path = td_path / f"resume_{safe_company}.pdf"
        tex_path.write_text(latex, encoding="utf-8")

        try:
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode",
                 "-output-directory", str(td_path), str(tex_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            compile_success = result.returncode == 0 and pdf_path.exists()
            if not compile_success:
                compile_log = (result.stdout or result.stderr)[-2000:]
                logger.warning(f"LaTeX first pass issue: {compile_log[-500:]}")
                # Second pass (e.g. for references)
                result2 = subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode",
                     "-output-directory", str(td_path), str(tex_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                compile_success = result2.returncode == 0 and pdf_path.exists()
                if not compile_success:
                    compile_log = (result2.stdout or result2.stderr)[-2000:]
        except subprocess.TimeoutExpired:
            compile_log = "pdflatex timed out after 30 seconds"
            logger.error(compile_log)
        except FileNotFoundError:
            compile_log = "pdflatex not found — LaTeX not installed"
            logger.error(compile_log)

        if compile_success:
            pdf_bytes = pdf_path.read_bytes()

    logger.info(
        f"LaTeX resume for {company}: compile={'OK' if compile_success else 'FAILED'}, "
        f"bytes={len(pdf_bytes) if pdf_bytes else 0}"
    )

    return {
        "latex_source": latex,
        "pdf_bytes": pdf_bytes,
        "compile_success": compile_success,
        "compile_log": compile_log if not compile_success else "",
        "tailored_data": tailored,
    }

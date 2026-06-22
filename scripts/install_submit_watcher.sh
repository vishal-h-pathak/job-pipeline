#!/usr/bin/env bash
#
# install_submit_watcher.sh — set up the local submit watcher to run
# autonomously via launchd (feat/submit-watch, Part 2).
#
# Run ONCE. Fills the plist template with your actual repo path, venv, and Node
# bin dir, copies it to ~/Library/LaunchAgents/, and loads it. Idempotent —
# re-running unloads then reloads. After this the watcher starts at login and
# stays alive across reboots; a dashboard "Pre-fill" click opens a Chrome window
# on your machine and fills the form.
#
# Prereqs:
#   - The repo's venv exists and has `pip install -e .` (so `jobpipe-submit` is
#     on the venv's bin). Pass --venv to point at a non-default venv.
#   - `.env` is populated in the repo root (SUPABASE_URL,
#     SUPABASE_SERVICE_ROLE_KEY, etc.).
#   - You've logged into your ATS accounts once in the persistent Chrome
#     profile (run `jobpipe-submit --watch` by hand the first time, or open a
#     job and log in when the tab appears).
#   - The Supabase Realtime migration (014_realtime_jobs.sql) is applied to the
#     live project — otherwise the websocket gets no events.
#
# Usage:
#   scripts/install_submit_watcher.sh [--venv PATH] [--channel chrome|chromium]
set -euo pipefail

LABEL="io.thak.jobpipe.submit-watch"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${REPO_DIR}/deploy/launchd/${LABEL}.plist"
DEST_DIR="${HOME}/Library/LaunchAgents"
DEST="${DEST_DIR}/${LABEL}.plist"
LOG_PATH="${HOME}/Library/Logs/jobpipe-submit-watch.log"

VENV_DIR=""
CHANNEL="chrome"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --venv)    VENV_DIR="$2"; shift 2 ;;
    --channel) CHANNEL="$2";  shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Locate the venv: explicit --venv, then .venv, then venv.
if [[ -z "${VENV_DIR}" ]]; then
  if   [[ -x "${REPO_DIR}/.venv/bin/jobpipe-submit" ]]; then VENV_DIR="${REPO_DIR}/.venv"
  elif [[ -x "${REPO_DIR}/venv/bin/jobpipe-submit"  ]]; then VENV_DIR="${REPO_DIR}/venv"
  fi
fi
if [[ -z "${VENV_DIR}" || ! -x "${VENV_DIR}/bin/jobpipe-submit" ]]; then
  echo "ERROR: could not find jobpipe-submit in a venv under ${REPO_DIR}." >&2
  echo "       Create the venv and run 'pip install -e .', or pass --venv PATH." >&2
  exit 1
fi
VENV_BIN="${VENV_DIR}/bin"

# Node bin dir (Playwright shells out to node for channel=chrome).
NODE_BIN="$(dirname "$(command -v node || true)")"
if [[ -z "${NODE_BIN}" || ! -x "${NODE_BIN}/node" ]]; then
  echo "WARNING: 'node' not found on PATH. channel=chrome needs Node installed." >&2
  NODE_BIN="/opt/homebrew/bin"
fi

echo "Repo:    ${REPO_DIR}"
echo "Venv:    ${VENV_DIR}"
echo "Node:    ${NODE_BIN}"
echo "Channel: ${CHANNEL}"
echo "Log:     ${LOG_PATH}"

mkdir -p "${DEST_DIR}" "$(dirname "${LOG_PATH}")"

# Render the template. Escape '&' and '|' are not expected in these paths.
sed \
  -e "s|@@VENV_BIN@@|${VENV_BIN}|g" \
  -e "s|@@REPO_DIR@@|${REPO_DIR}|g" \
  -e "s|@@NODE_BIN@@|${NODE_BIN}|g" \
  -e "s|@@LOG_PATH@@|${LOG_PATH}|g" \
  "${TEMPLATE}" > "${DEST}"

# If the user asked for bundled chromium, strip the channel env from the rendered
# plist (delete the key + its following <string> value line).
if [[ "${CHANNEL}" != "chrome" ]]; then
  /usr/bin/sed -i '' '/<key>JOBPIPE_BROWSER_CHANNEL<\/key>/,+1d' "${DEST}"
fi

echo "Wrote ${DEST}"

# Load (idempotent: unload first if already loaded). Prefer the modern
# bootstrap/bootout API, fall back to load/unload on older macOS.
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  echo "Already loaded — unloading first."
  launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null \
    || launchctl unload "${DEST}" 2>/dev/null || true
fi

if launchctl bootstrap "${DOMAIN}" "${DEST}" 2>/dev/null; then
  echo "Bootstrapped ${DOMAIN}/${LABEL}."
else
  echo "bootstrap unavailable — falling back to launchctl load."
  launchctl load "${DEST}"
fi
launchctl enable "${DOMAIN}/${LABEL}" 2>/dev/null || true

echo
echo "Status:"
if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  launchctl print "${DOMAIN}/${LABEL}" | grep -E '^\s*(state|pid) =' || true
  echo "✓ Watcher is loaded. It runs at login and stays alive."
else
  echo "✗ Not loaded — check ${LOG_PATH}."
  exit 1
fi
echo
echo "Tail the log with:  tail -f ${LOG_PATH}"
echo "Now click 'Pre-fill Form' on the dashboard; a Chrome window should open."

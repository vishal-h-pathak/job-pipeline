#!/usr/bin/env bash
#
# uninstall_submit_watcher.sh — stop and remove the submit-watch LaunchAgent
# (feat/submit-watch, Part 2). Idempotent — safe to run when nothing is loaded.
set -euo pipefail

LABEL="io.thak.jobpipe.submit-watch"
DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  echo "Unloading ${DOMAIN}/${LABEL}..."
  launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null \
    || launchctl unload "${DEST}" 2>/dev/null || true
else
  echo "Not loaded — nothing to unload."
fi

if [[ -f "${DEST}" ]]; then
  rm -f "${DEST}"
  echo "Removed ${DEST}"
else
  echo "No plist at ${DEST}."
fi

echo "✓ Uninstalled. The persistent Chrome profile and logs are left in place."
echo "  Profile: ~/.jobpipe/chrome-profile   Log: ~/Library/Logs/jobpipe-submit-watch.log"

#!/bin/bash
cd /Users/jarvis/dev/jarvis/job-hunter
echo "=== Run at $(date '+%Y-%m-%d %H:%M:%S') ===" >> agent.log
/usr/bin/python3 job_agent.py >> agent.log 2>&1

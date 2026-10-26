#!/bin/bash
# Ensure session storage (/mnt/workspace) is writable by sbx (uid 1000).
# This runs as root before dropping to the sbx user.
# AgentCore mounts sessionStorage as root-owned; we chown it once.
if [ -d "/mnt/workspace" ]; then
  chown -R 1000:1000 /mnt/workspace 2>/dev/null || true
fi
# Python image has `python`; Swift (Ubuntu) image has `python3`. Prefer whichever exists.
PY=$(command -v python || command -v python3)
exec su -s /bin/bash sbx -c "$PY /app/app.py"

#!/usr/bin/env bash
# Starts CKAD Lab at http://127.0.0.1:8000 (local only; there is no authentication).
# Ctrl+C stops the server and tears down the clusters of active sessions.
set -euo pipefail
cd "$(dirname "$0")"

[ -x .venv/bin/uvicorn ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
docker image inspect ckad-lab-shell >/dev/null 2>&1 || docker build -q -t ckad-lab-shell app/shell

echo "CKAD Lab at http://${HOST:-127.0.0.1}:${PORT:-8000}  (MAX_SESSIONS=${MAX_SESSIONS:-2}, SESSION_MINUTES=${SESSION_MINUTES:-120})"
exec .venv/bin/uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"

#!/bin/bash
# Runs the API on 127.0.0.1:8000 and the web server on $PORT; if either exits, the container stops.
set -euo pipefail
cd /app/backend
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers &
cd /app/web
HOSTNAME=0.0.0.0 PORT="${PORT:-10000}" node server.js &
wait -n
exit 1

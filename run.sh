#!/bin/bash
# Start Gulnaaz Job Search (dashboard + daily refresh loop).
cd "$(dirname "$0")"

# Stop a previous instance if it is still running.
PIDFILE="data/app.pid"
if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null)
  if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
    echo "Stopping previous instance (pid $OLD)…"
    kill "$OLD"
    sleep 1
  fi
fi

exec python3 -u app.py

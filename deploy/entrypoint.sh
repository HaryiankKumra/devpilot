#!/usr/bin/env bash
# =============================================================================
# Single-container entrypoint: migrate, then run the worker and the API side by
# side, and die if either one dies.
#
# The last part is the important one. A container whose worker has crashed but
# whose API still answers health checks looks healthy to the platform and
# silently reviews nothing. Exiting takes the whole container down, the host
# restarts it, and the failure is visible in the restart count rather than
# discovered a week later.
# =============================================================================
set -euo pipefail

PORT="${PORT:-10000}"
# Free tiers are memory-constrained (Render: 512 MB). One uvicorn worker and a
# solo-pool Celery worker -- tasks run in the worker's own process, no forked
# children -- fit comfortably; the prefork default would not.
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

echo "==> applying migrations"
alembic upgrade head

echo "==> starting worker"
celery -A app.worker.celery_app:celery_app worker \
  --loglevel=info --pool=solo --max-tasks-per-child=50 &
WORKER_PID=$!

echo "==> starting api on :${PORT}"
uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" \
  --workers "${UVICORN_WORKERS}" --proxy-headers --forwarded-allow-ips '*' \
  --no-server-header &
API_PID=$!

# Forward termination so the platform's stop is graceful for both.
trap 'kill -TERM "$WORKER_PID" "$API_PID" 2>/dev/null; wait' TERM INT

# `wait -n` returns when the *first* child exits. Whichever it was, the
# container is no longer whole, so stop the other and report the failure.
wait -n "$WORKER_PID" "$API_PID"
STATUS=$?
echo "==> a process exited with status ${STATUS}; stopping the other"
kill -TERM "$WORKER_PID" "$API_PID" 2>/dev/null || true
wait || true
exit "${STATUS}"

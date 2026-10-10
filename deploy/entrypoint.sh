#!/bin/sh
# Container entrypoint (design §9.2). exec so PID 1 is uvicorn and signals arrive.
set -eu

# Optional preflight hook (no-op by default).
if [ -n "${MONSOON_GRID_PREFLIGHT:-}" ]; then
  echo "running preflight: ${MONSOON_GRID_PREFLIGHT}"
  sh -c "${MONSOON_GRID_PREFLIGHT}"
fi

exec python -m uvicorn deploy.asgi:app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}" \
  --log-config /opt/monsoon/deploy/uvicorn-logging.json \
  --no-access-log \
  --workers 1

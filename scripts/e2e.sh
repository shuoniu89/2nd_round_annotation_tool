#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
export DATA_DIR="$(mktemp -d "${TMPDIR:-/tmp}/codecheck-e2e.XXXXXX")"
export DJANGO_DEBUG=1
export CSRF_TRUSTED_ORIGINS=http://127.0.0.1:13000
export DJANGO_API_URL=http://127.0.0.1:18000
export NEXT_TELEMETRY_DISABLED=1
export CODECHECK_TEST_DIST_DIR=.next-e2e
export E2E_BASE_URL=http://127.0.0.1:13000
.venv/bin/python backend/manage.py migrate --noinput
.venv/bin/python backend/manage.py import_workbook
.venv/bin/python backend/manage.py runserver 127.0.0.1:18000 --noreload > "$DATA_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
cd frontend
node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 13000 > "$DATA_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
cleanup() { kill "$FRONTEND_PID" "$BACKEND_PID" 2>/dev/null || true; wait "$FRONTEND_PID" "$BACKEND_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
node --input-type=module -e '
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const health = await fetch("http://127.0.0.1:18000/api/health/");
      const response = await fetch("http://127.0.0.1:13000");
      if (health.ok && response.ok) process.exit(0);
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error("Test servers did not become ready. Check the temporary server logs.");
'
npm run test:e2e
echo "Browser checks passed. Test database and logs: $DATA_DIR"

#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
if [[ ! -x .venv/bin/python || ! -d frontend/node_modules ]]; then
  echo "Install dependencies first: ./scripts/setup.sh"
  exit 1
fi
.venv/bin/python backend/manage.py migrate --noinput
.venv/bin/python backend/manage.py import_workbook
.venv/bin/python backend/manage.py runserver "127.0.0.1:${BACKEND_PORT:-8000}" &
DJANGO_PID=$!
cleanup() { kill "$DJANGO_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
cd frontend
export DJANGO_API_URL="http://127.0.0.1:${BACKEND_PORT:-8000}"
npm run dev -- --port "${FRONTEND_PORT:-3000}"


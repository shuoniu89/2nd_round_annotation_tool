#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
if [[ ! -x .venv/bin/python ]]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -r backend/requirements.txt
cd frontend
npm ci --no-audit --no-fund
cd "$PROJECT_DIR"
.venv/bin/python backend/manage.py migrate --noinput
.venv/bin/python backend/manage.py import_workbook
echo "Setup complete. Start the app with ./scripts/dev.sh"


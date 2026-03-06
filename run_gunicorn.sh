#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${APP_ENV:=development}"
: "${APP_ENV_FILE:=/volume1/hwi/config/env/DjangoProto8/.env}"
export APP_ENV
export APP_ENV_FILE
export PYTHONPATH="$SCRIPT_DIR/app"

exec "$SCRIPT_DIR/venv/bin/gunicorn" \
  --chdir "$SCRIPT_DIR/app" \
  --bind 127.0.0.1:9898 \
  --workers 1 \
  --access-logfile - \
  --error-logfile - \
  --log-level info \
  --timeout 120 \
  --graceful-timeout 30 \
  --keep-alive 2 \
  DjangoProto8.wsgi:application

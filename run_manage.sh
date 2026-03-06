#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${APP_ENV_FILE:=/volume1/hwi/config/env/DjangoProto8/.env}"
export APP_ENV_FILE

if [ "${APP_ENV:-}" = "test" ]; then
  export APP_DB_ENGINE=sqlite
fi

exec "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/app/manage.py" "$@"

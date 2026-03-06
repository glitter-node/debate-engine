#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/.tmp/ci"
mkdir -p "$LOG_DIR"

TEST_STATUS="PASS"
BLACK_STATUS="SKIPPED"
PYLINT_STATUS="SKIPPED"
NODE_STATUS="SKIPPED"

print_excerpt() {
  local file="$1"
  if [ ! -s "$file" ]; then
    echo "(no output)"
    return
  fi
  echo "--- output (tail -n 40) ---"
  tail -n 40 "$file"
  echo "--- end output ---"
}

run_and_enforce() {
  local name="$1"
  shift
  local logfile="$LOG_DIR/${name}.log"
  echo "CMD: $*"
  set +e
  "$@" >"$logfile" 2>&1
  local ec=$?
  set -e
  echo "EXIT:$ec"
  print_excerpt "$logfile"
  if [ "$ec" -ne 0 ]; then
    TEST_STATUS="FAIL"
    exit "$ec"
  fi
}

run_advisory() {
  local name="$1"
  shift
  local logfile="$LOG_DIR/${name}.log"
  echo "CMD: $*"
  set +e
  "$@" >"$logfile" 2>&1
  local ec=$?
  set -e
  LAST_ADVISORY_EXIT="$ec"
  echo "EXIT:$ec"
  print_excerpt "$logfile"
  return 0
}

run_black_dir_advisory() {
  local name="$1"
  local dir="$2"
  local logfile="$LOG_DIR/${name}.log"
  echo "CMD: $BLACK_BIN --check $dir"
  : >"$logfile"
  local ec=0
  while IFS= read -r pyfile; do
    set +e
    "$BLACK_BIN" --check "$pyfile" >>"$logfile" 2>&1
    local file_ec=$?
    set -e
    if [ "$file_ec" -ne 0 ]; then
      ec="$file_ec"
    fi
  done < <(rg --files "$dir" -g '*.py')
  LAST_ADVISORY_EXIT="$ec"
  echo "EXIT:$ec"
  print_excerpt "$logfile"
  return 0
}

if [ -d "$ROOT_DIR/venv" ]; then
  VENV_DIR="$ROOT_DIR/venv"
elif [ -d "$ROOT_DIR/.venv" ]; then
  VENV_DIR="$ROOT_DIR/.venv"
else
  echo "CMD: python -m venv .venv"
  set +e
  python -m venv .venv >"$LOG_DIR/create_venv.log" 2>&1
  create_ec=$?
  set -e
  echo "EXIT:$create_ec"
  print_excerpt "$LOG_DIR/create_venv.log"
  if [ "$create_ec" -ne 0 ]; then
    TEST_STATUS="FAIL"
    exit "$create_ec"
  fi
  VENV_DIR="$ROOT_DIR/.venv"
fi

if [ ! -e "$ROOT_DIR/venv" ] && [ "$VENV_DIR" = "$ROOT_DIR/.venv" ]; then
  echo "CMD: ln -s .venv venv"
  set +e
  ln -s .venv venv >"$LOG_DIR/link_venv.log" 2>&1
  link_ec=$?
  set -e
  echo "EXIT:$link_ec"
  print_excerpt "$LOG_DIR/link_venv.log"
  if [ "$link_ec" -ne 0 ]; then
    TEST_STATUS="FAIL"
    exit "$link_ec"
  fi
fi

PIP_BIN="$VENV_DIR/bin/pip"
BLACK_BIN="$VENV_DIR/bin/black"
PYLINT_BIN="$VENV_DIR/bin/pylint"

run_and_enforce "pip_install" "$PIP_BIN" install -r requirements.txt
run_and_enforce "django_check" env APP_ENV=test ./run_manage.sh check
run_and_enforce "collectstatic" env APP_ENV=test ./run_manage.sh collectstatic --noinput
run_and_enforce "django_test" env APP_ENV=test ./run_manage.sh test

run_black_dir_advisory "black_check_djangoproto8" app/DjangoProto8
black_djangoproto8_ec="$LAST_ADVISORY_EXIT"
echo "BLACK_STEP app/DjangoProto8 EXIT:$black_djangoproto8_ec"
run_black_dir_advisory "black_check_api" app/api
black_api_ec="$LAST_ADVISORY_EXIT"
echo "BLACK_STEP app/api EXIT:$black_api_ec"
run_black_dir_advisory "black_check_authflow" app/authflow
black_authflow_ec="$LAST_ADVISORY_EXIT"
echo "BLACK_STEP app/authflow EXIT:$black_authflow_ec"
run_black_dir_advisory "black_check_thinking" app/thinking
black_thinking_ec="$LAST_ADVISORY_EXIT"
echo "BLACK_STEP app/thinking EXIT:$black_thinking_ec"
if [ "$black_djangoproto8_ec" -eq 0 ] && [ "$black_api_ec" -eq 0 ] && [ "$black_authflow_ec" -eq 0 ] && [ "$black_thinking_ec" -eq 0 ]; then
  BLACK_STATUS="PASS"
else
  BLACK_STATUS="FAIL (advisory)"
fi
echo "BLACK_SUMMARY: app/DjangoProto8=$black_djangoproto8_ec app/api=$black_api_ec app/authflow=$black_authflow_ec app/thinking=$black_thinking_ec status=$BLACK_STATUS"

run_advisory "pylint" "$PYLINT_BIN" app/DjangoProto8 app/api app/authflow app/thinking
pylint_ec="$LAST_ADVISORY_EXIT"
if [ "$pylint_ec" -eq 0 ]; then
  PYLINT_STATUS="PASS"
else
  PYLINT_STATUS="FAIL (advisory)"
fi

if [ -f "$ROOT_DIR/package.json" ]; then
  run_and_enforce "npm_ci" npm ci
  run_and_enforce "npm_lint" npm run lint
  run_and_enforce "npm_build" npm run build
  if npm run | rg -q "^[[:space:]]*test"; then
    run_and_enforce "npm_test" npm run test
  else
    run_and_enforce "npm_test" npm test
  fi
  NODE_STATUS="PASS"
else
  echo "No package.json detected; skipping Node pipeline."
  NODE_STATUS="SKIPPED"
fi

echo "SUMMARY: tests=$TEST_STATUS black=$BLACK_STATUS pylint=$PYLINT_STATUS node=$NODE_STATUS"
exit 0

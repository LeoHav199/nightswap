#!/bin/sh
# Runs every test suite against a throwaway database. Usage, from the project folder: sh tests/run_all.sh
cd "$(dirname "$0")/.." || exit 1
PY="${PYTHON:-../.venv/bin/python}"
export LOAD_STARTER_DATA=0  # tests start from an empty database
status=0
for t in tests/*_test.py; do
  out=$("$PY" "$t" "$PWD" 2>&1)
  passed=$(printf '%s\n' "$out" | grep -c '^PASS')
  failed=$(printf '%s\n' "$out" | grep -c '^FAIL ')
  printf '%-22s %3s passed  %s failed\n' "$(basename "$t")" "$passed" "$failed"
  if [ "$failed" != 0 ] || printf '%s\n' "$out" | grep -q Traceback; then
    printf '%s\n' "$out" | grep -E '^FAIL |Error' | head -5; status=1
  fi
done
exit $status

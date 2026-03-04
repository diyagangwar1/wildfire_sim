#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_tests.sh — run the wildfire_sim test suite
#
# Usage:
#   ./run_tests.sh            # all tests (unit + integration, ~25s)
#   ./run_tests.sh --fast     # unit tests only (skips integration, <2s)
#   ./run_tests.sh --only syntax
# ---------------------------------------------------------------------------

set -e
cd "$(dirname "$0")"

FAST=0
ONLY=""

for arg in "$@"; do
    case "$arg" in
        --fast)  FAST=1 ;;
        --only)  shift; ONLY="$1" ;;
    esac
done

if [[ -n "$ONLY" ]]; then
    python3 -m unittest "tests.test_${ONLY}" -v 2>&1
elif [[ "$FAST" -eq 1 ]]; then
    echo "[run_tests] Unit tests only (skipping integration)"
    python3 -m unittest discover -s tests -p "test_*.py" -v 2>&1
else
    echo "[run_tests] Full suite (unit + integration, ~25s)"
    RUN_INTEGRATION=1 python3 -m unittest discover -s tests -p "test_*.py" -v 2>&1
fi

#!/usr/bin/env bash
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
#
# refresh.sh -- the whole pipeline, in the order it has to run.
#
# THE ORDER IS NOT A PREFERENCE. Each step needs the one before it:
#
#   1. nsdl_daily        ratings, so holdings have something to resolve
#                        against. Second, and the day's new funds resolve
#                        against yesterday's ratings.
#   2. fund_batch        holdings, which need the ratings from step 1.
#   3. fund_batch        fills holdings from answers given in the review queue
#      --reapply         since the last run. AFTER step 2, and this is the one
#                        whose position actually matters: run it before and it
#                        fills holdings that step 2 then rewrites, so a week of
#                        someone's answers silently does nothing.
#   4. fund_rank         scores each fund against its category, which needs
#                        both the holdings from step 2 and the ratings those
#                        holdings resolved to.
#
# THE METRICS CSV IS NOT REGENERATED HERE, DELIBERATELY. get_funds_data.py
# downloads NAV history for every AMFI scheme from mfapi.in and recomputes
# Sharpe, Sortino, Calmar and rolling CAGR -- hours of work against a third
# endpoint, on data that moves slowly. Bolting it onto a job someone runs twice
# a week would make that job unrunnable. Step 4 uses whatever CSV is on disk
# and says how old it is; regenerating is a separate, deliberate act.
#
# If the CSV is absent, step 4 is SKIPPED rather than failed: ranking is the
# only optional part of this pipeline, and a missing metrics file should not
# make a successful holdings refresh look broken.
#
# SAFE TO RUN TWICE. Every step is idempotent -- NSDL writes only on a rating
# change, fund_batch skips funds finished today, reapply only fills what is
# empty, and fund_rank replaces the day's scores. A second run costs the
# fetches and changes nothing.
#
# IT DOES NOT STOP ON THE FIRST FAILURE. If ETMoney is down the NSDL ratings
# should still land, and tomorrow's run picks up the rest. `set -e` here would
# mean one flaky endpoint costs the whole refresh.
#
#   ./scripts/refresh.sh
#   ./scripts/refresh.sh --limit 50                    # a short run
#   RTP_DSN="host=db user=x dbname=y" ./scripts/refresh.sh
#   RTP_METRICS=/path/to/Fund_Metrics_Output.csv ./scripts/refresh.sh

set -uo pipefail

DSN="${RTP_DSN:-host=localhost user=postgres dbname=RetirementPlanning}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${RTP_PYTHON:-python3}"
METRICS="${RTP_METRICS:-$ROOT/Fund_Metrics_Output.csv}"
LOG_DIR="${RTP_LOG_DIR:-$ROOT/data/logs}"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
STALE_DAYS="${RTP_METRICS_STALE_DAYS:-45}"
mkdir -p "$LOG_DIR"

cd "$ROOT"
failed=0
skipped=0

step () {
  local name="$1"; shift
  local log="$LOG_DIR/${STAMP}_${name}.log"
  echo "=== $name  ($(date '+%H:%M:%S')) ==="
  if "$@" 2>&1 | tee "$log"; then
    echo "--- $name ok"
  else
    # PIPESTATUS[0] is the command's status; $? would be tee's.
    echo "--- $name FAILED (exit ${PIPESTATUS[0]}), see $log"
    failed=$((failed + 1))
  fi
  echo
}

step nsdl    "$PY" scripts/nsdl_daily.py --dsn "$DSN"
step funds   "$PY" scripts/fund_batch.py --dsn "$DSN" "$@"
step reapply "$PY" scripts/fund_batch.py --dsn "$DSN" --reapply

if [ -r "$METRICS" ]; then
  # Age reported rather than enforced: stale metrics still rank correctly
  # against each other, they are just describing an older world.
  if age_days=$(( ( $(date +%s) - $(date -r "$METRICS" +%s) ) / 86400 )); then
    if [ "$age_days" -gt "$STALE_DAYS" ]; then
      echo "NOTE: $METRICS is $age_days days old (over $STALE_DAYS)."
      echo "      Regenerate with get_funds_data.py when convenient; ranking"
      echo "      still works, it is just describing an older world."
      echo
    fi
  fi
  step rank "$PY" scripts/fund_rank.py --dsn "$DSN" --metrics "$METRICS"
else
  echo "=== rank  (skipped) ==="
  echo "no metrics file at $METRICS"
  echo "  Ranking is the only optional step here. Generate the file with"
  echo "  get_funds_data.py, or set RTP_METRICS to point at it."
  echo
  skipped=$((skipped + 1))
fi

echo "=== queue ($(date '+%H:%M:%S')) ==="
"$PY" scripts/fund_batch.py --dsn "$DSN" --queue 2>&1 | head -25

echo
if [ "$failed" -gt 0 ]; then
  echo "$failed step(s) failed; logs in $LOG_DIR"
  exit 1
fi
[ "$skipped" -gt 0 ] && echo "$skipped step(s) skipped"
echo "refresh complete, logs in $LOG_DIR"

#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="$PATH:$HERE/../bin"
export MARESTAIL_LIMIT_WAITS="${MARESTAIL_LIMIT_WAITS:-36}"
STOP_AT="${STOP_AT:-hardener}"
STAMP="$(date +%Y%m%dT%H%M)"
LOG=".marestail/runs/overnight-$STAMP.log"
SUMMARY=".marestail/runs/overnight-$STAMP.md"
mkdir -p .marestail/runs
{
  echo "# Overnight run started $(date '+%F %T') on $(git branch --show-current) at $(git rev-parse --short HEAD)"
  echo
} > "$SUMMARY"
AGENT_FLAG=()
if [ -n "${AGENT:-}" ]; then
  AGENT_FLAG=("--agent" "$AGENT")
fi
MODEL_FLAG=()
if [ -n "${MODEL:-}" ]; then
  MODEL_FLAG=("--model" "$MODEL")
fi
EFFORT_FLAG=()
if [ -n "${EFFORT:-}" ]; then
  EFFORT_FLAG=("--effort" "$EFFORT")
fi
START_FLAG=()
if [ -n "${START_FROM:-}" ]; then
  START_FLAG=("--from" "$START_FROM")
fi
for task in "$@"; do
  start=$(date +%s)
  echo "### $task ($(date '+%T'))" >> "$SUMMARY"
  marestail run "$task" "${START_FLAG[@]}" --to "$STOP_AT" --auto "${AGENT_FLAG[@]}" "${MODEL_FLAG[@]}" "${EFFORT_FLAG[@]}" >> "$LOG" 2>&1
  code=$?
  minutes=$(( ($(date +%s) - start) / 60 ))
  {
    echo "- exit $code after ${minutes} min, HEAD $(git rev-parse --short HEAD)"
    grep -E "^== |finished in|verdict" "$LOG" | tail -n 40 | sed 's/^/    /'
    sed -n '/^## Config changes/,$p' "$LOG" | tail -n 60 | sed 's/^/    /'
    echo
  } >> "$SUMMARY"
  if [ "$code" -ne 0 ]; then
    echo "stopped: $task exited $code" | tee -a "$SUMMARY"
    exit "$code"
  fi
done
echo "all tasks complete $(date '+%F %T')" | tee -a "$SUMMARY"

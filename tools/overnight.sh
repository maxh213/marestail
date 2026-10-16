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
SCOPE_FLAG=()
if [ -n "${SCOPE:-}" ]; then
  SCOPE_FLAG=("--scope" "$SCOPE")
fi
FOCUS_FLAG=()
for path in ${FOCUS:-}; do
  FOCUS_FLAG+=("--focus" "$path")
done

run_task() {
  local task=$1
  local use_start=$2
  local start_flag=()
  if [ "$use_start" = 1 ]; then
    start_flag=("${START_FLAG[@]}")
  fi
  local start code minutes
  start=$(date +%s)
  echo "### $task ($(date '+%T'))" >> "$SUMMARY"
  marestail run "$task" "${start_flag[@]}" "${SCOPE_FLAG[@]}" "${FOCUS_FLAG[@]}" --to "$STOP_AT" --auto "${AGENT_FLAG[@]}" "${MODEL_FLAG[@]}" "${EFFORT_FLAG[@]}" >> "$LOG" 2>&1
  code=$?
  minutes=$(( ($(date +%s) - start) / 60 ))
  {
    echo "- exit $code after ${minutes} min, HEAD $(git rev-parse --short HEAD)"
    grep -E "^== |finished in|verdict|dandelion/route" "$LOG" | tail -n 40 | sed 's/^/    /'
    sed -n '/^## Config changes/,/^## Performance changes/{/^## Performance changes/!p;}' "$LOG" | tail -n 60 | sed 's/^/    /'
    sed -n '/^## Performance changes/,$p' "$LOG" | tail -n 40 | sed 's/^/    /'
    echo
  } >> "$SUMMARY"
  if [ "$code" -ne 0 ]; then
    echo "stopped: $task exited $code" | tee -a "$SUMMARY"
    if ! marestail push >> "$LOG" 2>&1; then
      echo "stopped: push failed" | tee -a "$SUMMARY"
      return 1
    fi
    return "$code"
  fi
}

publish_branch() {
  if ! marestail push >> "$LOG" 2>&1; then
    echo "stopped: push failed" | tee -a "$SUMMARY"
    return 1
  fi
}

sync_upstream() {
  publish_branch || return $?
  if ! marestail upstream >> "$LOG" 2>&1; then
    echo "stopped: upstream push failed" | tee -a "$SUMMARY"
    return 1
  fi
  local round=0
  while [ "$round" -lt 3 ]; do
    round=$((round + 1))
    local extra=()
    mapfile -t extra < <(marestail upstream drain 2>> "$LOG")
    if [ "${#extra[@]}" -eq 0 ]; then
      break
    fi
    local task
    for task in "${extra[@]}"; do
      run_task "$task" 0 || return $?
      publish_branch || return $?
    done
    if ! marestail upstream >> "$LOG" 2>&1; then
      echo "stopped: upstream push failed" | tee -a "$SUMMARY"
      return 1
    fi
  done
}

for task in "$@"; do
  sync_upstream || exit $?
  run_task "$task" 1 || exit $?
  sync_upstream || exit $?
done
echo "all tasks complete $(date '+%F %T')" | tee -a "$SUMMARY"

# Sourced by the shims beside it. A machine that offloads tests puts this folder first on PATH (marestail does
# it for every agent it starts; `marestail shims-path` prints it for anyone else), so a test run started by
# name goes to the remote box through `marestail offload`. Anything else, every run with MARESTAIL_NO_OFFLOAD=1
# (set by marestail's own gate runs and by every job on the box) and every run with MARESTAIL_REMOTE=0 execs
# the real tool: the next one on PATH once every shim folder is taken off it. MARESTAIL_REMOTE=0 counts only
# while `marestail remote policy` says "allowed"; under [remote] local = "never" the test run goes to the box.
shim_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
marestail_bin="$shim_dir/../../bin/marestail"

strip_shims() {
  local part kept="" parts
  IFS=: read -ra parts <<< "$PATH"
  for part in "${parts[@]}"; do
    [ -f "$part/_offload.sh" ] && continue
    kept="${kept:+$kept:}$part"
  done
  export PATH="$kept"
}

remote_off() {
  case "${MARESTAIL_REMOTE,,}" in
    0|off|false|no) return 0 ;;
  esac
  return 1
}

local_allowed() {
  [ "$("$marestail_bin" remote policy 2>/dev/null)" = allowed ]
}

runs_here() {
  [ "${MARESTAIL_NO_OFFLOAD:-}" = 1 ] || { remote_off && local_allowed; }
}

# WORD is the first argument that is not an option, REST what follows it; $1 lists the options that take a value.
next_word() {
  local valued=" $1 "
  shift
  WORD=""
  REST=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --) shift; [ $# -gt 0 ] && { WORD=$1; shift; REST=("$@"); }; return 0 ;;
      -*) if [[ "$valued" == *" $1 "* ]]; then shift; fi; [ $# -gt 0 ] && shift ;;
      *) WORD=$1; shift; REST=("$@"); return 0 ;;
    esac
  done
}

test_script() {
  case "$1" in
    test*) return 0 ;;
  esac
  return 1
}

run_script() {
  test_script "$1" || [ "$1" = check ]
}

runner_call() {
  local name=${1%%@*}
  [ -z "$name" ] && name=$1
  name=${name##*/}
  shift
  case "$name" in
    vitest|jest) return 0 ;;
    playwright) next_word "" "$@"; [ "$WORD" = test ] ;;
    stryker) next_word "" "$@"; [ "$WORD" = run ] ;;
    *) return 1 ;;
  esac
}

pnpm_test() {
  next_word "-C --dir -F --filter --reporter --loglevel --workspace-concurrency" "$@"
  local word=$WORD rest=("${REST[@]}")
  case "$word" in
    run|run-script) next_word "" "${rest[@]}"; run_script "$WORD" ;;
    exec|dlx) next_word "" "${rest[@]}"; runner_call "$WORD" "${REST[@]}" ;;
    t) return 0 ;;
    *) run_script "$word" || runner_call "$word" "${rest[@]}" ;;
  esac
}

npm_test() {
  next_word "-w --workspace --prefix --loglevel" "$@"
  local word=$WORD rest=("${REST[@]}")
  case "$word" in
    test|t|tst) return 0 ;;
    run|run-script|rum|urn) next_word "" "${rest[@]}"; run_script "$WORD" ;;
    exec|x) next_word "-p --package -c --call" "${rest[@]}"; runner_call "$WORD" "${REST[@]}" ;;
    *) return 1 ;;
  esac
}

npx_test() {
  next_word "-p --package -c --call" "$@"
  runner_call "$WORD" "${REST[@]}"
}

yarn_test() {
  next_word "--cwd" "$@"
  local word=$WORD rest=("${REST[@]}")
  case "$word" in
    run) next_word "" "${rest[@]}"; run_script "$WORD" ;;
    exec|dlx) next_word "" "${rest[@]}"; runner_call "$WORD" "${REST[@]}" ;;
    *) test_script "$word" || runner_call "$word" "${rest[@]}" ;;
  esac
}

offloads() {
  local tool=$1
  shift
  case "$tool" in
    pnpm) pnpm_test "$@" ;;
    npm) npm_test "$@" ;;
    npx) npx_test "$@" ;;
    yarn) yarn_test "$@" ;;
    vitest|jest|playwright|stryker) runner_call "$tool" "$@" ;;
    mutmut) next_word "" "$@"; [ "$WORD" = run ] ;;
    mix) next_word "" "$@"; [ "$WORD" = muex ] ;;
    mvn|mvnw) [[ " $* " == *pitest* ]] ;;
    *) return 1 ;;
  esac
}

offload_or_exec() {
  local tool=$1
  shift
  strip_shims
  if offloads "$tool" "$@" && ! runs_here; then
    exec "$marestail_bin" offload -- "$tool" "$@"
  fi
  exec "$tool" "$@"
}

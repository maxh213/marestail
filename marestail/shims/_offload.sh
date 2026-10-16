# Sourced by the shims beside it. The runner puts this directory first on every agent's PATH, so a mutation
# tool an agent starts by name goes to the spot box through `marestail offload`, whatever shell the agent
# spawns. Anything else, and a run marestail itself started on this machine (MARESTAIL_MUTATION_LOCAL=1),
# execs the real tool with this directory taken off PATH.
shim_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
marestail_bin="$shim_dir/../../bin/marestail"

strip_shims() {
  local part kept=""
  local IFS=":"
  for part in $PATH; do
    [ "$part" = "$shim_dir" ] && continue
    kept="${kept:+$kept:}$part"
  done
  PATH="$kept"
  export PATH
}

mutation_run() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      stryker|mutmut|muex|*pitest*) return 0 ;;
    esac
  done
  return 1
}

offload_or_exec() {
  strip_shims
  if [ "${MARESTAIL_MUTATION_LOCAL:-}" = "1" ] || ! mutation_run "$@"; then
    exec "$@"
  fi
  exec "$marestail_bin" offload -- "$@"
}

# Sourced from BASH_ENV for every agent shell. Mutation tools run on the spot box.
_mare_bin="$(cd "$(dirname "${BASH_SOURCE[0]}")/../bin" && pwd)/marestail"

_mare_mutation() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      stryker|mutmut|muex|*pitest*) return 0 ;;
    esac
  done
  return 1
}

npx() {
  if _mare_mutation "$@"; then "$_mare_bin" offload -- npx "$@"; return $?; fi
  command npx "$@"
}
npm() {
  if _mare_mutation "$@"; then "$_mare_bin" offload -- npm "$@"; return $?; fi
  command npm "$@"
}
pnpm() {
  if _mare_mutation "$@"; then "$_mare_bin" offload -- pnpm "$@"; return $?; fi
  command pnpm "$@"
}
yarn() {
  if _mare_mutation "$@"; then "$_mare_bin" offload -- yarn "$@"; return $?; fi
  command yarn "$@"
}
stryker() { "$_mare_bin" offload -- stryker "$@"; }
mutmut() { "$_mare_bin" offload -- mutmut "$@"; }
mix() {
  if [ "${1:-}" = "muex" ]; then "$_mare_bin" offload -- mix "$@"; return $?; fi
  command mix "$@"
}
mvn() {
  if _mare_mutation "$@"; then "$_mare_bin" offload -- mvn "$@"; return $?; fi
  command mvn "$@"
}
mvnw() {
  if _mare_mutation "$@"; then "$_mare_bin" offload -- mvnw "$@"; return $?; fi
  command mvnw "$@"
}

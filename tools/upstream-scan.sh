#!/usr/bin/env bash
# One level of ~/workspace/*/marestail.toml. Skips main, master, and a detached HEAD.
# Publishes only the checked-out branch, then merges [git] base into it.
set -uo pipefail
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  nvm use --delete-prefix v22.23.2 --silent
  export PATH="$NVM_DIR/versions/node/v22.23.2/bin:$PATH"
fi
export PATH="/home/max/workspace/marestail/bin:$HOME/.local/bin:$PATH"
unset AGENT EFFORT START_FROM SCOPE FOCUS
export MODEL=dandelion/route

if [ "$#" -gt 0 ]; then
  repos=("$@")
else
  repos=()
  shopt -s nullglob
  for toml in "$HOME"/workspace/*/marestail.toml; do
    repos+=("$(dirname "$toml")")
  done
fi

eligible() {
  local repo=$1
  [ -f "$repo/marestail.toml" ] || return 1
  git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 1
  local branch
  branch=$(git -C "$repo" branch --show-current 2>/dev/null || true)
  case "$branch" in
    ""|main|master) return 1 ;;
  esac
  printf '%s\n' "$branch"
}

failed=0
ready=()
if [ "${#repos[@]}" -eq 0 ]; then
  exit 0
fi
for repo in "${repos[@]}"; do
  branch=$(eligible "$repo") || continue
  echo "upstream $repo ($branch)"
  if ! (cd "$repo" && marestail push && marestail upstream); then
    echo "upstream failed in $repo" >&2
    failed=1
    continue
  fi
  ready+=("$repo")
done

drain_repo() {
  local repo=$1 extra=() stop=hardener
  cd "$repo" || return 1
  mapfile -t extra < <(marestail upstream drain)
  if [ "${#extra[@]}" -eq 0 ]; then
    return 0
  fi
  if grep -Eq '^[[:space:]]*\[qa\][[:space:]]*$' marestail.toml; then
    stop=qa
  fi
  echo "drain $repo -> $stop: ${extra[*]}"
  STOP_AT=$stop systemd-inhibit --what=idle --who=marestail --why="upstream $(basename "$repo")" \
    bash /home/max/workspace/marestail/tools/overnight.sh "${extra[@]}"
}

if [ "${#ready[@]}" -gt 0 ]; then
  for repo in "${ready[@]}"; do
    drain_repo "$repo" || failed=1
  done
fi
exit "$failed"

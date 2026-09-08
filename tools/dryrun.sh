#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${1:-/tmp/marestail-dryrun}"
PLAN="${2:-$HERE/dryrun-plan.txt}"
rm -rf "$WORK/repo" && mkdir -p "$WORK/repo" && cd "$WORK/repo"
git init -q -b main && git config user.email dryrun@marestail && git config user.name dryrun
printf '[git]\nbase = "main"\n' > marestail.toml
printf '.marestail/\n' > .gitignore
mkdir tasks && echo "# Add one" > tasks/t.md
git add -A && git commit -qm init
cp "$PLAN" "$WORK/plan.txt"
export PATH="$PATH:$HERE/../bin" STUB_PLAN="$WORK/plan.txt" MARESTAIL_CLAUDE="$HERE/stub-claude"
marestail run tasks/t.md --auto
echo "remaining plan lines: $(wc -l < "$WORK/plan.txt")"

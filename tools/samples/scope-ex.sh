#!/usr/bin/env bash
set -euo pipefail

WORKTREE="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
ROOT="$(mktemp -d /tmp/marestail-scope-ex.XXXXXX)"
trap 'rm -rf "$ROOT"' EXIT
failures=0

mix new "$ROOT/app" --module Sample >/dev/null
cd "$ROOT/app"
git init -q
git config user.email fixture@example.com
git config user.name Fixture
printf '.marestail/\n' >> .gitignore

cat > lib/grime.ex <<'EOF'
defmodule Sample.Grime do
  def messy( x ) do
    unused = 5
    if x > 1 do
      Sample.GrimeHelper.bounce(x)
    else
      x
    end
  end
end
EOF

cat > lib/grime_helper.ex <<'EOF'
defmodule Sample.GrimeHelper do
  def bounce(x) do
    Sample.Grime.messy(x)
  end
end
EOF

cat > marestail.toml <<'EOF'
[git]
base = "HEAD"

[elixir]
root = "."
crap_max = 4
EOF

git add -A
git commit -qm base

cat > lib/clean.ex <<'EOF'
defmodule Sample.Clean do
  def double(x) do
    x * 2
  end

  def describe(x) do
    if x > 0 do
      "positive"
    else
      "non-positive"
    end
  end
end
EOF

cat > test/clean_test.exs <<'EOF'
defmodule Sample.CleanTest do
  use ExUnit.Case, async: true

  test "double" do
    assert Sample.Clean.double(3) == 6
  end

  test "describe both arms" do
    assert Sample.Clean.describe(1) == "positive"
    assert Sample.Clean.describe(-1) == "non-positive"
  end
end
EOF

mix format lib/clean.ex test/clean_test.exs

gate() {
  "$WORKTREE/bin/marestail" gate --tier fast --only ex.tests,ex.crap,ex.lint,ex.deps "$@"
}

check() {
  label="$1"
  expected="$2"
  actual="$3"
  if [ "$actual" -eq "$expected" ]; then
    if [ "$actual" -eq 0 ]; then
      echo "== $label: PASS (expected PASS)"
    else
      echo "== $label: FAIL (expected FAIL)"
    fi
  else
    echo "== $label: UNEXPECTED exit $actual (wanted $expected)"
    failures=$((failures + 1))
  fi
}

set +e
out="$(gate --scope changed 2>&1)"
code=$?
set -e
echo "$out"
check scoped 0 "$code"
if ! echo "$out" | grep -q "scope: changed"; then
  echo "== scoped: report is missing the scope line"
  failures=$((failures + 1))
fi

set +e
out="$(gate --scope all 2>&1)"
code=$?
set -e
echo "$out"
check all 1 "$code"

cat > lib/clean.ex <<'EOF'
defmodule Sample.Clean do
  def double(x) do
    x * 2
  end

  def describe(x) do
    if x > 0 do
      "positive"
    else
      "non-positive"
    end
  end

  def uncovered_math(x) do
    if x > 10 do
      x * 2
    else
      x
    end
  end
end
EOF

mix format lib/clean.ex

set +e
out="$(gate --scope changed 2>&1)"
code=$?
set -e
echo "$out"
check dirtied-scoped 1 "$code"

python3 - "$WORKTREE" <<'EOF'
import sys

sys.path.insert(0, sys.argv[1])
from marestail.gates.ex_crap import in_hunks
from marestail.gates.ex_deps import parse_cycles
from marestail.gates.ex_lint import scoped_blocks

assert in_hunks({"line": 10, "end_line": 20}, {15})
assert not in_hunks({"line": 10, "end_line": 20}, {21})
assert in_hunks({"line": 7}, {7})
assert in_hunks({"line": 7}, None)

output = "Compiling 2 files (.ex)\n    warning: a is unused\n    |\n    \u2514\u2500 lib/a.ex:3:5: A.f/1\n\n    warning: b is unused\n    |\n    \u2514\u2500 lib/b.ex:4:5: B.g/1\n\ndone\n"
blocks = scoped_blocks(output, ["lib/b.ex"])
assert len(blocks) == 1 and "lib/b.ex" in blocks[0] and "lib/a.ex" not in blocks[0], blocks

xref = "1 cycles found. Showing them in decreasing size:\n\nCycle of length 2:\n\n    lib/a.ex\n    lib/b.ex\n\n** (Mix) Too many cycles (found: 1, permitted: 0)\n"
assert parse_cycles(xref) == [["lib/a.ex", "lib/b.ex"]]
assert parse_cycles("no cycles here") == []
print("unit checks OK (hunk-function mapping, compile block filter, cycle parse)")
EOF

echo "note: ex.mutation is a full-tier gate and muex is not installed here; its scoping is the --files argument built from the same scoped_sources helper exercised above"

if [ "$failures" -gt 0 ]; then
  echo "scope-ex fixture FAILED ($failures unexpected verdicts)"
  exit 1
fi
echo "scope-ex fixture OK"

#!/usr/bin/env bash
set -euo pipefail

MARESTAIL="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
REPO="/tmp/scope-py-fixture"
VENV="$REPO/.venv"

fail() {
    echo "scope-py.sh: FAIL: $1" >&2
    exit 1
}

rm -rf "$REPO"
mkdir -p "$REPO/app" "$REPO/core" "$REPO/tests"
cd "$REPO"

if command -v uv >/dev/null 2>&1; then
    uv venv "$VENV" --quiet
    uv pip install --python "$VENV/bin/python" --quiet pytest pytest-cov radon ruff mypy vulture import-linter
else
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet pytest pytest-cov radon ruff mypy vulture import-linter
fi

cat > marestail.toml <<'EOF'
[git]
base = "base"

[python]
root = "."
sources = ["app", "core"]

[deadcode]
python_kinds = ["unreachable code"]
EOF

cat > pyproject.toml <<'EOF'
[tool.pytest.ini_options]
pythonpath = ["."]

[tool.mypy]
files = ["app", "core", "tests"]

[tool.importlinter]
root_packages = ["app", "core"]

[[tool.importlinter.contracts]]
name = "core must not import app"
type = "forbidden"
source_modules = ["core"]
forbidden_modules = ["app"]
EOF

cat > .gitignore <<'EOF'
.venv/
.marestail/
.mypy_cache/
.ruff_cache/
.coverage
__pycache__/
EOF

touch app/__init__.py core/__init__.py

cat > app/helpers.py <<'EOF'
def assist(x):
    return x + 1
EOF

cat > app/clean.py <<'EOF'
def existing(x):
    return x * 2


def legacy(flag):
    if flag:
        return "yes"
    return "no"
EOF

cat > core/dirty.py <<'EOF'
import os

from app.helpers import assist


# FIXME: this module predates the gates and is allowed to stay dirty
def used_helper(x):
    return assist(x) * 10


def nasty( value ):
    total = 0
    for item in value:
        if item % 2 == 0:
            total += item
        else:
            total -= item
    return total
    print("unreachable")
EOF

cat > tests/test_app.py <<'EOF'
from app.clean import existing
from core.dirty import used_helper


def test_existing():
    assert existing(3) == 6


def test_used_helper():
    assert used_helper(1) == 20
EOF

"$VENV/bin/ruff" format --quiet app tests

git init -q -b main
git config user.email fixture@example.com
git config user.name fixture
git add -A
git commit -q -m base
git branch base

cat >> app/clean.py <<'EOF'


def shipped(score):
    if score >= 90:
        return "gold"
    if score >= 50:
        return "silver"
    return "bronze"
EOF

cat >> tests/test_app.py <<'EOF'


def test_shipped():
    assert shipped(95) == "gold"
    assert shipped(60) == "silver"
    assert shipped(10) == "bronze"
EOF

sed -i 's/^from app.clean import existing$/from app.clean import existing, shipped/' tests/test_app.py
"$VENV/bin/ruff" format --quiet app/clean.py tests/test_app.py
cp app/clean.py /tmp/scope-py-clean-v2.py

run_gate() {
    set +e
    OUTPUT="$(python3 "$MARESTAIL/marestail/cli.py" gate --tier fast "$@" 2>&1)"
    CODE=$?
    set -e
}

expect() {
    local want_code="$1" want_word="$2" label="$3"
    local got_word="FAIL"
    [ "$CODE" -eq 0 ] && got_word="PASS"
    if [ "$CODE" -ne "$want_code" ] || [ "$got_word" != "$want_word" ]; then
        echo "$OUTPUT"
        fail "$label: expected exit $want_code/$want_word, got exit $CODE/$got_word"
    fi
    echo "verdict: $label -> $got_word (exit $CODE, expected)"
}

echo "scope-py fixture: $REPO"

run_gate --scope changed
expect 0 PASS "--scope changed with a clean change"
echo "$OUTPUT" | grep -q "GATE PASSED" || fail "scoped run did not print GATE PASSED"
echo "$OUTPUT" | grep -q "scope: changed" || fail "scoped run did not print the scope line"
if echo "$OUTPUT" | grep -q "core/dirty.py"; then
    fail "scoped run reported pre-existing core/dirty.py"
fi

run_gate --scope all
expect 1 FAIL "--scope all on the same repo"
echo "$OUTPUT" | grep -q "core/dirty.py" || fail "--scope all did not report core/dirty.py"
echo "$OUTPUT" | grep -q "not covered" || fail "--scope all did not report coverage gaps"

cat >> app/clean.py <<'EOF'


def bonus(x):
    if x > 100:
        return "huge"
    return "small"
EOF
"$VENV/bin/ruff" format --quiet app/clean.py

run_gate --scope changed
expect 1 FAIL "--scope changed after dirtying the changed module"
echo "$OUTPUT" | grep -q "app/clean.py" || fail "dirtied scoped run did not report app/clean.py"
echo "$OUTPUT" | grep -q "not covered" || fail "dirtied scoped run did not report the uncovered new lines"

cp /tmp/scope-py-clean-v2.py app/clean.py

cat > app/clean.py <<'EOF'
def existing(x):
    return x * 2


def shipped(score):
    if score >= 90:
        return "gold"
    if score >= 50:
        return "silver"
    return "bronze"
EOF

cat >> app/helpers.py <<'EOF'


def legacy(flag):
    if flag:
        return "yes"
    return "no"
EOF
"$VENV/bin/ruff" format --quiet app/clean.py app/helpers.py

run_gate --scope changed
expect 1 FAIL "--scope changed with legacy moved into existing app/helpers.py"
echo "$OUTPUT" | grep -q "app/helpers.py" || fail "moved function was not gated in app/helpers.py"

echo "scope-py.sh: OK (scoped PASS / all FAIL / dirtied FAIL / moved function gated in new file)"

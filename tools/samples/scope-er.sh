#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
WORKTREE="$(pwd)"

python3 - <<'EOF'
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from marestail.config import Config
from marestail.context import Context
from marestail.gates import er_crap, er_lint, er_tests


def ctx_for(root: Path, changed: set[str], lines: dict[str, set[int]]) -> Context:
    return Context(config=Config(root=root, raw={}), scope_changed=True, changed=changed, focus=set(), changed_lines_map=lines)


with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    target = root / "src" / "a.erl"
    target.parent.mkdir()
    target.write_text("-module(a).\n\nfirst(X) ->\n    X + 1.\n\nsecond(Y) ->\n    Y + 2.\n")
    ctx = ctx_for(root, {"src/a.erl"}, {"src/a.erl": {4}})
    coverage = {
        "files": {
            str(root / "src" / "a.erl"): {"missing_lines": [2, 4, 6]},
            str(root / "src" / "b.erl"): {"missing_lines": [9]},
        }
    }
    assert er_tests.coverage_findings(coverage, ctx) == ["src/a.erl:4 not covered"]
    unscoped = Context(config=Config(root=root, raw={}))
    assert er_tests.coverage_findings(coverage, unscoped) == ["src/a.erl:2 not covered", "src/a.erl:4 not covered", "src/a.erl:6 not covered", "src/b.erl:9 not covered"]
    functions = [
        {"file": str(target), "line": 3, "name": "first/1", "complexity": 1},
        {"file": str(target), "line": 6, "name": "second/1", "complexity": 1},
    ]
    kept = er_crap.scoped_functions(functions, ctx)
    assert [fn["name"] for fn in kept] == ["first/1"], kept
    ctx_all = ctx_for(root, {"src/a.erl"}, {"src/a.erl": {6}})
    kept = er_crap.scoped_functions(functions, ctx_all)
    assert [fn["name"] for fn in kept] == ["second/1"], kept
    lint_output = "src/dirty.erl:12:5: variable 'Unused' is unused\nsrc/a.erl:3: warning\n"
    assert er_lint.lint_findings(lint_output, ctx) == ["src/a.erl:3 warning"]
    assert len(er_lint.lint_findings(lint_output, unscoped)) == 2
    raw = "escript: some global crash\n"
    assert er_lint.lint_findings(raw, ctx) == ["escript: some global crash"]

print("er unit assertions OK")
EOF

FIXTURE="$(mktemp -d /tmp/marestail-scope-er.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT
cd "$FIXTURE"

mkdir -p src test
cat > .gitignore <<'EOF'
.marestail/
EOF
cat > marestail.toml <<'EOF'
[git]
base = "HEAD"

[erlang]
root = "."
crap_max = 4
EOF
cat > src/dirty.erl <<'EOF'
-module(dirty).

-export([ping/0, tag/1, classify/1, describe/1]).

ping() ->
    pong.

tag(Box) ->
    case Box of
        empty ->
            empty;
        Value ->
            {tagged, Value}
    end.

classify(N) when is_integer(N) ->
    if
        N > 10 ->
            if
                N > 20 ->
                    if
                        N > 30 ->
                            if
                                N > 40 -> huge;
                                true -> big
                            end;
                        true -> mid
                    end;
                true -> small
            end;
        true -> tiny
    end.

describe(Thing) ->
    Unused = dirty_helper:help(Thing),
    ok.
EOF
cat > src/dirty_helper.erl <<'EOF'
-module(dirty_helper).

-export([help/1]).

help(Thing) ->
    dirty:ping(),
    Thing.
EOF
cat > src/clean.erl <<'EOF'
-module(clean).

-export([double/1]).

double(N) ->
    N * 2.
EOF
cat > test/clean_tests.erl <<'EOF'
-module(clean_tests).

-include_lib("eunit/include/eunit.hrl").

double_test() ->
    ?assertEqual(4, clean:double(2)).
EOF

git init -q .
git add .
git -c user.email=fixture@test -c user.name=fixture commit -qm base

cat > src/fresh.erl <<'EOF'
-module(fresh).

-export([triple/1]).

triple(N) ->
    N + N + N.
EOF
cat >> test/clean_tests.erl <<'EOF'

triple_test() ->
    ?assertEqual(9, fresh:triple(3)).
EOF

GATES="er.tests,er.crap,er.lint,er.deps"
run_gate() {
    python3 "$WORKTREE/marestail/cli.py" gate --tier fast --only "$GATES" "$@"
}

if run_gate --scope changed > scoped.log 2>&1; then
    echo "VERDICT 1: scoped PASS (expected)"
else
    echo "VERDICT 1: scoped FAIL (unexpected)" >&2
    cat scoped.log >&2
    exit 1
fi

if run_gate --scope all > all.log 2>&1; then
    echo "VERDICT 2: all-scope PASS (unexpected)" >&2
    cat all.log >&2
    exit 1
else
    echo "VERDICT 2: all-scope FAIL (expected)"
fi

cat >> src/fresh.erl <<'EOF'

quad(N) ->
    N * 4.
EOF
sed -i 's/-export(\[triple\/1\])\./-export([triple\/1, quad\/1])./' src/fresh.erl

if run_gate --scope changed > dirtied.log 2>&1; then
    echo "VERDICT 3: dirtied-scope PASS (unexpected)" >&2
    cat dirtied.log >&2
    exit 1
else
    grep -q "src/fresh.erl" dirtied.log || { cat dirtied.log >&2; exit 1; }
    echo "VERDICT 3: dirtied-scope FAIL on src/fresh.erl (expected)"
fi

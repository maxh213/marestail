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
from marestail.gates import ts_deps, ts_lint, ts_mutation, ts_tests
from marestail.gates import ts_crap


def ctx_for(root: Path, changed: set[str], lines: dict[str, set[int]]) -> Context:
    return Context(config=Config(root=root, raw={}), scope_changed=True, changed=changed, focus=set(), changed_lines_map=lines)


with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "tsconfig.app.json").write_text("{}")
    ctx = ctx_for(root, {"src/a.ts"}, {"src/a.ts": {3, 10}})
    unscoped = Context(config=Config(root=root, raw={}))
    istanbul = {
        str(root / "src" / "a.ts"): {
            "statementMap": {
                "0": {"start": {"line": 2}},
                "1": {"start": {"line": 3}},
                "2": {"start": {"line": 7}},
            },
            "s": {"0": 0, "1": 0, "2": 0},
            "branchMap": {
                "0": {"loc": {"start": {"line": 10}}, "locations": [{"start": {"line": 11}}, {"start": {"line": 13}}]},
                "1": {"loc": {"start": {"line": 20}}, "locations": [{"start": {"line": 21}}, {"start": {"line": 10}}]},
            },
            "b": {"0": [0, 1], "1": [0, 0]},
        },
        str(root / "src" / "b.ts"): {
            "statementMap": {"0": {"start": {"line": 5}}},
            "s": {"0": 0},
            "branchMap": {},
            "b": {},
        },
    }
    scoped = ts_tests.coverage_findings(istanbul, ctx)
    assert scoped == ["src/a.ts:3 not covered", "src/a.ts:10 branch arm 0 not taken", "src/a.ts:20 branch arm 1 not taken"], scoped
    whole = ts_tests.coverage_findings(istanbul, unscoped)
    assert len(whole) == 7, whole
    fn_changed = {"file": str(root / "src" / "a.ts"), "line": 2, "endLine": 5, "name": "f", "complexity": 1}
    fn_untouched = {"file": str(root / "src" / "a.ts"), "line": 20, "endLine": 25, "name": "g", "complexity": 1}
    assert ts_crap.touches_hunk(fn_changed, ctx)
    assert not ts_crap.touches_hunk(fn_untouched, ctx)
    assert ts_crap.touches_hunk(fn_untouched, unscoped)
    report = {"files": {str(root / "src" / "a.ts"): {"mutants": [{"status": "Survived", "mutatorName": "Eq", "location": {"start": {"line": 3}}, "replacement": "x"}]},
                        str(root / "src" / "b.ts"): {"mutants": [{"status": "Survived", "mutatorName": "Eq", "location": {"start": {"line": 1}}, "replacement": "y"}]}}}
    survivors = ts_mutation.surviving(report, ctx)
    assert survivors == ["src/a.ts:3 Eq Survived: x"], survivors
    assert len(ts_mutation.surviving(report, unscoped)) == 2
    err = "  error no-circular: src/dirty.ts → src/helper.ts\n  warn no-orphans: src/a.ts\nx 2 dependency violations. 3 modules cruised.\n"
    assert ts_deps.scoped_findings(err, ctx) == ["warn no-orphans: src/a.ts"]
    chain = "  error no-circular: src/dirty.ts → \n      src/helper.ts →\n      src/dirty.ts\n\nx 1 dependency violations (1 errors, 0 warnings). 2 modules cruised.\n"
    assert ts_deps.scoped_findings(chain, ctx) == []
    chain_in_scope = "  error no-circular: src/a.ts → \n      src/helper.ts →\n      src/a.ts\nx 1 dependency violations. 2 modules cruised.\n"
    assert ts_deps.scoped_findings(chain_in_scope, ctx) == ["error no-circular: src/a.ts →"]
    crash = "Error: ENOENT no config found\n"
    assert ts_deps.scoped_findings(crash, ctx) == ["Error: ENOENT no config found"]

    class FakeRun:
        def __init__(self, output: str) -> None:
            self.output = output

    tsc_out = "src/dirty.ts(4,10): error TS2304: Cannot find name 'missingGlobal'.\nsrc/a.ts(3,5): error TS2322: Type 'x' is not assignable.\n"
    original_run = ts_lint.run
    ts_lint.run = lambda *a, **k: (1, tsc_out)
    try:
        assert ts_lint.tsc_findings(ctx) == ["src/a.ts:3 error TS2322: Type 'x' is not assignable."]
        assert len(ts_lint.tsc_findings(unscoped)) == 2
        ts_lint.run = lambda *a, **k: (1, "error TS5058: bad tsconfig path\n")
        assert ts_lint.tsc_findings(ctx) == ["tsc: error TS5058: bad tsconfig path"]
        eslint_out = '[{"filePath":"%s","messages":[{"ruleId":"no-unused-vars","message":"x is defined but never used","line":2}]},{"filePath":"%s","messages":[]}]' % (root / "src" / "dirty.ts", root / "src" / "a.ts")
        ts_lint.run = lambda *a, **k: (1, eslint_out)
        assert ts_lint.eslint_findings(ctx) == []
        assert len(ts_lint.eslint_findings(unscoped)) == 1
        ts_lint.run = lambda *a, **k: (2, "Oops! Something went wrong!")
        assert ts_lint.eslint_findings(ctx) == ["eslint: Oops! Something went wrong!"]
    finally:
        ts_lint.run = original_run

print("ts unit assertions OK")
EOF

FIXTURE="$(mktemp -d /tmp/marestail-scope-ts.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT
cd "$FIXTURE"

mkdir -p src
cat > .gitignore <<'EOF'
node_modules/
.marestail/
EOF
cat > package.json <<'EOF'
{
  "name": "scope-ts-fixture",
  "private": true,
  "type": "module",
  "scripts": {"test": "vitest run"}
}
EOF
cat > tsconfig.app.json <<'EOF'
{
  "compilerOptions": {
    "target": "es2022",
    "module": "esnext",
    "moduleResolution": "bundler",
    "strict": false,
    "noEmit": true,
    "types": []
  },
  "include": ["src"]
}
EOF
cat > eslint.config.js <<'EOF'
export default [
  {files: ["src/**/*.ts"], rules: {"no-unused-vars": "error"}}
];
EOF
cat > .dependency-cruiser.cjs <<'EOF'
module.exports = {
  forbidden: [
    {name: "no-circular", severity: "error", from: {}, to: {circular: true}}
  ]
};
EOF
cat > vitest.config.ts <<'EOF'
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    coverage: {
      provider: "v8",
      include: ["src/**/*.ts"],
      exclude: ["src/**/*.test.ts"]
    }
  }
});
EOF
cat > marestail.toml <<'EOF'
[git]
base = "HEAD"

[ts]
root = "."
crap_max = 4
EOF
cat > src/dirty.ts <<'EOF'
import { helperValue } from "./helper";

export const unusedLocal = 1;

export function classify(n) {
  if (n > 10) {
    if (n > 20) {
      if (n > 30) {
        if (n > 40) {
          return "huge";
        }
        return "big";
      }
      return "mid";
    }
    return "small";
  }
  return "tiny";
}

export function neverCalled() {
  missingGlobal();
  return helperValue;
}
EOF
cat > src/helper.ts <<'EOF'
import { classify } from "./dirty";

export const helperValue = classify(1);
EOF
cat > src/clean.ts <<'EOF'
export function double(n) {
  return n * 2;
}
EOF
cat > src/clean.test.ts <<'EOF'
import { describe, expect, it } from "vitest";
import { double } from "./clean";

describe("clean", () => {
  it("doubles", () => {
    expect(double(2)).toBe(4);
  });
});
EOF

git init -q .
git add .
git -c user.email=fixture@test -c user.name=fixture commit -qm base

npm install --no-audit --no-fund --silent -D vitest @vitest/coverage-v8 typescript@5 eslint dependency-cruiser > npm-install.log 2>&1

cat > src/fresh.ts <<'EOF'
export function triple(n) {
  return n + n + n;
}
EOF
cat > src/fresh.test.ts <<'EOF'
import { describe, expect, it } from "vitest";
import { triple } from "./fresh";

describe("fresh", () => {
  it("triples", () => {
    expect(triple(3)).toBe(9);
  });
});
EOF

GATES="ts.tests,ts.crap,ts.lint,ts.deps"
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

cat >> src/fresh.ts <<'EOF'

export function quad(n) {
  return n * 4;
}
EOF

if run_gate --scope changed > dirtied.log 2>&1; then
    echo "VERDICT 3: dirtied-scope PASS (unexpected)" >&2
    cat dirtied.log >&2
    exit 1
else
    grep -q "src/fresh.ts" dirtied.log || { cat dirtied.log >&2; exit 1; }
    echo "VERDICT 3: dirtied-scope FAIL on src/fresh.ts (expected)"
fi

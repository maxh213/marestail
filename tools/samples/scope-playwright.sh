#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
WORKTREE="$(pwd)"

python3 "$WORKTREE/tools/test-ts-playwright.py"

FIXTURE="$(mktemp -d /tmp/marestail-scope-playwright.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT
cd "$FIXTURE"

mkdir -p src tests
cat > .gitignore <<'EOF'
node_modules/
.marestail/
playwright-report/
test-results/
EOF
cat > package.json <<'EOF'
{
  "name": "scope-playwright-fixture",
  "private": true,
  "devDependencies": {
    "@playwright/test": "^1.56.1",
    "c8": "^10.1.3",
    "typescript": "^5.8.3"
  }
}
EOF
cat > playwright.config.ts <<'EOF'
import { defineConfig } from "@playwright/test";
export default defineConfig({ testDir: "./tests" });
EOF
cat > marestail.toml <<'EOF'
[git]
base = "HEAD"

[ts]
root = "."
source = "src"
crap_max = 4
runner = "playwright"
EOF
cat > src/box.ts <<'EOF'
export function classify(n: number): string {
  if (n > 10) {
    return "big";
  }
  return "small";
}
EOF
cat > src/clean.ts <<'EOF'
export function double(n: number): number {
  return n * 2;
}
EOF
cat > tests/box.spec.ts <<'EOF'
import { expect, test } from "@playwright/test";
import { classify } from "../src/box";
import { double } from "../src/clean";

test("small", () => {
  expect(classify(1)).toBe("small");
});

test("doubles", () => {
  expect(double(2)).toBe(4);
});
EOF

git init -q .
git add .
git -c user.email=fixture@test -c user.name=fixture commit -qm base

PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --no-audit --no-fund --silent > npm-install.log 2>&1

run_gate() {
    python3 "$WORKTREE/marestail/cli.py" gate --tier fast --only ts.tests "$@"
}

if run_gate --scope all > uncovered.log 2>&1; then
    echo "VERDICT 1: all-scope PASS (unexpected)" >&2
    cat uncovered.log >&2
    exit 1
fi
grep -q "src/box.ts" uncovered.log || { cat uncovered.log >&2; exit 1; }
echo "VERDICT 1: all-scope FAIL on src/box.ts (expected)"

cat > tests/box.spec.ts <<'EOF'
import { expect, test } from "@playwright/test";
import { classify } from "../src/box";
import { double } from "../src/clean";

test("small", () => {
  expect(classify(1)).toBe("small");
});

test("big", () => {
  expect(classify(20)).toBe("big");
});

test("doubles", () => {
  expect(double(2)).toBe(4);
});
EOF

if run_gate --scope all > covered.log 2>&1; then
    echo "VERDICT 2: all-scope PASS after covering the branch (expected)"
else
    echo "VERDICT 2: all-scope FAIL (unexpected)" >&2
    cat covered.log >&2
    exit 1
fi

git add tests/box.spec.ts
git -c user.email=fixture@test -c user.name=fixture commit -qm covered

cat > src/fresh.ts <<'EOF'
export function triple(n: number): number {
  return n + n + n;
}
EOF
cat > tests/fresh.spec.ts <<'EOF'
import { expect, test } from "@playwright/test";
import { triple } from "../src/fresh";

test("triples", () => {
  expect(triple(3)).toBe(9);
});
EOF

if run_gate --scope changed > scoped.log 2>&1; then
    echo "VERDICT 3: scoped PASS (expected)"
else
    echo "VERDICT 3: scoped FAIL (unexpected)" >&2
    cat scoped.log >&2
    exit 1
fi

git add src/fresh.ts tests/fresh.spec.ts
git -c user.email=fixture@test -c user.name=fixture commit -qm fresh

cat > src/unused.ts <<'EOF'
export function neverCalled(n: number): number {
  return n + 1;
}
EOF

if run_gate --scope all > unused.log 2>&1; then
    echo "VERDICT 4: all-scope PASS with untested file (unexpected)" >&2
    cat unused.log >&2
    exit 1
fi
grep -q "src/unused.ts" unused.log || { cat unused.log >&2; exit 1; }
echo "VERDICT 4: all-scope FAIL on src/unused.ts (expected)"

mv node_modules/.bin/c8 node_modules/.bin/c8.hidden
if run_gate --scope all > noc8.log 2>&1; then
    echo "VERDICT 5: missing c8 PASS (unexpected)" >&2
    cat noc8.log >&2
    exit 1
fi
grep -q "c8 is not installed" noc8.log || { cat noc8.log >&2; exit 1; }
echo "VERDICT 5: missing c8 FAIL closed (expected)"
mv node_modules/.bin/c8.hidden node_modules/.bin/c8

echo "scope-playwright.sh: OK"

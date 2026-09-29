# QA procedure: hyper tests the repo can run, proven by mutation

Start in the marestail-green root with `.venv` active and `bin/` on PATH; `export M=$PWD`. Node 22 and npm must be on PATH. `ts.tests` behaves the same under `--tier fast` and `--tier full`; every step uses `--tier full`.

1. Build the sample:
   ```sh
   export T=/tmp/mt-testcmd && rm -rf $T && mkdir -p $T/client/embed && cd $T
   git init -q -b main && git config user.email q@a && git config user.name qa
   printf '.marestail/\nmarestail.toml\n' >> .git/info/exclude
   printf '[git]\nbase = "main"\n\n[ts]\nroot = "."\ntooling = ".marestail/tooling"\n\n[hyper]\ntest_cmd = "for f in client/embed/*.test.js; do node \\"$f\\" || exit 1; done"\n' > marestail.toml
   printf 'function popUpUrl(base, id) {\n  var url = base + "/embed/" + id;\n  return url;\n}\n\nfunction isOpen(state) {\n  return state === "open" || state === "shown";\n}\n\nmodule.exports = { popUpUrl: popUpUrl, isOpen: isOpen };\n' > client/embed/popUp.js
   printf 'function sizeOf(kind) {\n  if (kind === "wide") return 800;\n  if (kind === "tall") return 600;\n  var fallback = 400;\n  return fallback;\n}\n\nmodule.exports = { sizeOf: sizeOf };\n' > client/embed/size.js
   git add client && git commit -qm seed && git checkout -qb fix
   npm install --prefix .marestail/tooling @stryker-mutator/core typescript >/dev/null
   ```
   Expected: `git status --porcelain` prints nothing; `.marestail/tooling/node_modules/.bin/stryker` exists.
2. Make the fix and an asserting test whose vm sandbox passes `process`:
   ```sh
   sed -i '7s/"shown"/"opening"/' client/embed/popUp.js
   cat > client/embed/popUp.test.js <<'EOF'
   const assert = require("assert"); const fs = require("fs"); const vm = require("vm"); const path = require("path");
   const sandbox = { module: { exports: {} }, process };
   vm.runInNewContext(fs.readFileSync(path.join(__dirname, "popUp.js"), "utf8"), sandbox);
   const { isOpen } = sandbox.module.exports;
   function it(name, fn) { fn(); console.log("ok - " + name); }
   it("opening is open", () => assert.strictEqual(isOpen("opening"), true));
   it("open is open", () => assert.strictEqual(isOpen("open"), true));
   it("closed is closed", () => assert.strictEqual(isOpen("closed"), false));
   EOF
   node client/embed/popUp.test.js
   ```
   Expected: three `ok - ` lines, exit 0.
3. `marestail gate --tier full --scope hyper --only ts.tests,ts.crap,ts.mutation; echo "exit=$?"`
   Expected: the `scope: hyper: ...` line, directly followed by `proof: mutation via [hyper] test_cmd; coverage not measured`, then a blank line; `[ok  ] ts.tests       test_cmd exited 0; coverage advisory: not measured with [hyper] test_cmd`; `[ok  ] ts.mutation    all mutants killed (proof: mutation via [hyper] test_cmd)`; `[ok  ] ts.crap` with `0 above CRAP 4`; `GATE PASSED`; `exit=0`.
4. `git status --porcelain; ls package.json reports .stryker-tmp .marestail/stryker-tmp; cat .marestail/stryker/command.config.json`
   Expected: only `M client/embed/popUp.js` and `?? client/embed/popUp.test.js`; all four `ls` targets missing; the config shows `"testRunner": "command"`, the test_cmd and `"ignorePatterns": [".marestail"]`.
5. `cp client/embed/popUp.test.js /tmp/mt-good.js && sed -i 's/, process }/ }/' client/embed/popUp.test.js && node client/embed/popUp.test.js && marestail gate --tier full --scope hyper --only ts.tests,ts.mutation; echo "exit=$?"`
   Expected: the test still prints three `ok - ` lines; ts.tests `[ok  ]`; `[FAIL] ts.mutation` with `surviving mutants (proof: mutation via [hyper] test_cmd)` and first finding `hint: no mutant was killed; a test that loads code with vm must pass process into the sandbox, or no assertion depends on the changed lines`; `exit=1`.
6. `cp /tmp/mt-good.js client/embed/popUp.test.js && sed -i 's/assert.strictEqual(\(.*\), \(true\|false\))/\1/' client/embed/popUp.test.js && marestail gate --tier full --scope hyper --only ts.tests,ts.crap,ts.mutation; echo "exit=$?"`
   Expected: ts.tests `[ok  ]` with the advisory; `[FAIL] ts.mutation` with `surviving mutants`, the same `hint:` first finding, every other finding starting `client/embed/popUp.js:7 `, none mentioning `popUp.js:2` or `popUp.js:3`; `[FAIL] ts.crap` with `client/embed/popUp.js:6 isOpen crap=6.0 (cc=2, coverage=0%); changed lines not covered: 7`; `GATE FAILED: ts.crap, ts.mutation`; `exit=1`.
7. Change only lines that get no mutant (popUp.js 3 inside popUpUrl, 4 punctuation, 9 outside every function; size.js 5 inside sizeOf, cc=3) and test both files:
   ```sh
   git checkout -q client/embed/popUp.js
   sed -i -e '3s/return url;/return String(url);/' -e '4s/^}$/};/' -e '9s/^$/var loaded = 1;/' client/embed/popUp.js
   sed -i '5s/return fallback;/return Number(fallback);/' client/embed/size.js
   printf 'const assert=require("assert"),fs=require("fs"),vm=require("vm"),path=require("path");\nfunction load(f){const s={module:{exports:{}},process};vm.runInNewContext(fs.readFileSync(path.join(__dirname,f),"utf8"),s);return s.module.exports;}\nassert.strictEqual(load("popUp.js").popUpUrl("https://x","a"),"https://x/embed/a");console.log("ok - url");\nassert.strictEqual(load("size.js").sizeOf("square"),400);console.log("ok - size");\n' > client/embed/popUp.test.js
   marestail gate --tier full --scope hyper --only ts.tests,ts.crap,ts.mutation
   ```
   Expected: `[ok  ] ts.mutation    no mutants on changed lines` (whether or not Stryker wrote a report); `[ok  ] ts.crap        0 innermost changed functions, 0 above CRAP 4, 0 of them no worse than base; 2 changed lines not provable by mutation`, with exactly the findings `client/embed/popUp.js:3 not provable by mutation` and `client/embed/size.js:5 not provable by mutation` (no `crap=`, no `popUp.js:4`, no `popUp.js:9`); `GATE PASSED`.
8. `git checkout -q client/embed && marestail gate --tier full --scope hyper --only ts.tests,ts.crap,ts.mutation`
   Expected: `[skip] ts.mutation    no changed typescript sources`; `[skip] ts.crap        no files in scope`.
9. `sed -i 's/"https:\/\/x\/embed\/a"/"wrong"/' client/embed/popUp.test.js && marestail gate --tier full --scope hyper --only ts.tests; echo "exit=$?"`
   Expected: `[FAIL] ts.tests       test_cmd exited 1`, a finding containing `AssertionError`; `exit=1`.
10. `sed -i '/^\[hyper\]/,$d' marestail.toml && marestail gate --tier full --scope hyper --only ts.tests; echo "exit=$?"`
    Expected: `[FAIL] ts.tests       hyper: set [hyper] test_cmd to the command that runs this repository's tests`; no `proof:` line; `exit=1`.
11. Put a vitest runner only in the tooling folder, as `marestail install --scope hyper` does, and keep `[hyper]` removed:
    ```sh
    npm install --prefix .marestail/tooling vitest @stryker-mutator/vitest-runner >/dev/null
    printf 'import { defineConfig } from "vitest/config";\nexport default defineConfig({ root: process.cwd(), test: { include: ["client/**/*.spec.js"] } });\n' > .marestail/tooling/vitest.config.ts
    printf '{ "testRunner": "vitest", "plugins": ["@stryker-mutator/vitest-runner"], "ignorePatterns": [".marestail"] }\n' > .marestail/tooling/stryker.config.json
    ls package.json; marestail gate --tier full --scope hyper --only ts.tests; echo "exit=$?"
    marestail gate --tier full --scope diff --only ts.tests | grep -c 'hyper: set'
    ```
    Expected: `package.json` missing; `[FAIL] ts.tests       hyper: set [hyper] test_cmd to the command that runs this repository's tests`, no vitest output, no `proof:` line, `exit=1`; under `--scope diff` the grep prints `0` (ts.tests drives the tooling vitest as today).
12. `printf '[hyper]\ntest_cmd = "for f in client/embed/*.test.js; do node \\"$f\\" || exit 1; done"\n' >> marestail.toml && sed -i '7s/"shown"/"opening"/' client/embed/popUp.js && mv .marestail/tooling/node_modules /tmp/mt-nm && marestail gate --tier full --scope hyper --only ts.mutation; mv /tmp/mt-nm .marestail/tooling/node_modules`
    Expected: `[FAIL] ts.mutation    stryker produced no report (exit 127)`.
13. `cd $M && python3 tools/test-hyper-test-cmd.py; echo "exit=$?"; python3 tools/test-perf.py | tail -1`
    Expected: last line `hyper test_cmd ok`, `exit=0`; test-perf still ends `verdict-commit-files: '' != 'perf/bench_x.py'`.
14. `grep -n 'test_cmd' README.md; grep -n 'mutation stands in for coverage' README.md; grep -n 'pass process into the sandbox' README.md`
    `grep -n 'a runner installed only in the tooling folder does not count' README.md`
    Expected: the hyper section states the tests rule and shows the `[hyper] test_cmd` example; the other three greps each find a line.

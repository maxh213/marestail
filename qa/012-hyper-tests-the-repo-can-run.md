# QA procedure: hyper tests the repo can run, proven by mutation

Start in the marestail-green root with `.venv` active and `bin/` on PATH; `export M=$PWD`. Node 22 and npm must be on PATH.

1. Build the sample:
   ```sh
   export T=/tmp/mt-testcmd && rm -rf $T && mkdir -p $T/client/embed && cd $T
   git init -q -b main && git config user.email q@a && git config user.name qa
   printf '.marestail/\nmarestail.toml\n' >> .git/info/exclude
   printf '[git]\nbase = "main"\n\n[ts]\nroot = "."\ntooling = ".marestail/tooling"\n\n[hyper]\ntest_cmd = "for f in client/embed/*.test.js; do node \\"$f\\" || exit 1; done"\n' > marestail.toml
   printf 'function popUpUrl(base, id) {\n  var url = base + "/embed/" + id;\n  return url;\n}\n\nfunction isOpen(state) {\n  return state === "open" || state === "shown";\n}\n\nmodule.exports = { popUpUrl: popUpUrl, isOpen: isOpen };\n' > client/embed/popUp.js
   git add client && git commit -qm seed && git checkout -qb fix
   npm install --prefix .marestail/tooling @stryker-mutator/core typescript >/dev/null
   ```
   Expected: `git status --porcelain` prints nothing; `ls .marestail/tooling/node_modules/.bin/stryker` exists.
2. Make the fix and an asserting test:
   ```sh
   sed -i '7s/"shown"/"opening"/' client/embed/popUp.js
   cat > client/embed/popUp.test.js <<'EOF'
   const assert = require("assert"); const fs = require("fs"); const vm = require("vm"); const path = require("path");
   const sandbox = { module: { exports: {} } };
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
   Expected: a line `proof: mutation via [hyper] test_cmd; coverage not measured`; `[ok  ] ts.tests       test_cmd exited 0; coverage advisory: not measured with [hyper] test_cmd`; `[ok  ] ts.mutation    all mutants killed (proof: mutation via [hyper] test_cmd)`; `[ok  ] ts.crap` with `0 above CRAP 4`; `GATE PASSED`; `exit=0`.
4. `git status --porcelain; ls package.json reports .stryker-tmp`
   Expected: only `M client/embed/popUp.js` and `?? client/embed/popUp.test.js`; all three `ls` targets missing.
5. `sed -i 's/assert.strictEqual(\(.*\), \(true\|false\))/\1/' client/embed/popUp.test.js && marestail gate --tier full --scope hyper --only ts.tests,ts.crap,ts.mutation; echo "exit=$?"`
   Expected: ts.tests `[ok  ]` with the advisory; `[FAIL] ts.mutation` with `surviving mutants (proof: mutation via [hyper] test_cmd)`, every finding starting `client/embed/popUp.js:7 ` and none mentioning `popUp.js:2` or `:3`; `[FAIL] ts.crap` with `client/embed/popUp.js:6 isOpen crap=6.0 (cc=2, coverage=0%); changed lines not covered: 7`; `GATE FAILED: ts.crap, ts.mutation`; `exit=1`.
6. `git checkout -q client/embed/popUp.js && sed -i '3s/return url;/return String(url);/' client/embed/popUp.js && printf 'const assert=require("assert"),fs=require("fs"),vm=require("vm"),path=require("path");const s={module:{exports:{}}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,"popUp.js"),"utf8"),s);assert.strictEqual(s.module.exports.popUpUrl("https://x","a"),"https://x/embed/a");console.log("ok - url");\n' > client/embed/popUp.test.js && marestail gate --tier full --scope hyper --only ts.tests,ts.crap,ts.mutation`
   Expected: `[ok  ] ts.mutation    no mutants on changed lines`; `[ok  ] ts.crap` whose summary ends `; 1 changed lines not provable by mutation` with finding `client/embed/popUp.js:3 not provable by mutation`; `GATE PASSED`.
7. `sed -i 's/"https:\/\/x\/embed\/a"/"wrong"/' client/embed/popUp.test.js && marestail gate --tier fast --scope hyper --only ts.tests; echo "exit=$?"`
   Expected: `[FAIL] ts.tests       test_cmd exited 1`, a finding containing `AssertionError`; `exit=1`.
8. `sed -i '/^\[hyper\]/,$d' marestail.toml && marestail gate --tier fast --scope hyper --only ts.tests; echo "exit=$?"`
   Expected: `[FAIL] ts.tests       hyper: set [hyper] test_cmd to the command that runs this repository's tests`; no `proof:` line; `exit=1`.
9. `printf '[hyper]\ntest_cmd = "for f in client/embed/*.test.js; do node \\"$f\\" || exit 1; done"\n' >> marestail.toml && mv .marestail/tooling/node_modules /tmp/mt-nm && marestail gate --tier full --scope hyper --only ts.mutation; mv /tmp/mt-nm .marestail/tooling/node_modules`
   Expected: `[FAIL] ts.mutation    stryker produced no report (exit 127)`.
10. `cd $M && python3 tools/test-hyper-test-cmd.py; echo "exit=$?"; python3 tools/test-perf.py | tail -1`
    Expected: last line `hyper test_cmd ok`, `exit=0`; test-perf still ends `verdict-commit-files: '' != 'perf/bench_x.py'`.
11. `grep -n 'test_cmd' README.md; grep -n 'mutation stands in for coverage' README.md`
    Expected: the hyper section states the tests rule and shows the `[hyper] test_cmd` example; the second grep finds a line.

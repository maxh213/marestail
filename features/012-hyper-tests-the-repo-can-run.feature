Feature: under `--scope hyper` tests run with the repository's own command, and mutation proves they bite

  After this task a hyper fix adds tests that anyone can run with what the repository already has.
  When the repository has no runner marestail drives, `[hyper] test_cmd` tells the gates how to run
  those tests, and mutation on the changed lines stands in for coverage.

  Pinned rules (all apply only under `--scope hyper` with `[hyper] test_cmd` set, to the `[ts]` gates):
  - ts.tests runs `bash -lc "<test_cmd>"` in the target root and reads only its exit code.
    Exit 0: `[ok  ] ts.tests       test_cmd exited 0; coverage advisory: not measured with [hyper] test_cmd`.
    Exit n != 0: `[FAIL] ts.tests       test_cmd exited <n>` with the command's last 30 non-blank output lines as findings.
    No coverage report is read and coverage never fails the gate.
  - ts.mutation runs exactly `<tooling>/node_modules/.bin/stryker run <repo>/.marestail/stryker/command.config.json`,
    with no other flags, and cwd = the ts root (as today); only the binary comes from tooling (011). The gate writes
    that config, holding `"testRunner": "command"`,
    `"commandRunner": {"command": "<test_cmd>"}`, `"coverageAnalysis": "off"`, `"reporters": ["json", "progress"]`,
    `"jsonReporter": {"fileName": "<repo>/.marestail/stryker/mutation.json"}`,
    `"tempDirName": ".marestail/stryker-tmp"`, `"cleanTempDir": "always"`, `"ignorePatterns": [".marestail"]` and
    `mutate` set to the changed line ranges (007) of changed `.js .mjs .cjs .ts .tsx` files that are not tests.
    `ignorePatterns` keeps `.marestail/` (tooling, earlier reports) out of every sandbox; the temp dir still lives
    under `.marestail/` and is removed afterwards. Nothing lands in `reports/` or `.stryker-tmp/`.
    Only mutants that start on a changed line are counted or reported. Summaries:
    `all mutants killed (proof: mutation via [hyper] test_cmd)`,
    `<k> surviving mutants (proof: mutation via [hyper] test_cmd)` (fails), or
    `no mutants on changed lines` when none start on a changed line (passes).
    When at least one mutant survives and none is killed, the first finding is
    `hint: no mutant was killed; a test that loads code with vm must pass process into the sandbox, or no assertion depends on the changed lines`.
  - No report: Stryker exit 0 with no report means zero mutants (`no mutants on changed lines`, and ts.crap marks every
    changed line not provable). Any other exit with no report fails ts.mutation and ts.crap with today's text
    `stryker produced no report (exit <n>)` (007). No changed non-test JS/TS file: Stryker does not run,
    ts.mutation is `[skip] no changed typescript sources` and ts.crap is `[skip] no files in scope`, as today.
  - Stryker switches mutants through `process.env` of the global the code runs in. So a test that loads code with
    `vm` must pass `process` into the sandbox, e.g. `vm.runInNewContext(src, { module: { exports: {} }, process })`.
    The gate does not patch `vm`.
  - ts.crap needs no coverage file. It takes per-line proof from the same Stryker run as ts.mutation (one run per
    `marestail gate` call, even with `--only ts.crap`). It scores only files in that run's `mutate` set, so test files
    are never scored. Its lines are the provable-by-shape lines: changed lines (007) inside an innermost changed
    function (008), minus blank lines and lines holding only `{ } ( ) [ ] ; ,` and whitespace. Changed lines outside
    every function are ignored by ts.crap (ts.mutation still counts mutants that start on them).
    Each such line is covered (at least one killed mutant and no other mutant), not covered (it has a mutant that is
    not killed), or not provable (no mutant starts on it). A function's coverage is its covered lines over its lines
    that are covered or not covered, and its "changed lines not covered" are its not-covered lines; the 008 rules then
    apply unchanged. A function with no covered and no not-covered line is not scored: it is left out of the
    `<n> innermost changed functions` count and gets no CRAP finding.
    Each not-provable line adds the finding `<path>:<line> not provable by mutation`, sorted by path then line, after
    any CRAP findings; these never fail the gate, and the summary ends `; <n> changed lines not provable by mutation`.
  - The report prints `proof: mutation via [hyper] test_cmd; coverage not measured` on the line directly after
    `scope: <summary>` and before the blank line. `--json` output keeps today's keys and adds no proof key; the proof
    shows only in the gate summaries above.
  - A known runner is `vitest` or `jest` in `dependencies` or `devDependencies` of `<R>/package.json`, the target's own
    file. A runner installed only in `[ts] tooling` (for example the vitest, `vitest.config.ts` and vitest-runner
    `stryker.config.json` that `marestail install --scope hyper` puts there) does not count under hyper, because the
    repository cannot run it. Under hyper with no `test_cmd` and no known runner, ts.tests fails with summary
    `hyper: set [hyper] test_cmd to the command that runs this repository's tests`, before running anything.
    ts.mutation and ts.crap are unchanged in that case: they run as today (the tooling `stryker.config.json`, today's
    coverage file), with no `proof:` line.
  - Everything else, including every gate when `test_cmd` is unset but a known runner exists, and every scope other
    than hyper, behaves exactly as today. Other languages ignore `[hyper] test_cmd`.

  Background:
    Given a git repo on branch "main" with `.git/info/exclude` holding `.marestail/` and `marestail.toml`
    And `marestail.toml` is:
      """
      [git]
      base = "main"

      [ts]
      root = "."
      tooling = ".marestail/tooling"

      [hyper]
      test_cmd = "for f in client/embed/*.test.js; do node \"$f\" || exit 1; done"
      """
    And `.marestail/tooling` holds a real `@stryker-mutator/core` and `typescript` installed by npm
    And the repo has no `package.json`, and "main" commits `client/embed/popUp.js`:
      """
      1  function popUpUrl(base, id) {
      2    var url = base + "/embed/" + id;
      3    return url;
      4  }
      5
      6  function isOpen(state) {
      7    return state === "open" || state === "shown";
      8  }
      9
      10 module.exports = { popUpUrl: popUpUrl, isOpen: isOpen };
      """
    And "main" commits `client/embed/size.js`, whose `sizeOf` has cc=3:
      """
      1  function sizeOf(kind) {
      2    if (kind === "wide") return 800;
      3    if (kind === "tall") return 600;
      4    var fallback = 400;
      5    return fallback;
      6  }
      7
      8  module.exports = { sizeOf: sizeOf };
      """
    And a branch "fix" checked out from "main"

  Scenario: an asserted changed line passes, proven by mutation
    Given on "fix" line 7 is `  return state === "open" || state === "opening";`
    And "fix" adds `client/embed/popUp.test.js`, a Node script using `assert`, `fs`, `vm` and a home-made `it`,
      that loads popUp.js with `vm.runInNewContext(src, { module: { exports: {} }, process })` and asserts `isOpen("opening") === true`,
      `isOpen("open") === true` and `isOpen("closed") === false`, printing `ok - <name>` per case
    When I run `marestail gate --tier full --scope hyper --only ts.tests,ts.crap,ts.mutation`
    Then the line starting `scope: hyper: ` is directly followed by `proof: mutation via [hyper] test_cmd; coverage not measured` and then a blank line
    And it contains `[ok  ] ts.tests       test_cmd exited 0; coverage advisory: not measured with [hyper] test_cmd`
    And it contains `[ok  ] ts.mutation    all mutants killed (proof: mutation via [hyper] test_cmd)`
    And it contains `[ok  ] ts.crap        1 innermost changed functions, 0 above CRAP 4, 0 of them no worse than base`
    And the last line is `GATE PASSED` and the exit code is 0
    And `git status --porcelain` lists only `client/embed/popUp.js` and `client/embed/popUp.test.js`
    And no `package.json`, `reports/` or `.stryker-tmp/` exists at the repo root

  Scenario: the same change with the assertions removed fails on mutation and on CRAP
    Given the change and test of the scenario above, with every `assert.strictEqual(x, y)` replaced by `x;`
    When I run `marestail gate --tier full --scope hyper --only ts.tests,ts.crap,ts.mutation`
    Then `ts.tests` is `[ok  ]` with the advisory summary
    And `ts.mutation` is `[FAIL]` with a summary matching `^\d+ surviving mutants \(proof: mutation via \[hyper\] test_cmd\)$`
    And its first finding is the `hint: no mutant was killed; ...` line and every other finding starts with `client/embed/popUp.js:7 `
    And `ts.crap` is `[FAIL]` with the finding `client/embed/popUp.js:6 isOpen crap=6.0 (cc=2, coverage=0%); changed lines not covered: 7`
    And the last line is `GATE FAILED: ts.crap, ts.mutation` and the exit code is 1

  Scenario: a vm test that does not pass process kills nothing, and the gate says why
    Given the change and asserting test of the first scenario, but the sandbox is `{ module: { exports: {} } }`
    When I run `marestail gate --tier full --scope hyper --only ts.tests,ts.mutation`
    Then `ts.tests` is `[ok  ]` with the advisory summary, because the test itself passes
    And `ts.mutation` is `[FAIL]` with a summary matching `^\d+ surviving mutants \(proof: mutation via \[hyper\] test_cmd\)$`
    And its first finding is `hint: no mutant was killed; a test that loads code with vm must pass process into the sandbox, or no assertion depends on the changed lines`

  Scenario: mutants on unchanged lines are never reported
    Given the assertion-free change of the scenario "the same change with the assertions removed"
    When I run `marestail gate --tier full --scope hyper --only ts.mutation`
    Then no finding contains `popUp.js:2` or `popUp.js:3`, although nothing tests `popUpUrl`

  Scenario: changed lines with no mutants are not provable, and their functions are not scored
    Given on "fix" these lines change, none of which gets a mutant:
      | file                  | line | new text                     | why it matters                              |
      | client/embed/popUp.js | 3    | `  return String(url);`      | inside popUpUrl (cc=1)                      |
      | client/embed/popUp.js | 4    | `};`                         | only punctuation, ignored                   |
      | client/embed/popUp.js | 9    | `var loaded = 1;`            | outside every function, ignored             |
      | client/embed/size.js  | 5    | `  return Number(fallback);` | inside sizeOf (cc=3): 0% would give crap=12 |
    And popUp.test.js (sandbox passes `process`) asserts `popUpUrl("https://x", "a") === "https://x/embed/a"`
      and `sizeOf("square") === 400`
    And Stryker, finding no mutant in the `mutate` ranges, may exit 0 without writing a report
    When I run `marestail gate --tier full --scope hyper --only ts.tests,ts.crap,ts.mutation`
    Then it contains `[ok  ] ts.mutation    no mutants on changed lines`
    And it contains `[ok  ] ts.crap        0 innermost changed functions, 0 above CRAP 4, 0 of them no worse than base; 2 changed lines not provable by mutation`
    And ts.crap's findings are exactly `client/embed/popUp.js:3 not provable by mutation` then `client/embed/size.js:5 not provable by mutation`
    And no finding mentions `crap=`, `popUp.js:4` or `popUp.js:9`
    And the last line is `GATE PASSED`

  Scenario: no changed source file means Stryker does not run
    Given on "fix" only `client/embed/popUp.test.js` is added
    When I run `marestail gate --tier full --scope hyper --only ts.tests,ts.crap,ts.mutation`
    Then it contains `[skip] ts.mutation    no changed typescript sources` and `[skip] ts.crap        no files in scope`

  Scenario: a failing test_cmd fails the tests gate with its last lines
    Given the change of the first scenario and a test asserting `isOpen("opening") === false`
    When I run `marestail gate --tier full --scope hyper --only ts.tests` (ts.tests behaves the same under `--tier fast`)
    Then it contains `[FAIL] ts.tests       test_cmd exited 1`
    And its findings include a line containing `AssertionError`
    And the exit code is 1

  Scenario: no test_cmd and no known runner
    Given the `[hyper]` section is removed from `marestail.toml`
    When I run `marestail gate --tier fast --scope hyper --only ts.tests`
    Then it contains `[FAIL] ts.tests       hyper: set [hyper] test_cmd to the command that runs this repository's tests`
    And it contains no `proof:` line, and the exit code is 1

  Scenario: a runner installed only in the tooling folder does not count under hyper
    Given the `[hyper]` section is removed from `marestail.toml`
    And `.marestail/tooling` also holds `vitest` and `@stryker-mutator/vitest-runner` in `node_modules`, `vitest.config.ts`
      and a vitest-runner `stryker.config.json`, as `marestail install --scope hyper` writes them
    And the repo still has no `package.json`
    When I run `marestail gate --tier fast --scope hyper --only ts.tests`
    Then it contains `[FAIL] ts.tests       hyper: set [hyper] test_cmd to the command that runs this repository's tests`
    And no vitest output appears, the output has no `proof:` line, and the exit code is 1
    When I run `marestail gate --tier fast --scope diff --only ts.tests` on the same repo
    Then ts.tests runs the tooling vitest as today, and its summary does not contain `hyper: set [hyper] test_cmd`

  Scenario: a missing Stryker still fails the mutation gate
    Given `.marestail/tooling/node_modules` is renamed away
    When I run `marestail gate --tier full --scope hyper --only ts.mutation`
    Then it contains `[FAIL] ts.mutation    stryker produced no report (exit 127)`

  Scenario: targets with a runner marestail drives are unchanged
    Given a target whose `package.json` lists `vitest` in `devDependencies`, with `[hyper] test_cmd` set or not
    When the gate runs under any scope other than hyper, or under hyper without `test_cmd`
    Then ts.tests runs vitest and reads coverage as today, ts.mutation uses `stryker.config.json` as today,
      and the output has no `proof:` line
    And under hyper with `test_cmd` set the rules above apply instead

  Scenario: the hyper prompts carry the rule about tests
    Given TESTS_RULE is `Tests must run with a command the repository already supports, in the style its existing tests use; find that out first. If the repository has a runner, use it. If it has tests but no runner, write the same kind of script. If it has no tests at all, write dependency-free tests for the language's standard runtime and say so in your handoff. A test that loads code with vm must pass process into the sandbox, so mutation testing can switch mutants.`
    When the coder's prompt is built under `--scope hyper`
    Then its `# Scope` body is exactly HYPER_SCOPE, a space, and
      `Change as few lines as the fix needs. Prefer a small, well-named function over a longer inline condition. <TESTS_RULE> Write as many as you need. <HUNKS_INSTRUCTION>`
    And it no longer contains `Write the tests the repository can already run, in the style it already uses.`, which TESTS_RULE replaces
    When the hardener's prompt is built under `--scope hyper`
    Then its `# Scope` body is exactly HYPER_SCOPE, a space, today's hardener sentences, a space, TESTS_RULE, a space, and
      `Rule on every changed line the gate reports as not provable by mutation.`
    And prompts outside hyper, and the other roles' hyper prompts, are byte-identical to today's

  Scenario: the diagnostic scripts and README
    When I run `python3 tools/test-hyper-test-cmd.py`
    Then it exits 0 and its last line is `hyper test_cmd ok`
    And every other `tools/test-*.py` keeps its current result (`tools/test-perf.py` still exits 1 as in 000)
    And README's `--scope hyper` text states the rule about tests, documents `[hyper] test_cmd` with the example above,
      contains `mutation stands in for coverage`, says a vm-loaded test must `pass process into the sandbox`,
      and says that under hyper `a runner installed only in the tooling folder does not count`

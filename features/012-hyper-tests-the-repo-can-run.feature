Feature: under `--scope hyper` tests run with the repository's own command, and mutation proves they bite

  After this task a hyper fix adds tests that anyone can run with what the repository already has.
  When the repository has no runner marestail drives, `[hyper] test_cmd` tells the gates how to run
  those tests, and mutation on the changed lines stands in for coverage.

  Pinned rules (all apply only under `--scope hyper` with `[hyper] test_cmd` set, to the `[ts]` gates):
  - ts.tests runs `bash -lc "<test_cmd>"` in the target root and reads only its exit code.
    Exit 0: `[ok  ] ts.tests       test_cmd exited 0; coverage advisory: not measured with [hyper] test_cmd`.
    Exit n != 0: `[FAIL] ts.tests       test_cmd exited <n>` with the command's last 30 non-blank output lines as findings.
    No coverage report is read and coverage never fails the gate.
  - ts.mutation runs `<T>/node_modules/.bin/stryker run <config>` from `.marestail/tooling` (011), where <config> is a file
    the gate writes under `.marestail/` holding `"testRunner": "command"`, `"commandRunner": {"command": "<test_cmd>"}`,
    `"coverageAnalysis": "off"` and `mutate` set to the changed line ranges (007) of changed `.js .mjs .cjs .ts .tsx`
    files that are not tests. Stryker's report and temp dir also live under `.marestail/`.
    Only mutants that start on a changed line are counted or reported. Summaries:
    `all mutants killed (proof: mutation via [hyper] test_cmd)`,
    `<k> surviving mutants (proof: mutation via [hyper] test_cmd)` (fails), or
    `no mutants on changed lines` when none start on a changed line (passes). A missing or crashing
    Stryker fails with today's text `stryker produced no report (exit <n>)`.
  - ts.crap needs no coverage file. It takes per-line proof from the same Stryker run as ts.mutation (one run per
    `marestail gate` call, even with `--only ts.crap`). A changed line is covered when it has at least one killed
    mutant and no other mutant; a changed line with a surviving mutant is not covered; a function's coverage is its
    covered changed lines over its changed lines that have mutants. The 008 rules then apply unchanged.
    A changed line with no mutants is neither: it adds the finding `<path>:<line> not provable by mutation`,
    does not fail the gate, and the summary ends `; <n> changed lines not provable by mutation`.
  - Under the `scope:` header the report prints `proof: mutation via [hyper] test_cmd; coverage not measured`.
  - A known runner is `vitest` or `jest` in `dependencies` or `devDependencies` of `<R>/package.json`. Under hyper with
    no `test_cmd` and no known runner, ts.tests fails with summary
    `hyper: set [hyper] test_cmd to the command that runs this repository's tests`.
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
    And a branch "fix" checked out from "main"

  Scenario: an asserted changed line passes, proven by mutation
    Given on "fix" line 7 is `  return state === "open" || state === "opening";`
    And "fix" adds `client/embed/popUp.test.js`, a Node script using `assert`, `fs`, `vm` and a home-made `it`,
      that loads popUp.js with `vm.runInNewContext` and asserts `isOpen("opening") === true`,
      `isOpen("open") === true` and `isOpen("closed") === false`, printing `ok - <name>` per case
    When I run `marestail gate --tier full --scope hyper --only ts.tests,ts.crap,ts.mutation`
    Then the output contains `scope: hyper: ` and the line `proof: mutation via [hyper] test_cmd; coverage not measured`
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
    And every ts.mutation finding starts with `client/embed/popUp.js:7 `
    And `ts.crap` is `[FAIL]` with the finding `client/embed/popUp.js:6 isOpen crap=6.0 (cc=2, coverage=0%); changed lines not covered: 7`
    And the last line is `GATE FAILED: ts.crap, ts.mutation` and the exit code is 1

  Scenario: mutants on unchanged lines are never reported
    Given the assertion-free change of the scenario above
    When I run `marestail gate --tier full --scope hyper --only ts.mutation`
    Then no finding contains `popUp.js:2` or `popUp.js:3`, although nothing tests `popUpUrl`

  Scenario: a changed line with no mutants is not provable by mutation
    Given on "fix" only line 3 changes, to `  return String(url);`, and popUp.test.js asserts
      `popUpUrl("https://x", "a") === "https://x/embed/a"`
    When I run `marestail gate --tier full --scope hyper --only ts.tests,ts.crap,ts.mutation`
    Then it contains `[ok  ] ts.mutation    no mutants on changed lines`
    And `ts.crap` is `[ok  ]`, its summary ends `; 1 changed lines not provable by mutation`,
      and it shows the finding `client/embed/popUp.js:3 not provable by mutation`
    And the last line is `GATE PASSED`

  Scenario: a failing test_cmd fails the tests gate with its last lines
    Given the change of the first scenario and a test asserting `isOpen("opening") === false`
    When I run `marestail gate --tier full --scope hyper --only ts.tests`
    Then it contains `[FAIL] ts.tests       test_cmd exited 1`
    And its findings include a line containing `AssertionError`
    And the exit code is 1

  Scenario: no test_cmd and no known runner
    Given the `[hyper]` section is removed from `marestail.toml`
    When I run `marestail gate --tier fast --scope hyper --only ts.tests`
    Then it contains `[FAIL] ts.tests       hyper: set [hyper] test_cmd to the command that runs this repository's tests`
    And it contains no `proof:` line, and the exit code is 1

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
    When the coder's prompt is built under `--scope hyper`
    Then its `# Scope` section contains `Tests must run with a command the repository already supports, in the style its existing tests use; find that out first. If the repository has a runner, use it. If it has tests but no runner, write the same kind of script. If it has no tests at all, write dependency-free tests for the language's standard runtime and say so in your handoff.`
    When the hardener's prompt is built under `--scope hyper`
    Then its `# Scope` section contains that same text and `Rule on every changed line the gate reports as not provable by mutation.`
    And prompts outside hyper are byte-identical to today's

  Scenario: the diagnostic scripts and README
    When I run `python3 tools/test-hyper-test-cmd.py`
    Then it exits 0 and its last line is `hyper test_cmd ok`
    And every other `tools/test-*.py` keeps its current result (`tools/test-perf.py` still exits 1 as in 000)
    And README's `--scope hyper` text states the rule about tests, documents `[hyper] test_cmd` with the example above,
      and contains `mutation stands in for coverage`

# 012 — under hyper scope, the tests are ones the repository can already run, and mutation proves they bite

After this task, a reviewer who has never heard of marestail can run the tests in a hyper-scoped pull request with what is already in the repository. And marestail can still prove those tests mean something, even when the repository has no test runner and no coverage tool.

Task 011 keeps marestail's tooling out of the commit. That creates a trap: a Jest spec whose Jest config was never committed is a test nobody else can run. StripeDonationPortal is the example. Its existing JavaScript tests, `client/shared/js/embed/parentPostMessage.test.js` and `client/v1/js/validators.test.js`, are dependency-free Node scripts: `assert`, `fs`, `vm`, a home-made `it`, run as `node file.test.js`. CI runs no tests. A hyper fix there should add `popUpEmbed.test.js` in that style, not a Jest suite.

## What changes

**The rule, in the coder's and hardener's hyper prompts:** tests must run with a command the repository already supports, in the style its existing tests use. Find that out first. If the repository has a runner, use it. If it has tests but no runner, write the same kind of script. If it has no tests at all, write dependency-free tests for the language's standard runtime and say so in the handoff.

**A new config key tells the gates how to run them:**

```toml
[hyper]
test_cmd = "for f in client/shared/js/embed/*.test.js; do node \"$f\" || exit 1; done"
```

When the target has a runner marestail already drives, `test_cmd` is not needed and nothing changes. When `test_cmd` is set, under `--scope hyper`:

- **The tests gate** runs `test_cmd` and reads the exit code. It reports coverage as `advisory: not measured with [hyper] test_cmd` and does not fail on it.
- **Mutation is the proof.** The mutation gate runs the language's mutation tool with its command runner pointed at `test_cmd`, limited to the changed lines (007). Every mutant on a changed line must be killed. A killed mutant shows the line ran and an assertion depended on it, which is more than coverage shows. Stryker's command runner needs only an exit code, and it runs from `.marestail/tooling` (011).
- **CRAP** (008) takes "every changed line is covered" from the mutation result: a line with at least one killed mutant and no surviving or uncovered mutants counts as covered. A changed line on which the tool generates no mutants is reported as `not provable by mutation` and the hardener must rule on it.
- The gate summary says plainly which proof was used.

If there is no `test_cmd` and no runner marestail knows, the tests gate fails with `hyper: set [hyper] test_cmd to the command that runs this repository's tests`.

## What must not change

- Targets with a supported runner, under any scope.
- The mutation gate outside hyper, and under hyper without `test_cmd`.
- The rule from 007 that a missing or crashing tool fails its gate.
- Nothing is installed into the target's own dependency files.

## Tests

`tools/test-hyper-test-cmd.py`, on a sample with one legacy `.js` file and a Node-script test in the `assert` plus `vm` style:

- a changed line with an asserting test: tests gate passes, mutation kills every mutant on the line, CRAP treats the line as covered
- the same change with the assertion removed: mutation reports the surviving mutants on that line and the gate fails
- a changed line with no generated mutants is reported as `not provable by mutation`
- coverage is printed as advisory and never fails the gate
- no `test_cmd` and no known runner: the tests gate fails with the message above
- a failing `test_cmd` fails the tests gate with the command's last lines
- mutants on unchanged lines are never reported

## Done when

The new test passes, the other `tools/test-*.py` still pass, and README's hyper section states the rule about tests, documents `[hyper] test_cmd`, and explains in two sentences why mutation stands in for coverage there.

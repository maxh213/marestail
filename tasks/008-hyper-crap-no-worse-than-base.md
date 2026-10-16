# 008 — under hyper scope, a fix inside a complex legacy function passes CRAP without a rewrite

After this task, a developer who fixes a line inside a function that was already too complex can pass `marestail gate --scope hyper`, as long as they did not make the function worse.

Three things stop that today:

- `touches_hunk` matches every enclosing function as well as the innermost. In `popUpEmbed.js` the changed line sits in `receiveMessageFromIframe`, which is nested in `loadPaymentPopup`, which is the whole file. So the whole file is gated.
- Function coverage is counted by line range, so the outer function's score needs the whole file covered.
- The limit is an absolute 4. `receiveMessageFromIframe` has a complexity of 14. At 100% coverage CRAP equals complexity, so it can never pass.

## What changes, under `--scope hyper` only

- Only the **innermost** function containing a changed line is gated. A function that strictly contains another touched function is dropped.
- A gated function passes when **either** its CRAP is at most `crap_max`, **or** its cyclomatic complexity at HEAD is no higher than the same function's complexity at `[git] base` and every changed line in it is covered.
- The base complexity comes from the file at base (`git show <base>:<path>`), run through the same complexity scanner. Functions are matched by name and nesting path. A function that is new at HEAD has no base and must meet `crap_max`.
- A function that was renamed or moved counts as new. Hyper does not want renames anyway.
- The finding for a failure says which rule failed: `crap=… (cc=… coverage=…)`, or `complexity rose from 14 to 15; move the new condition into its own function`.
- Every language's CRAP gate gets the same two rules. Where a language's scanner cannot read a base file, that gate says so and falls back to `crap_max`.

The second rule has a useful effect. Adding `|| data.status === 'donated'` to a function at 14 takes it to 15 and fails. Moving the condition into a three-line `hasSuccessfullyDonated(data)` and calling it leaves the legacy function at 14, gives the new function a score it can meet, and makes the `if` readable. That is the boy scout rule doing its job on the code the fix touches.

## What must not change

- CRAP under `all`, `changed` and `hard`.
- `crap_max` and how it is configured.
- The scanner's complexity numbers.
- `ts.crap` still fails with `no coverage data; ts.tests must run first` when coverage is missing.

## Tests

Extend `tools/test-scope-hyper.py` with a sample file holding an outer function, and inside it a function at complexity 9 with no tests:

- change a line in the inner function without adding a branch, and cover that line: hyper passes; `changed` fails on both functions
- add a branch in the inner function: hyper fails with the `complexity rose from 9 to 10` finding
- move the new branch into a new small covered function: hyper passes
- a new function at complexity 6: hyper fails on `crap_max`
- an uncovered changed line in the inner function: hyper fails on coverage, not on complexity
- the outer function is never reported under hyper

## Done when

The extended test passes, the other `tools/test-*.py` still pass, and README's hyper paragraph states the two CRAP rules in one sentence each.

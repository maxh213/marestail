# 010 — under hyper scope, a judge rejects any change the fix did not need

After this task, a hyper run cannot hand back a diff that reaches past the code the fix touches. A judge reads every hunk against the task and bounces when the change wanders.

There is no line budget. Nobody knows in advance how many lines a fix needs, and a number would either be too tight for an honest fix or loose enough to hide a rewrite. The judge holds the standard instead. The line it draws is not "as few lines as possible". It is "nothing outside the code the fix touches". Inside that code the boy scout rule applies, and the architect (009) is expected to use it: naming a condition `hasSuccessfullyDonated()`, or splitting the function the fix lands in, is a better result than a bare `||`, and the judge must not bounce it. What the judge bounces is a hunk in a function the fix never needed to enter, a rename, a tidy-up next door, a change in behaviour nobody asked for.

A few things need no judgement, and a tool checks those first so the judge never sees them.

## What changes

**A tool check on the coder's work, under hyper, beside the freeze check.** It reads the diff from the run's start commit to HEAD and rejects, with a finding per problem:

- a renamed, moved or deleted file
- a hunk that changes only whitespace or formatting (present in `git diff`, absent from `git diff -w`)
- a changed file that is neither a source file nor a test file (config, lockfiles, docs, CI, tooling). Those are frozen under hyper, including `package.json`, lockfiles and `.gitignore`, which are not in the freeze list today
- a non-test hunk with no entry in the handoff's `## Hunks` section

The check runs on the coder's work and again on the architect's. Files that should not have been touched are reverted the way frozen files are today. Everything else bounces the role that made the change, with the findings. Nothing is partly reverted inside a file.

**The coder's and the architect's handoffs gain a `## Hunks` section:** one line per non-test hunk, `path:start-end — why`, where the reason is either "the fix needs it" or "boy scout: <what got better> in <the touched function>". The tool checks the section lists every non-test hunk. It does not read the reasons.

**A new judge, `blast`, after the architect and before the hardener, under hyper only.** It has no gate tier, bounces to the coder or the architect, whichever made the hunk, and writes nothing. Its prompt holds the task, the full diff, the diff stat and the `## Hunks` section. `roles/blast.md` is short:

> Judge whether this change stays inside the code the fix touches. For each hunk outside the tests, decide whether the fix needs it, or whether it makes the touched code better: a named condition, a long function split where the fix lands. Both are welcome. Bounce a hunk in code the fix never needed to enter, a rename or move, a tidy-up next door, a change that reaches into a module the task did not mention, a change in behaviour the task did not ask for, or a stated reason that does not hold against the code. Do not bounce for the number of lines. Tests are not limited: do not bounce for the number or size of tests, only for tests that exercise code the change does not touch in a way that would force later edits there. Say which hunk and what the smaller change is.

The hardener's hyper prompt from 009 already tells it not to ask for clean-up. With this judge in place, the hardener also may not bounce for a reason that would grow the diff beyond the fix; if it believes the fix is wrong, it bounces to the specifier.

## What must not change

- Runs under `all`, `changed` and `hard` never see the tool check, the `## Hunks` requirement or the judge.
- The freeze list under other scopes.
- How reverted files are committed and recorded as proposals.
- The repeat limits that stop a bounce loop for a human.

## Tests

Extend `tools/test-run-hyper.py`, stub agent:

- a coder that renames a file is bounced with the rename finding
- a coder that re-indents an untouched function is bounced with the whitespace finding
- a coder that edits `package.json` has that file reverted and recorded as a proposal
- a handoff missing a `## Hunks` line for one hunk is bounced, naming the hunk
- a stub `blast` verdict of `BOUNCE coder` sends the run back to the coder, `BOUNCE architect` to the architect; PASS moves on to the hardener
- `blast` runs between architect and hardener under hyper and not at all under hard
- an architect that extracts a named function from the touched function and lists it under `## Hunks` as boy scout passes the tool check
- a coder that adds five test files and one changed source line passes the tool check

## Done when

The extended test passes, the other `tools/test-*.py` still pass, `roles/blast.md` exists, and README's pipeline table and hyper section describe the judge and the four tool rules, state the boy scout rule in one sentence, and say in one sentence why there is no line budget.

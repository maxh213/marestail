# 009 — `marestail run --scope hyper` runs a short pipeline: the smallest fix, and the code it touches left a little better

After this task, a developer can hand marestail a bug and get back the smallest change that fixes it, with tests. The code the fix touches is left a little better than it was found, and the code around it is left alone.

That is the boy scout rule, applied only where the change lands. For the nag popup on StripeDonationPortal the result Max wanted was a new `hasSuccessfullyDonated()` that makes the `if` readable, not a 391-line rewrite and not a bare `||` bolted onto the condition either. So the architect stays in the hyper pipeline. It keeps its job, which is to look at the shape of the code, and takes the same focus as everyone else: it may split the function the fix lands in or give a condition a name, and it may not reshape anything the change does not touch.

The cleaner, practices and perf steps do not run. Their remit is the whole file or the whole repo, and on StripeDonationPortal#365 that is where a one-line fix became a rewrite. Steps cannot be dropped cleanly today. `window` takes only a contiguous slice, `optional` exists only on judges, and a worker returns before `optional` is checked.

There is no line budget in this mode, on purpose. Nobody knows in advance how many lines a fix needs. The standard is held by the roles, and 010 adds the judge that enforces it.

## What changes

- `run --scope hyper` is accepted. Its gates behave as in 007 and 008.
- The pipeline under hyper is: specifier ⇄ critic → coder → architect → hardener → qa. Cleaner, practices and perf do not run. 010 adds a judge after the architect.
- A `steps(mode)` function owns the list. `window`, `names` and `find` work from it, so `--from cleaner` under hyper is an error naming the roles that exist, and a judge that writes `VERDICT: BOUNCE cleaner` is refused the same way an unknown role is today.
- Every role's prompt gets a `# Scope` section under hyper, replacing the hard-scope text:
  - **All roles:** "This run is hyper-scoped. Make the smallest change that does what the task asks. Leave the code you touch a little better than you found it. Leave code the change does not touch exactly as it is, including code you would like to improve. The gates measure only the lines that change."
  - **Specifier:** "Write one scenario for the behaviour the task asks for, and regression scenarios only for behaviour the changed lines can reach."
  - **Critic:** "Bounce a scenario that would force a change outside the fix."
  - **Coder:** "Change as few lines as the fix needs. Prefer a small, well-named function over a longer inline condition. Write the tests the repository can already run, in the style it already uses. Write as many as you need."
  - **Architect:** "Apply the boy scout rule to the code this change touches, and only that code. If the function the fix lands in is long, split it. If the changed condition is hard to read, give it a name. Do not reshape, move or rename anything the change does not touch. Leave the dependency contracts as they are unless the change itself adds a dependency."
  - **Hardener:** "Judge the changed lines and their tests. Do not ask for clean-up, renames, or coverage of lines that did not change."
- The coder and the architect run the `full` tier under hyper, not `fast`, so it sees mutation results itself. Mutation covers only the changed lines, so this is cheap, and it removes the hardener round trip that cost 94 minutes on write-to task 001.
- `tools/overnight.sh` passes `SCOPE=hyper` through as it does `hard`.

## What must not change

- The pipeline, prompts and tiers under `all`, `changed` and `hard`.
- Role files. The hyper text lives with the other scope text in the prompt builder, not in `roles/*.md`.
- Freeze rules, handoff folding, verdict commits, the repeat limits.
- `--from` and `--to` with roles that exist under hyper.

## Tests

`tools/test-run-hyper.py`, temp repo, stub agent:

- a hyper run visits specifier, critic, coder, architect, hardener, qa in that order and no others
- `--from cleaner --scope hyper` exits with an error that lists the hyper roles
- a stub hardener that writes `VERDICT: BOUNCE cleaner` is refused and retried
- each role's prompt contains its hyper sentence and not the hard-scope paragraph
- the coder's and the architect's prompts tell them to run the `full` tier
- the architect's prompt contains the boy scout sentence and says to leave untouched code alone
- a `--scope hard` run still visits all nine roles

## Done when

The new test passes, the other `tools/test-*.py` still pass, and README's pipeline table has a hyper column showing which roles run.

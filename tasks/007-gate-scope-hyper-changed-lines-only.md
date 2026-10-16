# 007 — `marestail gate --scope hyper` gates only the lines that changed

After this task, a developer who changes one line in a legacy file can run `marestail gate --scope hyper` and be judged on that line alone. Pre-existing comments, lint findings, dead code, Sonar issues and unkilled mutants elsewhere in the file are not their problem.

This is the first slice of hyper scope. `--scope hard` means "bring this file fully up to standard": `gated_lines` returns every line of a focus file. On otwarteklatki/StripeDonationPortal#365 that turned a +1/−1 fix in a 275-line script into a 391-line rewrite, about 900 lines of tests and 229 minutes. Hyper is the opposite contract: the smallest change, and nothing outside it is measured.

`--scope changed` is most of the way there. Coverage already counts only changed lines and branch arms, and CRAP only functions a hunk touches. But lint, comments, deadcode, depth, mutation and Sonar filter by file, so one changed line still drags in every old finding in that file. `popUpEmbed.js` on master has 3 comments and 7 ESLint findings that the one-line fix does not touch.

## What changes

- `--scope hyper` is accepted by `gate` wherever `changed` and `hard` are. It needs no focus path. `--focus` with `--scope hyper` is an error: `--focus cannot be combined with --scope hyper; hyper gates the diff and nothing else`.
- Hyper builds the same diff as `changed`: `[git] base...HEAD` plus the working tree, untracked files wholly changed.
- One shared helper keeps a finding only when its `path:line` is a changed line. Every gate that reports `path:line` findings uses it under hyper: lint, comments, deadcode, depth, Sonar issues and hotspots, in every language.
- A finding with no usable line (a whole-file or whole-module finding) is dropped under hyper and counted in the gate's summary as `N file-level findings not gated under hyper`.
- Mutation: only mutants whose start line is a changed line are reported. Where the tool takes a line range (`stryker --mutate file:start-end`), pass it, so the run is short as well.
- Sonar duplication is per file with no line. Under hyper it is not gated, and the summary says so.
- Coverage is unchanged from `changed`. CRAP is unchanged in this task; 008 handles it.
- The scope line printed above the results reads `scope: hyper: N changed lines in M files`.
- `hook_scope` and `MARESTAIL_SCOPE` carry `hyper`, so Stop hooks gate the same way.

## What must not change

- `--scope all`, `changed` and `hard`, line for line. `hard` still counts a focus file as fully changed.
- Finding text and order. Hyper only removes findings.
- Fail-closed behaviour: a tool that crashes or produces no report still fails its gate under hyper.
- An empty diff under hyper skips the gates that skip under `changed` today, with the same messages.

## Tests

`tools/test-scope-hyper.py`, on the TypeScript and Python samples, each with a legacy file that fails comments, lint and mutation at base:

- change one clean, covered, asserted line: `gate --tier full --scope hyper` passes, `--scope changed` fails, `--scope hard --focus <file>` fails
- add a comment on the changed line: hyper fails with that one finding and no others
- add a lint error on the changed line: same
- change a line no test asserts on: hyper reports exactly the mutants on that line
- `--scope hyper --focus x` exits with the error above
- a gate whose tool is missing fails under hyper
- the summary shows the `file-level findings not gated` count when there are some

## Done when

The new test passes, `tools/test-scope-hard.py` and the other `tools/test-*.py` still pass, and README's scope section describes hyper next to `changed` and `hard`, with the one-line difference between the three.

Feature: under `--scope hyper` a tool check and the `blast` judge reject changes the fix did not need

  Under hyper the pipeline becomes specifier, critic, coder, architect, blast, hardener, qa.
  This replaces the six-role list in 009. `all`, `changed` and `hard` keep their nine roles, with no blast.

  The hunk check runs after every coder and architect attempt under hyper, next to the freeze check.
  It reads `git diff -U0 -M <start>..HEAD`, where <start> is the commit HEAD pointed at when `marestail run`
  started. It ignores `features/**`, `qa/**` and `.marestail/**`. A hunk's range is `s-e`, taken from the
  new side of its `@@` header: `+s,c` gives `s` to `s+c-1`, `+s` with no count gives `s-s`,
  and a deletion with `c=0` gives `s-s`.
  It emits one finding per problem:
    - rename or move: `<old> -> <new>: renamed or moved; under hyper no file may be renamed, moved or deleted`
    - deletion:       `<path>: deleted; under hyper no file may be renamed, moved or deleted`
    - whitespace:     `<path>:<s>-<e>: whitespace or formatting only; under hyper leave code the fix does not need as it is`
                      (a `git diff -U0` hunk whose `s-e` no `git diff -U0 -w` hunk of the same path overlaps; applies
                      to every file. A hunk that mixes re-indentation with a real change is overlapped, so it is not
                      whitespace-only and is checked as a normal hunk. A whitespace-only hunk gets this finding only,
                      never the unlisted one as well)
    - unlisted:       `<path>:<s>-<e>: not listed under ## Hunks`
                      (a hunk in a non-test file that no `## Hunks` line of this role's handoff covers.
                      A line covers a hunk when it has the same path and an overlapping `s-e`. Extra lines are fine.)
  The `## Hunks` section runs from its heading to the next `## ` heading or the end of the handoff. A line of it counts
  when, after optional leading spaces and an optional `- ` or `* `, it starts with `<path>:<s>-<e>` or `<path>:<s>`;
  `<path>:<s>` means `s-s`. Everything after the range is ignored, so `—`, `-`, `:` or no separator all work.
  Any other line of the section is ignored.
  A renamed, moved or deleted file gets its rename or delete finding only; none of its hunks, such as the
  `-1,4 +0,0` hunk of a deleted file, gets a whitespace or unlisted finding.
  The handoff checked is the one this role just wrote. The architect's `## Hunks` must list every non-test hunk
  since <start>, the coder's included; a coder hunk the architect leaves out is an unlisted finding against the architect.
  A test file has a path segment `tests`, `test`, `spec` or `__tests__`, or a name matching `test_*`,
  `*_test.*`, `*.test.*` or `*.spec.*`. A source file is any other file with extension
  .py .ts .tsx .js .jsx .mjs .cjs .rb .rs .cs .java .ex .exs .erl .hrl that the freeze list does not already cover.
  Any other changed file is frozen under hyper. It is reverted and recorded exactly as a frozen file is
  today, with the same texts. Every other finding sends the attempt back to the role that was checked, coder or architect. The runner never
  reverts part of a file.

  `tools/stub-claude` actions that this feature relies on. Each `code` variant does what `code` does today, plus the listed extra:
    `code`                   handoff also has `## Hunks` with `- src.py:1-2 — the fix needs it`
    `code no-hunks`          handoff has no `## Hunks` section
    `code rename`            `git mv util.py helpers.py`
    `code delete`            `git rm util.py`
    `code reindent`          lines 2-4 of util.py gain 4 more leading spaces each
    `code reindent-fix`      lines 2-4 of util.py gain 4 more leading spaces each and line 3 becomes `            return 2`;
                             `## Hunks` also lists `- util.py:2-4 — the fix needs it`
    `code miss-util`         util.py line 3 becomes `        return 2`; `## Hunks` still lists only src.py:1-2
    `code miss-util bare`    as `code miss-util`, and `## Hunks` also has the unbulleted line `util.py:3-3 — the fix needs it`
    `code miss-util single`  as `code miss-util`, and `## Hunks` also has the line `* util.py:3 - the fix needs it`
    `code package explain`   adds `package.json` containing `{}`, plus the `## Config change` section `explain` writes today
    `code five-tests`        the one exception: it skips the src.py write. It changes only util.py line 3 to `        return 2`,
                             writes tests/test_src.py plus tests/test_a.py to tests/test_d.py; `## Hunks` is `- util.py:3-3 — the fix needs it`
    `architect`              empty commit; handoff `architect done` with `## Hunks` `- src.py:1-2 — the fix needs it`
    `architect no-hunks`     empty commit; handoff `architect done` with no `## Hunks` section
    `architect util`         empty commit; handoff `architect done` with `## Hunks` `- util.py:3-3 — the fix needs it`
    `architect extract`      src.py becomes the six lines `def add_one(x):`, `    return increment(x)`, ``, ``,
                             `def increment(x):`, `    return x + 1`; `## Hunks` is `- src.py:1-6 — boy scout: named the increment in add_one`

  The blast prompt's `# Diff stat` is `git diff --stat <start>..HEAD` and its `# Diff` is `git diff <start>..HEAD`,
  both with the hunk check's `features/**`, `qa/**` and `.marestail/**` exclusions; either is `none` when empty.
  Its `# Hunks` holds, for the latest coder handoff and then the latest architect handoff in this run's handoff folder,
  a `## <handoff file stem>` line followed by that handoff's `## Hunks` lines. A handoff with no section is left out,
  and `# Hunks` is `none` when neither has one.

  Blast is not pinned: `VERDICT: BOUNCE` goes to the coder, and `VERDICT: BOUNCE <role>` follows today's rework rule.
  A worker role (coder, architect, or specifier) reruns and then blast judges again. A role that is not a worker,
  such as hardener, reruns nothing and the run prints `pipeline stopped at blast`.

  Background:
    Given the 009 fixture repo, plus `util.py` committed in `init` with the four lines
      `def untouched(value):`, `    if value:`, `        return 1`, `    return 0`
    And the 009 capture wrapper, STUB_PLAN and PATH setup
    And a "prepared" repo is a fixture after the hyper run of the first scenario; each scenario and each Examples row starts from its own prepared copy

  Scenario: a hyper run visits blast between architect and hardener
    Given STUB_PLAN is `specify`, `judge PASS`, `code`, `architect`, `judge PASS`, `judge PASS`, `worker qa`
    When I run `marestail run tasks/t.md --scope hyper --auto --retries 2` in a fresh fixture
    Then the `== <role> (` lines name, in order, specifier, critic, coder, architect, blast, hardener, qa
    And 7 prompts were saved and STUB_PLAN is empty
    And the exit code is 0 and the last non-empty stdout line is `pipeline complete`

  Scenario: the blast prompt holds the task, the diff, the diff stat and the hunks, and no gate report
    Given the run of the previous scenario
    Then prompt 05 starts with `You are the blast judge.`
    And it has a `# Task` section containing `# Add one`
    And it has a `# Diff stat` section whose text contains `src.py` and neither `features/t.feature` nor `qa/t.md`
    And it has a `# Diff` section whose text contains `+def add_one(x):`
    And it has a `# Hunks` section whose text contains `src.py:1-2 — the fix needs it`
    And it has no `# Gate report` section
    And its `# Scope` section is the shared hyper text alone, with no role sentence from 009

  Scenario: the blast prompt shows only what changed since this run started
    Given STUB_PLAN is `code five-tests`, `architect util`, `judge PASS`, `judge PASS`
    When I run `marestail run tasks/t.md --scope hyper --from coder --to hardener --auto --retries 1` in a prepared repo
    Then the exit code is 0 and the last non-empty stdout line is `pipeline complete`
    And the blast prompt's `# Diff` contains `+        return 2` and does not contain `+def add_one(x):`
    And its `# Diff stat` contains `util.py` and `tests/test_a.py` and does not contain `src.py |`
    And its `# Hunks` contains `## 01-coder`, then `- util.py:3-3 — the fix needs it`, then `## 02-architect`, then `- util.py:3-3 — the fix needs it`

  Scenario: the blast prompt says none when there is no diff and no hunks
    Given STUB_PLAN is `architect no-hunks`, `judge PASS`
    When I run `marestail run tasks/t.md --scope hyper --from architect --to blast --auto --retries 1` in a prepared repo
    Then the exit code is 0
    And the blast prompt's `# Diff stat`, `# Diff` and `# Hunks` sections are each `none`

  Scenario: roles/blast.md is short and says what to judge
    Then `roles/blast.md` starts with `You are the blast judge. You judge the diff; you never edit it.`
    And the rest of the file is the paragraph from the task, starting `Judge whether this change stays inside the code the fix touches.` and ending `Say which hunk and what the smaller change is.`

  Scenario Outline: blast routes its verdict
    Given STUB_PLAN is <plan>
    When I run `marestail run tasks/t.md --scope hyper --from architect --to hardener --auto --retries 2` in a prepared repo
    Then the `== <role> (` lines name, in order, <roles>
    And the exit code is 0 and the last non-empty stdout line is `pipeline complete`

    Examples:
      | plan                                                                          | roles                                          |
      | `architect`, `judge PASS`, `judge PASS`                                       | architect, blast, hardener                     |
      | `architect`, `judge BOUNCE coder`, `code`, `judge PASS`, `judge PASS`         | architect, blast, coder, blast, hardener       |
      | `architect`, `judge BOUNCE architect`, `architect`, `judge PASS`, `judge PASS` | architect, blast, architect, blast, hardener   |
      | `architect`, `judge BOUNCE specifier`, `specify`, `judge PASS`, `judge PASS`  | architect, blast, specifier, blast, hardener   |
      | `architect`, `judge BOUNCE`, `code`, `judge PASS`, `judge PASS`               | architect, blast, coder, blast, hardener       |

  Scenario: blast bouncing to a role that is not a worker stops the run
    Given STUB_PLAN is `architect`, `judge BOUNCE hardener`
    When I run `marestail run tasks/t.md --scope hyper --from architect --to hardener --auto --retries 2` in a prepared repo
    Then the `== <role> (` lines name, in order, architect, blast
    And stdout contains `pipeline stopped at blast` and the exit code is 1

  Scenario Outline: the hunk check bounces the coder with one finding per problem
    Given STUB_PLAN is `<action>`
    When I run `marestail run tasks/t.md --scope hyper --from coder --to coder --auto --retries 1` in a prepared repo
    Then stdout contains `<finding>`
    And stdout does not contain `<absent>`
    And stdout contains `pipeline stopped at coder` and the exit code is 1

    Examples:
      | action          | finding                                                                                              | absent                                |
      | code rename     | util.py -> helpers.py: renamed or moved; under hyper no file may be renamed, moved or deleted         | not listed under ## Hunks             |
      | code rename     | util.py -> helpers.py: renamed or moved; under hyper no file may be renamed, moved or deleted         | whitespace or formatting only         |
      | code delete     | util.py: deleted; under hyper no file may be renamed, moved or deleted                                | util.py:0-0                           |
      | code delete     | util.py: deleted; under hyper no file may be renamed, moved or deleted                                | not listed under ## Hunks             |
      | code reindent   | util.py:2-4: whitespace or formatting only; under hyper leave code the fix does not need as it is     | not listed under ## Hunks             |
      | code miss-util  | util.py:3-3: not listed under ## Hunks                                                                | whitespace or formatting only         |

  Scenario Outline: a ## Hunks line counts without a bullet, a dash separator or an end line
    Given STUB_PLAN is `<action>`
    When I run `marestail run tasks/t.md --scope hyper --from coder --to coder --auto --retries 1` in a prepared repo
    Then stdout does not contain `not listed under ## Hunks`
    And the exit code is 0 and the last non-empty stdout line is `pipeline complete`

    Examples:
      | action                 |
      | code miss-util bare    |
      | code miss-util single  |

  Scenario: a hunk that re-indents and also changes a line is not whitespace-only
    Given STUB_PLAN is `code reindent-fix`
    When I run `marestail run tasks/t.md --scope hyper --from coder --to coder --auto --retries 1` in a prepared repo
    Then stdout does not contain `whitespace or formatting only`
    And the exit code is 0 and the last non-empty stdout line is `pipeline complete`

  Scenario: the hunk check bounces the architect when its ## Hunks leaves out the coder's hunk
    Given STUB_PLAN is `code five-tests`, `architect no-hunks`
    When I run `marestail run tasks/t.md --scope hyper --from coder --to architect --auto --retries 1` in a prepared repo
    Then the `== <role> (` lines name, in order, coder, architect
    And stdout contains `util.py:3-3: not listed under ## Hunks`
    And stdout contains `pipeline stopped at architect` and the exit code is 1

  Scenario: a coder that edits package.json has it reverted and recorded as a proposal
    Given STUB_PLAN is `code package explain`, `code`
    When I run `marestail run tasks/t.md --scope hyper --from coder --to coder --auto --retries 2` in a prepared repo
    Then stdout contains `package.json: frozen, reverted. Your reason was recorded as`
    And `git log --format=%B` has a line matching `Revert change to frozen files by \d+-coder, recorded as a proposal` and a line matching `Proposed by \d+-coder: package\.json`
    And `git cat-file -e HEAD:package.json` fails
    And the exit code is 0 and the last non-empty stdout line is `pipeline complete`

  Scenario Outline: files that are neither source nor test are frozen under hyper
    When the hunk check classifies the changed path `<path>`
    Then it is <kind>

    Examples:
      | path                     | kind                                |
      | package.json             | frozen and reverted                 |
      | package-lock.json        | frozen and reverted                 |
      | .gitignore               | frozen and reverted                 |
      | README.md                | frozen and reverted                 |
      | .github/workflows/ci.yml | frozen and reverted                 |
      | note.txt                 | frozen and reverted                 |
      | lib/extra.py             | source, needing a `## Hunks` line   |
      | src/app.ts               | source, needing a `## Hunks` line   |
      | tests/test_x.py          | test, needing no `## Hunks` line    |
      | src/app.test.ts          | test, needing no `## Hunks` line    |

  Scenario: an architect that extracts a named function and lists it as boy scout passes the check
    Given STUB_PLAN is `code`, `architect extract`
    When I run `marestail run tasks/t.md --scope hyper --from coder --to architect --auto --retries 1` in a prepared repo
    Then the exit code is 0 and the last non-empty stdout line is `pipeline complete`
    And `git show HEAD:src.py` has `def increment(x):` on line 5

  Scenario: five test files and one changed source line pass the check
    Given STUB_PLAN is `code five-tests`
    When I run `marestail run tasks/t.md --scope hyper --from coder --to coder --auto --retries 1` in a prepared repo
    Then the exit code is 0 and the last non-empty stdout line is `pipeline complete`

  Scenario: the same hunk findings three times stop the run for a human
    Given STUB_PLAN is `code reindent` three times
    When I run `marestail run tasks/t.md --scope hyper --from coder --to coder --auto --retries 5` in a prepared repo
    Then stdout contains `coder got the same problems back 3 times in a row; the worker is not making progress, stopping for a human`
    And the exit code is 1

  Scenario: coder and architect are told to write ## Hunks under hyper only
    Given the hyper run of the first scenario
    Then prompts 03 (coder) and 04 (architect) contain "Add a `## Hunks` section to your handoff: one line per hunk outside the tests, `path:start-end — why`, where why is `the fix needs it` or `boy scout: <what got better> in <the touched function>`."
    And no other prompt of that run contains "Add a `## Hunks` section"
    And no prompt of a `--scope hard --focus src.py` run contains "Add a `## Hunks` section"

  Scenario: the hardener under hyper may not grow the diff
    Given the hyper run of the first scenario
    Then prompt 06 (hardener) contains `Do not bounce for a reason that would grow the diff beyond the fix; if you believe the fix is wrong, bounce to the specifier.`
    And no prompt outside hyper contains that sentence

  Scenario: blast does not exist outside hyper
    When I run `marestail run tasks/t.md --from blast --auto` in a fresh fixture with STUB_PLAN `code`
    Then the exit code is 1
    And stderr contains `unknown role blast; choose from specifier, critic, coder, cleaner, architect, practices, perf, hardener, qa`
    And no prompt was saved

  Scenario: the hyper unknown-role error lists blast
    When I run `marestail run tasks/t.md --scope hyper --from cleaner --auto` in a prepared repo with STUB_PLAN `code`
    Then the exit code is 1
    And stderr contains `unknown role cleaner; choose from specifier, critic, coder, architect, blast, hardener, qa`

  Scenario: a hard run still visits nine roles and never runs blast
    Given STUB_PLAN is `specify`, `judge PASS`, `code`, `worker cleaner`, `worker architect`, `judge PASS`, `worker qa`
    When I run `marestail run tasks/t.md --scope hard --focus src.py --auto --retries 2` in a fresh fixture
    Then the visited roles are specifier, critic, coder, cleaner, architect, practices, perf, hardener, qa
    And no `== blast (` line appears, STUB_PLAN is empty, and the last non-empty stdout line is `pipeline complete`

  Scenario Outline: outside hyper there is no hunk check and no new freeze
    Given STUB_PLAN is `code no-hunks package reindent`
    When I run `marestail run tasks/t.md <scope> --from coder --to coder --auto --retries 1` in a prepared repo
    Then the exit code is 0 and the last non-empty stdout line is `pipeline complete`
    And `git show HEAD:package.json` prints `{}`
    And stdout contains no `not listed under ## Hunks`, `whitespace or formatting only` or `is frozen` text

    Examples:
      | scope                         |
      |                               |
      | --scope changed               |
      | --scope hard --focus src.py   |

  Scenario: README describes the judge, the four rules, the boy scout rule and why there is no line budget
    Then README's pipeline table has the row `| blast | judge | — | none |` between the perf and hardener rows
    And the table's hyper column reads none, none, full, —, full, —, —, none, full, qa
    And the hyper section, the paragraph under the table that begins `The Gate column is`, says `that pipeline is specifier, critic, coder, architect, blast, hardener, qa`
    And the hyper section says that in the Gate column `—` means the step runs only under hyper
    And the hyper section names the four tool rules: renamed, moved or deleted files; whitespace or formatting only hunks; files that are neither a source file nor a test file; non-test hunks missing from `## Hunks`
    And the hyper section has one sentence containing `boy scout` and one sentence containing `no line budget`

  Scenario: the diagnostic scripts keep passing
    When I run `python3 tools/test-run-hyper.py`
    Then it exits 0 and its last line is `run hyper ok`, and it covers every scenario above
    And every other `tools/test-*.py` still exits 0 with a last line containing `ok`, except `tools/test-perf.py`, which still exits 1 with the last line `verdict-commit-files: '' != 'perf/bench_x.py'`

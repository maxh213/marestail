Feature: `marestail gate --scope hyper` gates only the lines that changed

  A developer who changes one line in a legacy file runs `marestail gate --scope hyper`
  and is judged on that line alone. Old comments, lint findings, dead code, Sonar issues
  and surviving mutants elsewhere in the file are not reported.

  Background:
    Given a git fixture repo whose `[git] base` is "base"
    And at base `app/legacy.py` is exactly:
      """
      import os
      # legacy note


      def double(price):
          return price * 2


      def label(name):
          return "item " + name


      def code(n):
          return n+1
      """
    And `tests/test_legacy.py` asserts `double(2) == 4` and `double(3) == 6`, and calls `label("x")` and `code(1)` without asserting their results
    And at base the fixture fails comments (`app/legacy.py:2`), py.lint (F401 at `app/legacy.py:1`, and `ruff format` wants to reformat `app/legacy.py`) and py.mutation (survivors in `label` and `code`)
    And the TypeScript fixture `src/legacy.ts` has the same shape: an old `// legacy note` on line 2, an ESLint error on line 1, a covered and asserted `double` on line 6, and unasserted `label` on line 10 and `code` on line 14

  Scenario Outline: one clean, covered, asserted line passes under hyper and nowhere else
    Given the working tree changes only line 6 of "<file>" to "<clean>"
    When I run `marestail gate --tier full --scope hyper`
    Then the exit code is 0 and the output ends with `GATE PASSED`
    And the first line is `scope: hyper: 1 changed lines in 1 files`
    And no result line mentions "<file>:1", "<file>:2", "<file>:10" or "<file>:14"
    When I run `marestail gate --tier full --scope changed`
    Then the exit code is 1 and the comments gate lists "<file>:2"
    When I run `marestail gate --tier full --scope hard --focus <file>`
    Then the exit code is 1 and the comments gate lists "<file>:2"

    Examples:
      | file          | clean                    |
      | app/legacy.py | "    return 2 * price"   |
      | src/legacy.ts | "  return 2 * price;"    |

  Scenario: a comment on the changed line is the only comments finding
    Given line 6 of `app/legacy.py` is changed to "    return 2 * price  # doubled"
    When I run `marestail gate --tier full --scope hyper --only comments`
    Then the exit code is 1
    And the comments gate has exactly one finding: `app/legacy.py:6 comment: # doubled`

  Scenario: a lint error on the changed line is the only lint finding
    Given line 6 of `app/legacy.py` is changed to "    return 2 * price if price != None else 0"
    When I run `marestail gate --tier full --scope hyper --only py.lint`
    Then the exit code is 1
    And py.lint has exactly one finding, and it contains `app/legacy.py:6:` and `E711`
    And no py.lint finding contains `app/legacy.py:1:` or `F401`

  Scenario: file-level findings are dropped and counted
    Given line 6 of `app/legacy.py` is changed to "    return 2 * price"
    When I run `marestail gate --tier full --scope hyper --only py.lint`
    Then py.lint is `[ok  ]` and its summary contains `1 file-level findings not gated under hyper`
    And the `ruff format` finding for `app/legacy.py` is not listed
    When I run the same with `--scope changed`
    Then py.lint fails and lists `format: Would reformat: app/legacy.py`

  Scenario: mutation reports only mutants on the changed line
    Given line 10 of `app/legacy.py` is changed to "    return \"item: \" + name"
    When I run `marestail gate --tier full --scope hyper --only py.mutation`
    Then the exit code is 1
    And every listed mutant is a `label` mutant (`x_label__mutmut_`) and at least one is listed
    And no `x_code__mutmut_` or `x_double__mutmut_` mutant is listed
    When I run the same with `--scope changed`
    Then `x_code__mutmut_` mutants are listed too

  Scenario: TypeScript mutation is limited to the changed line range
    Given line 10 of `src/legacy.ts` is changed and nothing else
    When I run `marestail gate --tier full --scope hyper --only ts.mutation`
    Then the stryker command gets `--mutate src/legacy.ts:10-10`
    And only mutants starting on line 10 are listed

  Scenario: Sonar issues are filtered by line and duplication is not gated
    Given Sonar reports issues at `app/legacy.py:2` and `app/legacy.py:6`, a hotspot at `app/legacy.py:1`, and 12.0% duplication in `app/legacy.py`
    And only line 6 of `app/legacy.py` changed
    When the sonar gate runs under `--scope hyper`
    Then its only finding is the issue at `app/legacy.py:6`
    And its summary contains `duplication not gated under hyper`

  Scenario: a gate whose tool is missing still fails under hyper
    Given line 6 of `app/legacy.py` is changed to "    return 2 * price"
    And `ruff` is removed from the fixture's `.venv/bin`
    When I run `marestail gate --tier full --scope hyper --only py.lint`
    Then the exit code is 1 and py.lint is `[FAIL]`

  Scenario: an empty diff skips as it does under changed
    Given the working tree equals base
    When I run `marestail gate --tier full --scope hyper`
    Then py.lint says `skipped: no changed python files` and py.mutation says `skipped: no changed python sources`, the same lines `--scope changed` prints

  Scenario: --focus cannot be combined with hyper
    When I run `marestail gate --scope hyper --focus app/legacy.py`
    Then the exit code is 2
    And stderr is `--focus cannot be combined with --scope hyper; hyper gates the diff and nothing else`

  Scenario: Stop hooks gate the same way
    Given `MARESTAIL_SCOPE=hyper` and line 6 of `app/legacy.py` changed to "    return 2 * price  # doubled"
    When `marestail gate --hook` runs
    Then it reports `app/legacy.py:6 comment: # doubled` and not `app/legacy.py:2`
    And `marestail run --scope hyper` exports `MARESTAIL_SCOPE=hyper`

  Scenario: existing scopes are unchanged
    When I run `marestail gate --help`
    Then `--scope` lists `all`, `changed`, `hard` and `hyper`
    And `python3 tools/test-scope-hard.py` exits 0
    And every other `tools/test-*.py` gives the same exit code and last line as before this task
    And `--scope changed --focus marestail` still prints `^scope: changed \(\d+ files, \d+ lines\) \+ focus: marestail$`
    And `--scope hard --focus marestail/cli.py` still prints `scope: hard: marestail/cli.py`

  Scenario: the diagnostic script and README cover hyper
    When I run `python3 tools/test-scope-hyper.py`
    Then it exits 0 and its last line contains `ok`
    And README's scope section describes `--scope hyper` next to `changed` and `hard`, with one line saying how the three differ

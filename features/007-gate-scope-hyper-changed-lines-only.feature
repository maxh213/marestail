Feature: `marestail gate --scope hyper` gates only the lines that changed

  A developer who changes one line in a legacy file runs `marestail gate --scope hyper`
  and is judged on that line alone.

  Rule for every path:line gate (comments, lint, deadcode, depth, Sonar issues and hotspots) under hyper, applied in this order:
    1. Crash first. If a tool exits with a code other than 0 or its findings code (ruff check, ruff format --check and mypy: 1),
       or produces no report, the gate fails with the finding text `changed` prints today, unfiltered.
    2. A finding holding `<repo-relative path>:<line>` (with or without `:<col>`) and line >= 1 is kept only when that line changed.
    3. A finding that names a changed file but has no line, or line 0, is file-level: dropped, and counted in the summary
       as `N file-level findings not gated under hyper`. The phrase is absent when N is 0.
    4. Any other output line (tool chatter such as `[*] 2 fixable with the --fix option.`, or ruff format's diff context) is dropped and not counted.
  Surviving findings keep their text and order. The same helper serves every language; the end-to-end fixtures are Python and TypeScript.

  Background:
    Given a Python fixture repo on branch "work" whose `marestail.toml` has `[git] base = "base"`, `[python] root = "."`, `sources = ["app"]`
    And its `pyproject.toml` sets pytest `pythonpath = ["."]`, `testpaths = ["tests"]`, `[tool.ruff] line-length = 120`,
      `[tool.ruff.lint] select = ["E", "F", "I"]`, mypy `files = ["app", "tests"]`, and an import-linter layers contract on `app`
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


      def relay(n):
          return code(n)
      """
    And `tests/test_legacy.py` asserts `double(2) == 4` and `double(3) == 6` and calls `label("x")`, `code(1)` and `relay(1)` without asserting
    And at base `--scope changed` after any edit to `app/legacy.py` reports these old findings:
      | gate        | old finding                                                         |
      | comments    | app/legacy.py:2 comment: # legacy note                              |
      | py.lint     | ruff: app/legacy.py:1:1: I001 [*] Import block is un-sorted or un-formatted |
      | py.lint     | ruff: app/legacy.py:1:8: F401 [*] `os` imported but unused         |
      | py.lint     | format:   --> app/legacy.py:14:13                                   |
      | depth       | app/legacy.py:17 relay only forwards its arguments                  |
      | deadcode    | app/legacy.py:5 unused function 'double'                            |
      | deadcode    | app/legacy.py:17 unused function 'relay'                            |
      | py.mutation | app.legacy.x_code__mutmut_1: survived                               |
    And a TypeScript fixture repo, with `package.json` and `package-lock.json` committed at base, vitest, `@vitest/coverage-v8`,
      `typescript@5`, eslint, `@stryker-mutator/core` and `@stryker-mutator/vitest-runner`,
      `eslint.config.js` = `export default [{files: ["src/**/*.ts"], rules: {"no-unused-vars": "error", "eqeqeq": "error"}}];`,
      `stryker.config.json` = `{"testRunner": "vitest", "coverageAnalysis": "perTest"}`, and `[ts] root = "."` in `marestail.toml`
    And at base `src/legacy.ts` is exactly:
      """
      const unused = 1;
      // legacy note


      export function double(price) {
        return price * 2;
      }

      export function label(name) {
        return "item " + name;
      }

      export function code(n) {
        return n+1;
      }
      """
    And `src/legacy.test.ts` expects `double(2)` to be 4 and `double(3)` to be 6 and calls `label("x")` and `code(1)` without expecting
    And at base `--scope changed` reports `src/legacy.ts:1 no-unused-vars: 'unused' is assigned a value but never used.`,
      `src/legacy.ts:2 comment: // legacy note`, and ts.mutation survivors on lines 9, 13 and 14

  Scenario Outline: one clean, covered, asserted line passes under hyper and nowhere else
    Given the working tree changes only line 6 of "<file>" to "<clean>"
    When I run `marestail gate --tier full --scope hyper`
    Then the exit code is 0 and the last line is `GATE PASSED`
    And the first line is `scope: hyper: 1 changed lines in 1 files`
    And no result line contains "<file>:1", "<file>:2", "<file>:14" or "<file>:17"
    When I run `marestail gate --tier full --scope hyper --json`
    Then the JSON has `"scope": "hyper"` and `"focus": []`
    When I run `marestail gate --tier full --scope changed`
    Then the exit code is 1 and comments lists "<file>:2 comment: <comment>"
    When I run `marestail gate --tier full --scope hard --focus <file>`
    Then the exit code is 1 and comments lists "<file>:2 comment: <comment>"

    Examples:
      | file          | clean                  | comment         |
      | app/legacy.py | "    return 2 * price" | # legacy note   |
      | src/legacy.ts | "  return 2 * price;"  | // legacy note  |

  Scenario: a comment on the changed line is the only comments finding
    Given line 6 of `app/legacy.py` is changed to "    return 2 * price  # doubled"
    When I run `marestail gate --tier full --scope hyper --only comments`
    Then the exit code is 1
    And comments has exactly one finding: `app/legacy.py:6 comment: # doubled`

  Scenario Outline: a lint error on the changed line is the only lint finding
    Given line 6 of "<file>" is changed to "<line>"
    When I run `marestail gate --tier full --scope hyper --only <gate>`
    Then the exit code is 1
    And <gate> has exactly one finding: `<finding>`
    And its summary is `1 problems`

    Examples:
      | file          | line                                              | gate    | finding                                                                    |
      | app/legacy.py | "    return 2 * price if price != None else 0"    | py.lint | ruff: app/legacy.py:6:34: E711 Comparison to `None` should be `cond is not None` |
      | src/legacy.ts | "  return price == null ? 0 : 2 * price;"         | ts.lint | src/legacy.ts:6 eqeqeq: Expected '===' and instead saw '=='.               |

  Scenario: depth and deadcode keep findings on changed lines and drop old ones
    Given lines 19 to 22 are appended to `app/legacy.py`: "", "", "def forward(n):", "    return label(n)"
    When I run `marestail gate --tier full --scope hyper --only depth,deadcode`
    Then the exit code is 1
    And depth has exactly one finding: `app/legacy.py:21 forward only forwards its arguments`
    And deadcode has exactly one finding: `app/legacy.py:21 unused function 'forward'`
    When I run the same with `--scope changed`
    Then depth also lists `app/legacy.py:17 relay only forwards its arguments` and deadcode also lists `app/legacy.py:5 unused function 'double'`

  Scenario: Python mutation reports only mutants on the changed line
    Given line 10 of `app/legacy.py` is changed to "    return \"item: \" + name"
    When I run `marestail gate --tier full --scope hyper --only py.mutation`
    Then the exit code is 1
    And the summary is `2 of 3 mutants not killed`
    And the findings are exactly `app.legacy.x_label__mutmut_2: survived` and `app.legacy.x_label__mutmut_3: survived`
    When I run the same with `--scope changed`
    Then the summary is `4 of 8 mutants not killed` and `app.legacy.x_code__mutmut_1: survived` is listed

  Scenario: TypeScript mutation mutates and reports only the changed line
    Given line 10 of `src/legacy.ts` is changed to "  return \"item: \" + name;" and nothing else
    When I run `marestail gate --tier full --scope hyper --only ts.mutation`
    Then the stryker command contains `--mutate src/legacy.ts:10-10`
    And the summary is `1 surviving mutants` and the only finding is `src/legacy.ts:10 StringLiteral Survived: ""`
    When I run the same with `--scope changed`
    Then survivors on `src/legacy.ts:9`, `src/legacy.ts:13` and `src/legacy.ts:14` are listed too

  Scenario: Sonar issues are filtered by line, file-level issues are counted, duplication is not gated
    Given a stubbed Sonar client, as in `tools/samples/scope-sonar.sh`, for a tree where only lines 1 and 6 of `app/legacy.py` changed
    And it returns open issues `{"component": "proj:app/legacy.py", "line": 2, "severity": "MAJOR", "rule": "python:S1481", "message": "old"}`,
      `{"component": "proj:app/legacy.py", "line": 6, "severity": "MAJOR", "rule": "python:S1481", "message": "new"}`
      and `{"component": "proj:app/legacy.py", "severity": "MINOR", "rule": "python:S1451", "message": "Add a header"}`,
      hotspots `{"component": "proj:app/legacy.py", "line": 1, "message": "check import"}` and
      `{"component": "proj:app/legacy.py", "line": 9, "message": "old hotspot"}`, and 12.0% duplication for `app/legacy.py`
    When the sonar gate collects and summarizes under `--scope hyper`
    Then the findings are exactly `app/legacy.py:6 MAJOR python:S1481: new` and `app/legacy.py:1 hotspot: check import`
    And no finding contains `duplication`, even though line 1 changed
    And the summary contains `1 file-level findings not gated under hyper` and `duplication not gated under hyper`

  Scenario: a gate whose tool is missing still fails under hyper
    Given line 6 of `app/legacy.py` is changed to "    return 2 * price"
    And `.venv/bin/ruff` is removed from the fixture
    When I run `marestail gate --tier full --scope hyper --only py.lint`
    Then the exit code is 1, py.lint is `[FAIL]` with summary `2 problems`
    And its findings are `ruff: <fixture>/.venv/bin/ruff: not found (...)` and `format: <fixture>/.venv/bin/ruff: not found (...)`
    And the summary does not contain `file-level`

  Scenario: an empty diff skips as it does under changed
    Given the working tree and HEAD equal base
    When I run `marestail gate --tier full --scope hyper`
    Then the first line is `scope: hyper: 0 changed lines in 0 files`, the exit code is 0 and the last line is `GATE PASSED`
    And py.lint says `skipped: no changed python files` and py.mutation says `skipped: no changed python sources`, as `--scope changed` does

  Scenario: --focus cannot be combined with hyper
    When I run `marestail gate --scope hyper --focus app/legacy.py`
    Then the exit code is 2
    And stderr is `--focus cannot be combined with --scope hyper; hyper gates the diff and nothing else`

  Scenario: Stop hooks and runs carry hyper
    Given `MARESTAIL_SCOPE=hyper`, `MARESTAIL_FOCUS` empty, and line 6 of `app/legacy.py` changed to "    return 2 * price  # doubled"
    When `echo '{}' | marestail gate --hook` runs
    Then its output contains `app/legacy.py:6 comment: # doubled` and not `app/legacy.py:2`
    When `marestail run --scope hyper <task>` starts an agent through `MARESTAIL_CLAUDE`
    Then the agent's environment has `MARESTAIL_SCOPE=hyper` and `MARESTAIL_FOCUS=` (empty)

  Scenario: existing scopes are unchanged
    When I run `marestail gate --help` and `marestail run --help`
    Then `--scope` lists `all`, `changed`, `hard` and `hyper`
    And `python3 tools/test-scope-hard.py` exits 0
    And every other `tools/test-*.py` gives the same exit code and last line as before this task
    And on the Python fixture, `--scope changed` prints `scope: changed (1 files, 1 lines)` and the same findings as before this task

  Scenario: the diagnostic script and README cover hyper
    When I run `python3 tools/test-scope-hyper.py`
    Then it exits 0 and its last line contains `ok`
    And README's scope section describes `--scope hyper` next to `changed` and `hard`, with one line saying how the three differ

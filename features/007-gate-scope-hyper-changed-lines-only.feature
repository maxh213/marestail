Feature: `marestail gate --scope hyper` gates only the lines that changed

  A developer who changes one line in a legacy file runs `marestail gate --scope hyper`
  and is judged on that line alone.

  Under hyper every gate sorts its output into two kinds before anything is filtered:
    - A location finding is built from one record of a tool's report that names a source file and a line
      (the findings `--scope changed` filters by file today). Only these go through the shared line filter.
    - A gate diagnostic is text marestail writes about the run itself: a crash, a missing tool, a missing or unparseable
      report, a bad config, a sanity check. Diagnostics are never line-filtered, never counted as file-level, and fail the gate.
      A placeholder location such as `marestail.toml:1`, `sonar-project.properties:1` or `<file>:1` does not make one a location finding.
  The shared line filter, for location findings only:
    1. A finding whose file is in the diff and whose line (>= 1) changed is kept, with its text and order unchanged.
    2. A finding whose file is in the diff but whose record has no line is file-level: dropped, and counted by appending
       `; N file-level findings not gated under hyper` to the gate's summary. Nothing is appended when N is 0.
    3. Every other location finding is dropped and not counted.
  Where each path:line gate draws the line (exit codes and texts are what the gates use today):
    | gate      | tool                    | location findings                                   | gate diagnostics, kept unfiltered                                                   |
    | py.lint   | ruff check, ruff format --check, mypy | exit 1: output lines holding `path:line[:col]`; other exit-1 lines (`[*] 2 fixable ...`, diff context, `1 file would be reformatted`) are dropped uncounted | any exit other than 0 or 1 (ruff 2, mypy 2, 127 `<bin>: not found (...)`): every output line, prefixed `ruff: `, `format: ` or `mypy: ` as today |
    | ts.lint   | tsc                     | lines matching the tsc `path(line,col)` pattern     | `tsc: <line>` when nothing matches; `marestail.toml:1 [ts] tsconfig = '<name>' does not exist under <root>` |
    | ts.lint   | eslint                  | messages in the JSON report; a message with no `line` is file-level, not `:1` | `eslint: <line>` when the report does not parse; `marestail.toml:1 eslint exited N without a message` when the exit is not 0 and no message survives parsing |
    | comments  | every scanner           | the scanner's `path:line comment: ...` records      | each `<language> comment scanner failed: <tail>` text                               |
    | deadcode  | vulture (exit 3 = findings), knip, and the ruby, C#, rust, java, elixir, erlang scanners | the scanner's `path:line ...` records | `vulture failed`, `knip produced no report`, `<language> deadcode scanner failed`, `elixir/erlang dead code analysis failed` texts and erlang hints |
    | depth     | in-process analysis     | every `path:line ...` finding                       | none                                                                                |
    | rb/rs/cs/java/ex/er lint | their linters | per-record `path:line` findings; rustfmt's `<file>:1 not rustfmt formatted` is file-level | `rubocop failed`, `cargo clippy failed`, `cargo fmt --check failed`, `PMD failed (exit N)`, `<file>:1 PMD could not analyse`, the C# `SARIF version` text, and each linter's existing failed text |
    | sonar     | SonarQube               | open issues, hotspots and reopened issues; an issue with no `line` is file-level | `not set up`, `scanner failed`, `analysis did not complete`, and the language checks `<where>:1 SonarQube received no <lang> lines ...` and `... SonarQube imported no <lang> coverage ...` |
  Mutation gates keep their existing no-report texts (`stryker produced no report (exit N)` and the like) as failures.
  The end-to-end fixtures are Python and TypeScript; other languages share the same filter and are covered by the table.

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
    Given a stubbed Sonar client, as in `tools/samples/scope-sonar.sh`, with quality gate status `ERROR`,
      for a tree where only lines 1 and 6 of `app/legacy.py` changed
    And it returns accepted issues `{"key": "A1", "component": "proj:app/legacy.py", "line": 6, "rule": "python:S3", "issueStatus": "ACCEPTED"}`
      and `{"key": "A2", "component": "proj:app/legacy.py", "line": 9, "rule": "python:S4", "issueStatus": "FALSE_POSITIVE"}`
    And it returns open issues `{"component": "proj:app/legacy.py", "line": 2, "severity": "MAJOR", "rule": "python:S1481", "message": "old"}`,
      `{"component": "proj:app/legacy.py", "line": 6, "severity": "MAJOR", "rule": "python:S1481", "message": "new"}`
      and `{"component": "proj:app/legacy.py", "severity": "MINOR", "rule": "python:S1451", "message": "Add a header"}`,
      hotspots `{"component": "proj:app/legacy.py", "line": 1, "message": "check import"}` and
      `{"component": "proj:app/legacy.py", "line": 9, "message": "old hotspot"}`, and 12.0% duplication for `app/legacy.py`
    When the sonar gate collects and summarizes under `--scope hyper`
    Then the client posted exactly one reopen transition, for issue `A1`; `A2` on unchanged line 9 is left as it is in Sonar
    And the findings, in this order, are exactly
      | app/legacy.py:6 python:S3 was marked ACCEPTED in Sonar instead of fixed; reopened. Fix the code, or a human adds an ignore rule to sonar-project.properties |
      | app/legacy.py:6 MAJOR python:S1481: new |
      | app/legacy.py:1 hotspot: check import |
    And no finding contains `duplication`, even though line 1 changed
    And the summary is `3 sonar findings in scope (global quality gate ERROR; scope: hyper: 2 changed lines in 1 files; 1 file-level findings not gated under hyper; duplication not gated under hyper)`
    When the stub returns no accepted issues, no open issues and no hotspots
    Then the gate passes with summary `sonar clean in scope (global quality gate ERROR; scope: hyper: 2 changed lines in 1 files; duplication not gated under hyper)`

  Scenario: Sonar language diagnostics still fail under hyper
    Given the same stub, a `[java]` section in `marestail.toml`, and language measures `java=40;py=12` with no `coverage` metric
    And no issues and no hotspots
    When the sonar gate runs under `--scope hyper`
    Then the gate fails with exactly one finding:
      `marestail.toml:1 SonarQube imported no java coverage; run java.tests first so .marestail/java-jacoco.xml exists`
    And the summary does not contain `file-level`

  Scenario: a config diagnostic with a placeholder line still fails under hyper
    Given in the TypeScript fixture `marestail.toml` has `[ts] tsconfig = "missing.json"`, committed at base
    And line 6 of `src/legacy.ts` is changed to "  return 2 * price;"
    When I run `marestail gate --tier full --scope hyper --only ts.lint`
    Then the exit code is 1
    And ts.lint has exactly one finding: `marestail.toml:1 [ts] tsconfig = 'missing.json' does not exist under .`

  Scenario: an eslint crash still fails under hyper
    Given line 6 of `src/legacy.ts` is changed to "  return 2 * price;"
    And `eslint.config.js` in the working tree is `export default [{rules: {"eqeqeq": }}];`
    When I run `marestail gate --tier full --scope hyper --only ts.lint`
    Then the exit code is 1 and ts.lint is `[FAIL]`
    And every finding starts with `eslint: ` or is `marestail.toml:1 eslint exited 2 without a message`

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

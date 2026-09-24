Feature: Bring marestail itself through its own gate

  This is a pure refactor: no user-visible behaviour of marestail may change.
  Every subcommand, flag, gate name, verdict format, worker prompt, freeze rule,
  handoff/run-log layout, and log line wording stays exactly as it is today.

  Background:
    Given the current working directory is the marestail-green repo root
    And the Python virtualenv at ".venv" is active and has the dev dependencies installed

  Scenario: full-tier gate passes for the Python package
    # py.mutation's mutant total omits marestail/gates/__init__.py: mutmut names those mutants
    # marestail.gates.x_*, which the gate's marestail.gates.__init__.* pattern never matches, so they
    # stay unchecked and drop out of both the total and the survivors. That is marestail's own
    # behaviour, unchanged from origin/main and out of scope for this refactor; the package's own
    # mutation self-test strips __init__ from its patterns, so those mutants are killed there.
    When I run "marestail gate --tier full"
    Then the exit code is 0
    And the output ends with the line "GATE PASSED"
    And every result line matches "^\[ok  \] .{14} .+  \(\d+\.\d+s\)$"
    And no result line starts with "[FAIL]"

  Scenario: default fast-tier gate still passes
    When I run "marestail gate"
    Then the exit code is 0
    And the output ends with the line "GATE PASSED"

  Scenario Outline: every tier prints the same gate names in the same order
    Given a local SonarQube started with "marestail sonar up" and configured with "marestail sonar setup"
    When I run "marestail gate --tier <tier>"
    Then the exit code is 0
    And the last line is "GATE PASSED"
    And the result lines name exactly these gates, in this order: <gates>
    And the "py.runtime" line reads "[ok  ] py.runtime     skipped: nothing declares the interpreter that ships  (0.0s)"
    And no result line names "qa", because marestail.toml has no [qa] section and the qa gate is dropped rather than printed

    Examples:
      | tier  | gates                                                                                           |
      | fast  | py.tests, py.crap, py.lint, py.deps, py.runtime, comments, depth, deadcode, docs                |
      | sonar | py.tests, py.crap, py.lint, py.deps, py.runtime, comments, depth, deadcode, docs, sonar         |
      | full  | py.tests, py.crap, py.lint, py.deps, py.runtime, comments, depth, deadcode, docs, py.mutation, sonar |
      | qa    | py.tests, py.crap, py.lint, py.deps, py.runtime, comments, depth, deadcode, docs                |
      | all   | py.tests, py.crap, py.lint, py.deps, py.runtime, comments, depth, deadcode, docs, py.mutation, sonar |

  Scenario: scoped gate flags still work
    When I run "marestail gate --tier fast --scope changed --focus marestail"
    Then the exit code is 0
    And the output contains a line matching "^scope: changed \(\d+ files, \d+ lines\) \+ focus: marestail$"
    When I run "marestail gate --tier fast --scope hard --focus marestail/cli.py"
    Then the exit code is 0
    And the output contains "scope: hard: marestail/cli.py"
    When I run "marestail gate --tier fast --scope all"
    Then the exit code is 0
    And no output line starts with "scope:"
    And the last line is "GATE PASSED"

  Scenario: combining --focus with --scope all is still a usage error
    When I run "marestail gate --tier fast --scope all --focus marestail"
    Then the exit code is 2
    And stderr contains "--focus cannot be combined with --scope all"

  Scenario: gate JSON output is unchanged
    When I run "marestail gate --tier fast --json"
    Then the exit code is 0
    And the output parses as JSON with keys "scope", "focus", and "results"
    And JSON "results" contains an entry for "py.tests"

  Scenario: gate failure reporting is unchanged
    Given a temporary Python file "marestail/_deliberate_comment.py" containing "# a comment\npass\n"
    When I run "marestail gate --tier fast --only comments"
    Then the exit code is 1
    And the output ends with "GATE FAILED: comments"
    And the output contains "marestail/_deliberate_comment.py:1 comment:"
    And the output does not end with "GATE PASSED"

  Scenario: no test calls a real agent CLI, docker, a real SonarQube, or the network
    Given a directory "/tmp/marestail-hermetic-bin" holding only symlinks to "git" and "sh"
    And an empty directory "/tmp/marestail-hermetic-home"
    When I run "unshare -r -n env -i HOME=/tmp/marestail-hermetic-home PATH=/tmp/marestail-hermetic-bin .venv/bin/pytest tests"
    Then the exit code is 0
    And no test failed or errored
    # env -i clears every variable the package reads, including every agent and tool override
    # (MARESTAIL_AGENT, MARESTAIL_AGY, MARESTAIL_CLAUDE, MARESTAIL_CURSOR, MARESTAIL_DANDELION, MARESTAIL_GROK,
    # MARESTAIL_KILO, MARESTAIL_KIMI, MARESTAIL_SONAR_PASSWORD, CLAUDE_CONFIG_DIR, GROK_HOME, JAVA_HOME);
    # PATH holds no docker, claude, grok, kilo, kimi, cursor-agent, agy, dandelion or sonar-scanner,
    # so docker and every agent CLI must be faked at marestail.shell.run, and each temporary git
    # repository must set its own user.name and user.email

  Scenario: long orchestration functions in runner, install, context, and the gates are split into small named helpers
    When I statically check function lengths in "marestail/runner.py", "marestail/install.py", "marestail/context.py", and "marestail/gates/*.py"
    Then no function exceeds 30 lines, except "registry" in "marestail/gates/__init__.py"
    And "registry" is exempt because it is a flat table of Gate declarations with no branches or calls to split out
    And long workflows are decomposed into small named helper functions

  Scenario: every CLI subcommand remains available
    When I run "marestail --help"
    Then the output lists the subcommands "gate", "run", "install", "sonar", "watch", "perf", "route", "graph", "depth"
    When I run "marestail gate --help"
    Then the output lists "--tier", "--scope", "--focus", "--only", "--json", "--hook"
    When I run "marestail run --help"
    Then the output lists "--from", "--to", "--auto", "--scope", "--focus", "--model", "--retries", "--effort", "--agent"
    When I run "marestail install --help"
    Then the output lists "--gitignore-generated"
    When I run "marestail sonar --help"
    Then the output lists "up", "down", "setup"
    When I run "marestail perf --help"
    Then the output lists "run", "db"
    When I run "marestail route --help" with "MARESTAIL_DANDELION" unset and PATH set to "/usr/bin:/bin"
    Then the exit code is 127
    And stderr contains "dandelion is not installed"
    # route forwards its arguments to dandelion instead of parsing them, so --help is not answered by marestail
    When I run "marestail watch --help"
    Then the output lists "--refresh", "--all"
    When I run "marestail graph"
    Then the exit code is 0
    And the first line of output is "## Python modules"
    # this repo declares its import-linter root packages in .importlinter, not pyproject.toml, so graph passes
    # grimp no package and prints grimp's TypeError traceback under that heading; the refactor keeps that output
    When I run "marestail depth"
    Then the exit code is 0

  Scenario: install command creates the expected files in an existing empty directory and prints the install line
    Given an existing empty temporary directory "/tmp/marestail-install-check"
    When I run "marestail install /tmp/marestail-install-check"
    Then the exit code is 0
    And "/tmp/marestail-install-check/marestail.toml" exists
    And "/tmp/marestail-install-check/sonar-project.properties" exists
    And "/tmp/marestail-install-check/tasks/README.md" exists
    And "/tmp/marestail-install-check/PERFORMANCE.md" exists
    And "/tmp/marestail-install-check/guidance/ts.md" exists
    And "/tmp/marestail-install-check/CLAUDE.md" contains "marestail gate"
    And "/tmp/marestail-install-check/AGENTS.md" contains "marestail gate"
    And stdout ends with "installed into /tmp/marestail-install-check; edit marestail.toml and sonar-project.properties\n"

  Scenario: route without dandelion still prints the install hint and exits 127
    Given the environment variable "MARESTAIL_DANDELION" is unset and PATH is set to "/usr/bin:/bin"
    When I run "marestail route"
    Then the exit code is 127
    And stderr contains "dandelion is not installed"
    And stderr contains "https://github.com/maxh213/dandelion"

  Scenario: existing diagnostic scripts keep passing
    # tools/test-route.py checks marestail's route parser against dandelion's own route table.
    # Unset, MARESTAIL_DANDELION_SRC falls back to ~/workspace/dandelion/src/domain/route.ts, another
    # project's working tree, which has since added a 'junie' provider marestail has no backend for,
    # so the script exits 1 there. origin/main carries the same fallback and fails identically, so
    # that is the external checkout drifting, not a regression; the fixture keeps this row self-contained.
    Given "MARESTAIL_DANDELION_SRC" points at a route-table fixture built from marestail's own
      backend names, so no row here depends on another project's working tree
    When I run each of the following directly with "python3":
      | script                         |
      | tools/test-agent-backends.py   |
      | tools/test-audit.py            |
      | tools/test-csproj-additions.py |
      | tools/test-drop-ignored.py     |
      | tools/test-route.py            |
      | tools/test-scope-hard.py       |
      | tools/test-sonar-worktree.py   |
      | tools/test-practices.py        |
    Then each script exits 0 and its last line of output contains "ok"
    And "tools/test-route.py" also prints a line matching "^dandelion source: \d+ route lines parse$"

  Scenario: the docker-backed diagnostic script keeps passing, proved by hand
    # tools/test-perf-db.py is the one tools/test-*.py that needs a live docker daemon: it shells
    # `docker run`/`docker exec` and boots a real postgres:16. No test under tests/ may run it. The
    # hermetic scenario above puts no docker on PATH, and where docker is present the script follows
    # daemon state (it once failed "db-samples-exit: 1 != 0" and passed unchanged on the next run),
    # so running it from pytest would make py.tests nondeterministic. QA step 27 runs it by hand.
    Given a live docker daemon that can run "postgres:16"
    When I run "python3 tools/test-perf-db.py"
    Then the exit code is 0 and the last line of output contains "ok"
    And no test under "tests/" runs this script

  Scenario: the hermetic suite pins the docker-backed script's contract with the package
    When I statically check every marestail name and call in "tools/test-perf-db.py"
    Then every name it reads off a marestail module still exists
    And every call binds against that function's real signature, so this refactor cannot rename a
      parameter, change an arity, or add a keyword-only argument without the check failing

  Scenario: tools/test-perf.py fails exactly as it does before the refactor
    # Its stub agent predates the two-phase perf step (commit 41f77a0) and crashes in the author phase;
    # tools/ is out of scope, so the refactor must reproduce today's failure, not fix it
    When I run "python3 tools/test-perf.py"
    Then the exit code is 1
    And the last line of output is "verdict-commit-files: '' != 'perf/bench_x.py'"

  Scenario: README documents every environment variable the package reads
    When I read "README.md"
    Then it contains a section titled "Environment variables"
    And that section lists all of the following variables:
      | MARESTAIL_AGENT                  |
      | MARESTAIL_AGY                    |
      | MARESTAIL_CLAUDE                 |
      | MARESTAIL_CURSOR                 |
      | MARESTAIL_DANDELION              |
      | MARESTAIL_FOCUS                  |
      | MARESTAIL_GATE_ACTIVE            |
      | MARESTAIL_GROK                   |
      | MARESTAIL_GROK_EFFORT            |
      | MARESTAIL_KILO                   |
      | MARESTAIL_KILO_VARIANT           |
      | MARESTAIL_KIMI                   |
      | MARESTAIL_LIMIT_WAIT_SECONDS     |
      | MARESTAIL_LIMIT_WAITS            |
      | MARESTAIL_PERF_DB_HOME           |
      | MARESTAIL_PERF_DB_PORT           |
      | MARESTAIL_PERF_DB_PREFIX         |
      | MARESTAIL_PERF_DB_ROWS           |
      | MARESTAIL_PERF_DB_VOLUME         |
      | MARESTAIL_SCOPE                  |
      | MARESTAIL_SONAR_PASSWORD         |
      | CLAUDE_CONFIG_DIR                |
      | DANDELION_CLAUDE_WORK_CONFIG_DIR |
      | GROK_HOME                        |
      | JAVA_HOME                        |
    And it notes that common variables such as HOME and PATH are ignored by the docs gate

  Scenario: no comments or docstrings remain under marestail/
    When I run "grep -R -E \"#[^!]|\"\"\"|'''\" marestail/ --include='*.py'"
    Then the only matches are shebang lines or string literals, not comments or docstrings

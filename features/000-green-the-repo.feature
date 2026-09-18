Feature: Bring marestail itself through its own gate

  This is a pure refactor: no user-visible behaviour of marestail may change.
  Every subcommand, flag, gate name, verdict format, worker prompt, freeze rule,
  handoff/run-log layout, and log line wording stays exactly as it is today.

  Background:
    Given the current working directory is the marestail-green repo root
    And the Python virtualenv at ".venv" is active and has the dev dependencies installed

  Scenario: full-tier gate passes for the Python package
    When I run "marestail gate --tier full"
    Then the exit code is 0
    And the output ends with the line "GATE PASSED"
    And every result line matches "^\[ok  \] <gate-name> <summary>  \\(<seconds>s\)$"
    And no result line starts with "[FAIL]"

  Scenario: default fast-tier gate still passes
    When I run "marestail gate"
    Then the exit code is 0
    And the output ends with the line "GATE PASSED"

  Scenario Outline: every tier name and verdict format is preserved
    When I run "marestail gate --tier <tier>"
    Then the exit code is 0
    And the output contains "GATE PASSED"
    And the output contains the gate names that belong to tier "<tier>"

    Examples:
      | tier  |
      | fast  |
      | sonar |
      | full  |
      | qa    |
      | all   |

  Scenario: scoped gate flags still work
    When I run "marestail gate --tier fast --scope changed --focus marestail"
    Then the exit code is 0
    And the output contains "scope: changed"
    When I run "marestail gate --tier fast --scope hard --focus marestail/cli.py"
    Then the exit code is 0
    And the output contains "scope: hard: marestail/cli.py"
    When I run "marestail gate --tier fast --scope all"
    Then the exit code is 0
    And the output contains "scope: all"

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
    When I run "marestail route --help"
    Then the exit code is 0
    When I run "marestail watch --help"
    Then the output lists "--refresh", "--all"
    When I run "marestail graph"
    Then the exit code is 0 or the error is only about a missing optional dependency
    When I run "marestail depth"
    Then the exit code is 0

  Scenario: install command creates the expected files in an existing empty directory and prints the same line
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
    And stdout is exactly "installed into /tmp/marestail-install-check; edit marestail.toml and sonar-project.properties\n"

  Scenario: route without dandelion still prints the install hint and exits 127
    Given the environment variable "MARESTAIL_DANDELION" is unset and PATH is set to "/usr/bin:/bin"
    When I run "marestail route"
    Then the exit code is 127
    And stderr contains "dandelion is not installed"
    And stderr contains "https://github.com/maxh213/dandelion"

  Scenario: existing diagnostic scripts keep passing
    When I run each of the following directly with "python3":
      | script                       |
      | tools/test-agent-backends.py |
      | tools/test-audit.py          |
      | tools/test-csproj-additions.py |
      | tools/test-drop-ignored.py   |
      | tools/test-route.py          |
      | tools/test-scope-hard.py     |
      | tools/test-sonar-worktree.py |
      | tools/test-perf.py           |
      | tools/test-perf-db.py        |
      | tools/test-practices.py      |
    Then each script exits 0 and its last line of output contains "ok"

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
    When I run "grep -R -E '#[^!]|"""|'''" marestail/ --include='*.py'
    Then the only matches are shebang lines or string literals, not comments or docstrings

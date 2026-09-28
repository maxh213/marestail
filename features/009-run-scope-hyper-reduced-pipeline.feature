Feature: `marestail run --scope hyper` runs a short pipeline

  Under hyper the pipeline is specifier, critic, coder, architect, hardener, qa.
  Cleaner, practices and perf do not exist in that mode. Every role's prompt gets
  a `# Scope` section with the hyper text, and the coder and the architect
  run the `full` tier. The pipeline, prompts and tiers under `all`, `changed`
  and `hard` stay as they are.

  The hyper roles, in order: specifier, critic, coder, architect, hardener, qa.
  The error for a role that does not exist is today's `find` text:
  `unknown role <name>; choose from <roles joined by ", ">`.

  Background:
    Given a temporary git repo with marestail installed and `tools/stub-claude` as the agent via `MARESTAIL_CLAUDE`
    And the task file is "tasks/t.md"
    And `marestail.toml` sets `[practices] enabled = false` and `[perf] enabled = false`, so both judges print their skip line instead of calling the agent
    And `tools/test-run-hyper.py` drives the scenarios below with that stub
    And a role is "visited" when the agent is invoked for it or the run prints its skip line `<role> disabled in marestail.toml; skipping`

  Scenario: a hyper run visits the six hyper roles in order and no others
    When I run `marestail run tasks/t.md --scope hyper --auto` with a stub plan where every worker commits a handoff and every judge writes `VERDICT: PASS`
    Then the agent is invoked for exactly these roles, in this order: specifier, critic, coder, architect, hardener, qa
    And stdout contains no line `practices disabled in marestail.toml; skipping` and no line `perf disabled in marestail.toml; skipping`
    And no handoff file under `.marestail/handoffs/t/` has a name ending in `-cleaner.md`, `-practices.md` or `-perf.md`
    And the last non-empty stdout line starts with `pipeline complete`

  Scenario: a hard run still visits all nine roles
    When I run `marestail run tasks/t.md --scope hard --focus src --auto` with the same kind of stub plan
    Then the roles visited, in order, are specifier, critic, coder, cleaner, architect, practices, perf, hardener, qa
    And the agent is invoked for specifier, critic, coder, cleaner, architect, hardener, qa
    And stdout contains `practices disabled in marestail.toml; skipping` and `perf disabled in marestail.toml; skipping`
    And the coder prompt says `--tier fast` and the architect prompt says `--tier sonar`

  Scenario: --from a role that hyper does not have is an error naming the hyper roles
    When I run `marestail run tasks/t.md --from cleaner --scope hyper --auto`
    Then the exit code is not 0
    And the output contains `unknown role cleaner; choose from specifier, critic, coder, architect, hardener, qa`
    And the agent is never invoked

  Scenario Outline: --from and --to with roles that exist under hyper still work
    When I run `marestail run tasks/t.md --scope hyper --from <from> --to <to> --auto`
    Then the agent is invoked for exactly <roles>, in that order

    Examples:
      | from      | to        | roles                     |
      | coder     | architect | coder, architect          |
      | architect | hardener  | architect, hardener       |
      | hardener  | qa        | hardener, qa              |

  Scenario: a judge that bounces to a role hyper does not have is refused
    When I run `marestail run tasks/t.md --scope hyper --from hardener --to hardener --auto --retries 2`
    And the stub hardener first writes `VERDICT: BOUNCE cleaner`, then the coder commits a handoff, then the hardener writes `VERDICT: PASS`
    Then the `cleaner` target is ignored, exactly as an unknown role such as `VERDICT: BOUNCE nobody` is today
    And the bounce goes to the hardener's default, so the next role invoked is coder, not cleaner
    And the hardener is invoked again after that coder and the run ends with `pipeline complete`

  Scenario Outline: every hyper prompt carries the hyper scope section
    When the <role> prompt is built under `--scope hyper`
    Then it contains a section `# Scope` with the sentence "This run is hyper-scoped. Make the smallest change that does what the task asks. Leave the code you touch a little better than you found it. Leave code the change does not touch exactly as it is, including code you would like to improve. The gates measure only the lines that change."
    And that section also contains <sentence>
    And the prompt does not contain "This run has a hard scope"

    Examples:
      | role      | sentence |
      | specifier | "Write one scenario for the behaviour the task asks for, and regression scenarios only for behaviour the changed lines can reach." |
      | critic    | "Bounce a scenario that would force a change outside the fix." |
      | coder     | "Change as few lines as the fix needs. Prefer a small, well-named function over a longer inline condition. Write the tests the repository can already run, in the style it already uses. Write as many as you need." |
      | architect | "Apply the boy scout rule to the code this change touches, and only that code. If the function the fix lands in is long, split it. If the changed condition is hard to read, give it a name. Do not reshape, move or rename anything the change does not touch. Leave the dependency contracts as they are unless the change itself adds a dependency." |
      | hardener  | "Judge the changed lines and their tests. Do not ask for clean-up, renames, or coverage of lines that did not change." |
      | qa        | no role-specific sentence (the all-roles sentence only) |

  Scenario Outline: the coder and the architect run the full tier under hyper
    When the <role> prompt is built under `--scope hyper`
    Then it contains "Run `marestail gate --tier full --scope hyper` and keep working until it prints GATE PASSED."
    And it does not contain "--tier fast" or "--tier sonar"

    Examples:
      | role      |
      | coder     |
      | architect |

  Scenario Outline: prompts and tiers outside hyper are unchanged
    When the <role> prompt is built under `--scope <scope>`
    Then it contains "Run `marestail gate --tier <tier>"
    And it contains no "This run is hyper-scoped"
    And it is byte-identical to the prompt the base commit builds for the same inputs

    Examples:
      | role      | scope   | tier  |
      | coder     | all     | fast  |
      | coder     | changed | fast  |
      | architect | all     | sonar |
      | architect | hard    | sonar |

  Scenario: overnight passes SCOPE=hyper through
    When I run `SCOPE=hyper START_FROM=coder STOP_AT=coder tools/overnight.sh tasks/t.md` with the stub
    Then the overnight summary records exit 0 for tasks/t.md
    And the coder prompt the stub received contains "This run is hyper-scoped." and "--tier full --scope hyper"

  Scenario: README shows which roles run under hyper
    When I read the table under `## Pipeline` in README.md
    Then it has a `hyper` column
    And that column marks specifier, critic, coder, architect, hardener and qa as running and cleaner, practices and perf as not running
    And the coder and architect rows name `full` as their gate under hyper

  Scenario: the existing diagnostic scripts keep passing
    When I run `python3 tools/test-run-hyper.py`
    Then it exits 0 and its last line contains "ok"
    And every other `tools/test-*.py` exits as it did before this task, `tools/test-perf.py` still exiting 1 with last line `verdict-commit-files: '' != 'perf/bench_x.py'`

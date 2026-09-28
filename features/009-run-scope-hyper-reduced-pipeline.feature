Feature: `marestail run --scope hyper` runs a short pipeline

  Under hyper the pipeline is specifier, critic, coder, architect, hardener, qa.
  Cleaner, practices and perf do not exist in that mode. Every role's prompt gets
  a `# Scope` section with the hyper text, and the coder and the architect are told
  to run, and are gated by, the `full` tier. `all`, `changed` and `hard` stay as they are.

  The error for a role that does not exist is today's `find` text:
  `unknown role <name>; choose from <roles joined by ", ">`. Under hyper it is
  `unknown role cleaner; choose from specifier, critic, coder, architect, hardener, qa`.

  Background:
    Given a fresh fixture repo: `git init -b main`; `marestail.toml` containing `[git] base = "main"`, `[practices] enabled = false` and `[perf] enabled = false`; `.gitignore` containing `.marestail/`; `tasks/t.md` containing `# Add one`; `src.py` containing `original`; all committed as `init`
    And `marestail` is run as `python3 <marestail-green>/marestail/cli.py run ...` with `<marestail-green>/bin` prepended to PATH
    And `MARESTAIL_CLAUDE` points at a capture wrapper that saves its stdin to `$PROMPTS/NN.txt` (NN = 01, 02, ... in invocation order) and then pipes it to `tools/stub-claude`
    And `STUB_PLAN` is a file with one `tools/stub-claude` action per line, consumed one per invocation
    And a "prepared" repo is a fresh fixture after the hyper run of the first scenario, so `features/t.feature` and `qa/t.md` exist
    And the role of an invocation is read from the first line of its saved prompt: `You are the <role>.` or `You are QA.`
    And a role is "visited" when stdout has its `== <role> (` line or its line `<role> disabled in marestail.toml; skipping`

  Scenario: a hyper run visits the six hyper roles in order and no others
    Given STUB_PLAN is `specify`, `judge PASS`, `code`, `worker architect`, `judge PASS`, `worker qa`
    When I run `marestail run tasks/t.md --scope hyper --auto --retries 2` in a fresh fixture
    Then the `== <role> (` lines on stdout name, in order, specifier, critic, coder, architect, hardener, qa
    And stdout names no cleaner, practices or perf, including no `disabled in marestail.toml; skipping` line
    And STUB_PLAN is empty and 6 prompts were saved
    And the exit code is 0 and the last non-empty stdout line is `pipeline complete`

  Scenario: a hard run still visits all nine roles
    Given STUB_PLAN is `specify`, `judge PASS`, `code`, `worker cleaner`, `worker architect`, `judge PASS`, `worker qa`
    When I run `marestail run tasks/t.md --scope hard --focus src.py --auto --retries 2` in a fresh fixture
    Then the visited roles, in order, are specifier, critic, coder, cleaner, architect, practices, perf, hardener, qa
    And the exit code is 0 and the last non-empty stdout line is `pipeline complete`

  Scenario Outline: --from or --to a role that hyper does not have is an error
    Given STUB_PLAN is `code`
    When I run `marestail run tasks/t.md --scope hyper <flag> cleaner --auto` in a prepared repo
    Then the exit code is 1
    And stderr contains `unknown role cleaner; choose from specifier, critic, coder, architect, hardener, qa`
    And no prompt was saved and STUB_PLAN still reads `code`

    Examples:
      | flag   |
      | --from |
      | --to   |

  Scenario Outline: --from or --to an unknown role is an error in every mode
    Given STUB_PLAN is `code`
    When I run `marestail run tasks/t.md <flag> bogus --auto` in a fresh fixture
    Then the exit code is 1
    And stderr contains `unknown role bogus; choose from specifier, critic, coder, cleaner, architect, practices, perf, hardener, qa`
    And stdout does not contain `pipeline complete`
    And no prompt was saved and STUB_PLAN still reads `code`

    Examples:
      | flag   |
      | --from |
      | --to   |

  Scenario Outline: --from and --to with roles that exist under hyper still work
    Given STUB_PLAN is <plan>
    When I run `marestail run tasks/t.md --scope hyper --from <from> --to <to> --auto --retries 2` in a prepared repo
    Then the `== <role> (` lines on stdout name exactly <roles>, in that order, and the exit code is 0

    Examples:
      | from      | to        | plan                                | roles               |
      | coder     | architect | `code`, `worker architect`          | coder, architect    |
      | architect | hardener  | `worker architect`, `judge PASS`    | architect, hardener |
      | hardener  | qa        | `judge PASS`, `worker qa`           | hardener, qa        |

  Scenario: a judge that bounces to a role hyper does not have is refused
    Given STUB_PLAN is `judge BOUNCE cleaner`, `code`, `judge PASS`
    When I run `marestail run tasks/t.md --scope hyper --from hardener --to hardener --auto --retries 2` in a prepared repo
    Then the `== <role> (` lines on stdout name, in order, hardener, coder, hardener
    And no cleaner is invoked
    And the exit code is 0 and the last non-empty stdout line is `pipeline complete`

  Scenario Outline: every hyper prompt carries its hyper scope section
    When the <role> prompt is saved during the first scenario's hyper run
    Then it contains a section `# Scope` with the sentence "This run is hyper-scoped. Make the smallest change that does what the task asks. Leave the code you touch a little better than you found it. Leave code the change does not touch exactly as it is, including code you would like to improve. The gates measure only the lines that change."
    And that section also contains <sentence>
    And it contains no other role's sentence from this table
    And it does not contain "This run has a hard scope"

    Examples:
      | role      | sentence |
      | specifier | "Write one scenario for the behaviour the task asks for, and regression scenarios only for behaviour the changed lines can reach." |
      | critic    | "Bounce a scenario that would force a change outside the fix." |
      | coder     | "Change as few lines as the fix needs. Prefer a small, well-named function over a longer inline condition. Write the tests the repository can already run, in the style it already uses. Write as many as you need." |
      | architect | "Apply the boy scout rule to the code this change touches, and only that code. If the function the fix lands in is long, split it. If the changed condition is hard to read, give it a name. Do not reshape, move or rename anything the change does not touch. Leave the dependency contracts as they are unless the change itself adds a dependency." |
      | hardener  | "Judge the changed lines and their tests. Do not ask for clean-up, renames, or coverage of lines that did not change." |
      | qa        | no role-specific sentence (the all-roles sentence only) |

  Scenario Outline: the coder and the architect are told to run the full tier under hyper
    When the <role> prompt is saved during the first scenario's hyper run
    Then it contains "Run `marestail gate --tier full --scope hyper` and keep working until it prints GATE PASSED."
    And it does not contain "--tier fast" or "--tier sonar"

    Examples:
      | role      |
      | coder     |
      | architect |

  Scenario Outline: the runner gates each worker and judge with the tier its mode names
    When the pipeline runs under `--scope <scope>` with the first scenario's stub plan for hyper and the hard scenario's plan otherwise, and the tier of every `Run.gates` call is recorded in call order
    Then the tiers are <tiers>

    Examples:
      | scope   | tiers                         |
      | hyper   | full, full, full, qa          |
      | hard    | fast, sonar, sonar, full, qa  |
      | changed | fast, sonar, sonar, full, qa  |
      | all     | fast, sonar, sonar, full, qa  |

  Scenario Outline: prompts outside hyper are unchanged
    When the <role> prompt is built under `--scope <scope>`
    Then it contains "Run `marestail gate --tier <tier>"
    And it does not contain "This run is hyper-scoped"
    And it is byte-identical to the prompt the base commit builds for the same inputs

    Examples:
      | role      | scope   | tier  |
      | coder     | all     | fast  |
      | coder     | changed | fast  |
      | coder     | hard    | fast  |
      | architect | all     | sonar |
      | architect | hard    | sonar |

  Scenario: overnight passes SCOPE=hyper through
    Given STUB_PLAN is `code`
    When I run `SCOPE=hyper START_FROM=coder STOP_AT=coder tools/overnight.sh tasks/t.md` in a prepared repo
    Then the exit code is 0 and the newest `.marestail/runs/overnight-*.md` contains `- exit 0`
    And the one saved prompt starts with `You are the coder.` and contains "This run is hyper-scoped." and "--tier full --scope hyper"

  Scenario: README shows which roles run under hyper
    When I read the table under `## Pipeline` in README.md
    Then it has a `hyper` column after `Gate`, whose cells are
      | Step      | hyper |
      | specifier | none  |
      | critic    | none  |
      | coder     | full  |
      | cleaner   | —     |
      | architect | full  |
      | practices | —     |
      | perf      | —     |
      | hardener  | full  |
      | qa        | qa    |
    And the paragraph under the table says the hyper column is the tier each step runs under `--scope hyper`, and that `—` means the step does not run under hyper

  Scenario: the diagnostic scripts keep passing
    When I run `python3 tools/test-run-hyper.py`
    Then it exits 0 and its last line contains "ok"
    And every other `tools/test-*.py` exits as it did before this task, `tools/test-perf.py` still exiting 1 with last line `verdict-commit-files: '' != 'perf/bench_x.py'`

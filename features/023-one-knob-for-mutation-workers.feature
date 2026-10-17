Feature: one knob for mutation workers: [run] jobs and MARESTAIL_JOBS

  `[run] jobs` in `marestail.toml` or `MARESTAIL_JOBS` in the environment sets
  the parallel worker count for every language mutation gate. The environment
  variable beats per-language keys, which beat `[run] jobs`, which beats each
  tool's default. Values from `MARESTAIL_JOBS` and `[run] jobs` are capped at
  `os.cpu_count() or 1`. Per-language keys are passed raw and uncapped. Zero
  and non-positive values are errors; empty `MARESTAIL_JOBS=` falls back to the
  configuration key.

  Background:
    Given a working directory with a valid `marestail.toml`

  Scenario Outline: parse accepts whole numbers 1 or more
    When `<name>` is parsed from `<raw>`
    Then the parsed worker count is <expected>

    Examples:
      | name           | raw  | expected |
      | MARESTAIL_JOBS | 1    | 1        |
      | MARESTAIL_JOBS | 3    | 3        |
      | MARESTAIL_JOBS | "3"  | 3        |
      | MARESTAIL_JOBS | "03" | 3        |
      | [run] jobs     | 1    | 1        |
      | [run] jobs     | 3    | 3        |
      | [run] jobs     | "3"  | 3        |
      | [run] jobs     | "03" | 3        |

  Scenario Outline: parse rejects non-integers, booleans, and values below 1
    When `<name>` is parsed from <raw>
    Then parsing raises SystemExit with message exactly "<name> must be a whole number 1 or more, got <rendered>"

    Examples:
      | name           | raw    | rendered |
      | MARESTAIL_JOBS | 0      | 0        |
      | MARESTAIL_JOBS | "0"    | '0'      |
      | MARESTAIL_JOBS | -1     | -1       |
      | MARESTAIL_JOBS | "-1"   | '-1'     |
      | MARESTAIL_JOBS | True   | True     |
      | MARESTAIL_JOBS | False  | False    |
      | MARESTAIL_JOBS | 2.5    | 2.5      |
      | MARESTAIL_JOBS | "2.5"  | '2.5'    |
      | MARESTAIL_JOBS | "four" | 'four'   |
      | MARESTAIL_JOBS | " 3"   | ' 3'     |
      | MARESTAIL_JOBS | [3]    | [3]      |
      | [run] jobs     | 0      | 0        |
      | [run] jobs     | "0"    | '0'      |
      | [run] jobs     | -1     | -1       |
      | [run] jobs     | "-1"   | '-1'     |
      | [run] jobs     | True   | True     |
      | [run] jobs     | False  | False    |
      | [run] jobs     | 2.5    | 2.5      |
      | [run] jobs     | "2.5"  | '2.5'    |
      | [run] jobs     | "four" | 'four'   |
      | [run] jobs     | " 3"   | ' 3'     |
      | [run] jobs     | [3]    | [3]      |

  Scenario: run_jobs resolves, validates both sources, and caps at CPU count
    When neither MARESTAIL_JOBS nor [run] jobs is set
    Then run_jobs returns None
    When [run] jobs is 3 and MARESTAIL_JOBS is unset
    Then run_jobs returns 3
    When [run] jobs is 3 and MARESTAIL_JOBS is "5"
    Then run_jobs returns 5
    When [run] jobs is 3 and MARESTAIL_JOBS is ""
    Then run_jobs returns 3
    When os.cpu_count is 2 and MARESTAIL_JOBS is "16"
    Then run_jobs returns 2 and stdout and stderr are empty
    When os.cpu_count returns None and MARESTAIL_JOBS is "16"
    Then run_jobs returns 1 and stdout and stderr are empty
    When MARESTAIL_JOBS is "0"
    Then run_jobs raises SystemExit with message "MARESTAIL_JOBS must be a whole number 1 or more, got '0'"
    When [run] jobs is 0 and MARESTAIL_JOBS is unset
    Then run_jobs raises SystemExit with message "[run] jobs must be a whole number 1 or more, got 0"
    When [run] jobs is 0 and MARESTAIL_JOBS is "5"
    Then run_jobs raises SystemExit with message "[run] jobs must be a whole number 1 or more, got 0"

  Scenario: pick obeys precedence and preserves raw language keys
    When MARESTAIL_JOBS is "3", section is "python", key is "mutation_workers" with value 8, and [run] jobs is 3
    Then pick returns 3
    When MARESTAIL_JOBS is unset, section is "python", key is "mutation_workers" with value 8, and [run] jobs is 3
    Then pick returns 8
    When MARESTAIL_JOBS is unset, section is "python", key is missing, [run] jobs is 3, and default is 4
    Then pick returns 3
    When MARESTAIL_JOBS is unset, section is "python", key is missing, [run] jobs is unset, and default is 4
    Then pick returns 4
    When MARESTAIL_JOBS is unset, section is "elixir", key is "muex_concurrency" with value 0, and [run] jobs is 3
    Then pick returns 0
    When os.cpu_count is 2, MARESTAIL_JOBS is unset, section is "rust", key is "mutation_jobs" with value 16, and [run] jobs is 3
    Then pick returns 16

  Scenario: each mutation gate passes the configured worker count to its tool
    Given os.cpu_count is 8
    When py.mutation runs with jobs unset
    Then mutmut receives arguments ["run", "--max-children", "4"]
    When py.mutation runs with [run] jobs = 3
    Then mutmut receives arguments ["run", "--max-children", "3"]
    When py.mutation runs with [python] mutation_workers = 8 and [run] jobs = 3
    Then mutmut receives arguments ["run", "--max-children", "8"]
    When py.mutation runs with [python] mutation_workers = 8 and MARESTAIL_JOBS=3
    Then mutmut receives arguments ["run", "--max-children", "3"]

    When ts.mutation runs with jobs unset
    Then stryker receives no "--concurrency" flag
    When ts.mutation runs with [run] jobs = 3
    Then stryker receives "--concurrency", "3" immediately after "--cleanTempDir", "always"

    When ex.mutation runs with jobs unset
    Then muex receives arguments ["--concurrency", "4"]
    When ex.mutation runs with MARESTAIL_JOBS=3
    Then muex receives arguments ["--concurrency", "3"]
    When ex.mutation runs with [elixir] muex_concurrency = 0
    Then muex receives no "--concurrency" flag

    When rb.mutation runs with jobs unset
    Then mutant receives command ["bundle", "exec", "mutant", "run"]
    When rb.mutation runs with [run] jobs = 3
    Then mutant receives command ["bundle", "exec", "mutant", "run", "--jobs", "3"]

    When cs.mutation runs with jobs unset
    Then dotnet stryker receives no "--concurrency" flag
    When cs.mutation runs with MARESTAIL_JOBS=3
    Then dotnet stryker receives "--concurrency", "3" immediately after "-r", "progress"

    When er.mutation runs with jobs unset
    Then the Erlang worker pool has max_workers 1
    When er.mutation runs with [run] jobs = 3
    Then the Erlang worker pool has max_workers 3
    And running five mutants with 3 workers produces a Result and er-mutation.json identical to running with 1 worker

    When rs.mutation runs with jobs unset
    Then cargo mutants receives arguments ["--jobs", "2"]
    When rs.mutation runs with MARESTAIL_JOBS=3
    Then cargo mutants receives arguments ["--jobs", "3"]
    When rs.mutation has mutation_args = ["--in-place"] and jobs unset
    Then cargo mutants receives no "--jobs" flag
    When rs.mutation has mutation_args = ["--in-place"] and MARESTAIL_JOBS=3
    Then cargo mutants receives no "--jobs" flag

    When java.mutation runs with jobs unset
    Then pitest receives argument "-Dthreads=2"
    When java.mutation runs with [run] jobs = 3
    Then pitest receives argument "-Dthreads=3"

  Scenario: rs and rb sort surviving findings before cutting to sixty
    When cargo mutants reports 70 surviving mutants across two files in shuffled completion order
    Then the findings list contains exactly 60 entries sorted by repo-relative path, integer line number, and finding text
    When mutant reports surviving results for subjects arriving in different orders
    Then the findings list contains the findings sorted by repo-relative path, integer line number, and finding text

  Scenario: runner verifies jobs before model selection and agent startup
    When I run `marestail run tasks/t.md` with MARESTAIL_JOBS="0"
    Then the process exits 1
    And stdout is empty
    And stderr is exactly "MARESTAIL_JOBS must be a whole number 1 or more, got '0'\n"
    And no agent session or model call is started
    When I run `marestail run tasks/t.md` with [run] jobs = 0 and MARESTAIL_JOBS unset
    Then the process exits 1
    And stdout is empty
    And stderr is exactly "[run] jobs must be a whole number 1 or more, got 0\n"

  Scenario: bad jobs inside a gate reports crashed gate and does not halt subsequent gates
    When marestail gate executes py.mutation with MARESTAIL_JOBS="0"
    Then the py.mutation Result is ok False with summary containing "crashed: SystemExit MARESTAIL_JOBS must be a whole number 1 or more, got '0'"
    And gates registered after py.mutation continue to run

  Scenario: configuration template, documentation, skills, and import layers
    Then `templates/marestail.toml` contains `[run]` with `# jobs = 4  # parallel workers for every mutation gate; MARESTAIL_JOBS wins over it`
    And `templates/marestail.toml` has commented `# mutation_workers = 4`, `# mutation_jobs = 2`, and `# mutation_threads = 2`
    And `README.md` documents the worker precedence rule, CPU capping, `--in-place`, and Maven `<threads>`
    And `README.md` environment table has the row for `MARESTAIL_JOBS`
    And `.claude/skills/add-language/SKILL.md` and `.agents/skills/add-language/SKILL.md` document the mutation parallel flag contract
    And `.importlinter` lists `marestail.jobs` in the layer with `marestail.context` and `marestail.ran_against`
    And `marestail.jobs` imports only `os`, `re`, and `marestail.config`
    And `tests/conftest.py` hermetic fixture removes `MARESTAIL_JOBS` from the environment

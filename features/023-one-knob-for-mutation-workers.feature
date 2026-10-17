Feature: one knob for mutation workers

  `[run] jobs` or `MARESTAIL_JOBS` sets how many mutants run at once.
  The variable wins over a language key, which wins over `[run] jobs`,
  which wins over today's default. The variable and `[run] jobs` are
  capped at `os.cpu_count() or 1` and print nothing. A language key is
  returned raw. `0` is an error. Only an empty `MARESTAIL_JOBS=` is unset.
  Jobs never changes which mutants run or how they are judged.

  The command builder still takes the worker count it is given.
  `build(13)` and the remote `build(16)` stay 13 and 16. The gate passes
  `pick` or `run_jobs` as that count. A skip does not read jobs, so a bad
  value does not turn a skip into a crash. `os.cpu_count()` is 8 unless a
  step says otherwise.

  Background:
    Given a temporary repo and neither `MARESTAIL_JOBS` nor `[run] jobs` unless a step sets them

  Scenario Outline: parse accepts a whole number 1 or more
    When `jobs.parse` is called with <raw> and name "<name>"
    Then it returns <expected>

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

  Scenario Outline: parse rejects anything else
    When `jobs.parse` is called with <raw> and name "<name>"
    Then it raises SystemExit "<name> must be a whole number 1 or more, got <rendered>"
    And stdout and stderr are empty

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

  Scenario: run_jobs reads both sources, and the variable wins
    When neither source is set
    Then `run_jobs` returns None
    When `[run] jobs` is 3 and the variable is unset
    Then `run_jobs` returns 3 and writes nothing
    When `[run] jobs` is 3 and `MARESTAIL_JOBS` is `5`
    Then `run_jobs` returns 5
    When `[run] jobs` is 3 and `MARESTAIL_JOBS` is empty
    Then `run_jobs` returns 3
    When `os.cpu_count` is 2 and `MARESTAIL_JOBS` is `16`
    Then `run_jobs` returns 2 and writes nothing
    When `os.cpu_count` returns None and `MARESTAIL_JOBS` is `16`
    Then `run_jobs` returns 1 and writes nothing
    When `MARESTAIL_JOBS` is `0`
    Then `run_jobs` raises SystemExit "MARESTAIL_JOBS must be a whole number 1 or more, got '0'"
    When `[run] jobs` is 0 and the variable is unset
    Then `run_jobs` raises SystemExit "[run] jobs must be a whole number 1 or more, got 0"
    When `[run] jobs` is 0 and `MARESTAIL_JOBS` is `5`
    Then `run_jobs` raises SystemExit "[run] jobs must be a whole number 1 or more, got 0"

  Scenario: pick caps the knob and leaves a language key raw
    When `MARESTAIL_JOBS` is `3`, `[python] mutation_workers` is 8 and `[run] jobs` is 3
    Then `pick(config, "python", "mutation_workers", 4)` returns 3
    When the variable is unset, `mutation_workers` is 8 and `[run] jobs` is 3
    Then that pick returns 8
    When the variable is unset, the key is absent, `[run] jobs` is 3 and the default is 4
    Then that pick returns 3
    When nothing is set and the default is 4
    Then that pick returns 4
    When the variable is unset and `muex_concurrency` is 0
    Then `pick(config, "elixir", "muex_concurrency", 4)` returns 0
    When `os.cpu_count` is 2, the variable is unset and `mutation_jobs` is 16
    Then `pick(config, "rust", "mutation_jobs", 2)` returns 16
    When `os.cpu_count` is 2 and `MARESTAIL_JOBS` is `16`
    Then `pick(config, "python", "mutation_workers", 4)` returns 2 and writes nothing
    When `os.cpu_count` is 2, the variable is unset, the key is absent and `[run] jobs` is 16
    Then that pick returns 2 and writes nothing
    When `MARESTAIL_JOBS` is `0` and `mutation_workers` is 8
    Then pick raises SystemExit "MARESTAIL_JOBS must be a whole number 1 or more, got '0'"
    When the key is absent and `[run] jobs` is 0
    Then pick raises SystemExit "[run] jobs must be a whole number 1 or more, got 0"
    When `MARESTAIL_JOBS` is `5` and `[run] jobs` is 0, with or without `mutation_workers` 8
    Then pick raises SystemExit "[run] jobs must be a whole number 1 or more, got 0"

  Scenario: py.mutation passes --max-children from pick
    When a full run has no language key and no knob
    Then the stub receives `["<repo>/.venv/bin/mutmut", "run", "--max-children", "4"]`
    When the same run has `MARESTAIL_JOBS=3`
    Then the stub receives that list with `"3"`
    When the same run has `[run] jobs = 3` and no variable
    Then the stub receives that list with `"3"`
    When `mutation_workers` is 8 and `[run] jobs` is 3
    Then the stub receives that list with `"8"`
    When `mutation_workers` is 8 and `MARESTAIL_JOBS=3`
    Then the stub receives that list with `"3"`
    And calling the recorded builder with 13 still returns `--max-children` `13`

  Scenario: ts.mutation appends --concurrency after BASE
    Given BASE is `["npx", "stryker", "run", "--reporters", "json,progress", "--tempDirName", ".stryker-tmp", "--cleanTempDir", "always", "--dryRunTimeoutMinutes", "120"]`
    When jobs are unset and `[ts] mutation_concurrency` is unset
    Then the stub receives BASE and the workers offered are `[0]`
    And the recorded builder called with 10 returns `BASE + ["--concurrency", "10"]`
    When `MARESTAIL_JOBS=3`
    Then the stub receives `BASE + ["--concurrency", "3"]`
    When `[run] jobs = 3` and the variable is unset
    Then the stub receives `BASE + ["--concurrency", "3"]`
    When the mutate targets are `a.ts` and `b.ts` and `MARESTAIL_JOBS=3`
    Then the stub receives `BASE + ["--concurrency", "3", "--mutate", "a.ts,b.ts"]`
    When those targets are set, `[run] jobs = 3` and the variable is unset
    Then the stub receives that same list
    When jobs are unset, `mutation_concurrency` is `"3"`, incremental is on and the target is `a.ts`
    Then the command is `BASE + ["--concurrency", "3", "--incremental", "--incrementalFile", "reports/stryker-incremental.json", "--mutate", "a.ts"]`
    When `mutation_concurrency` is `"9"`, incremental is on, the target is `a.ts` and jobs are unset
    Then the concurrency token is `"9"`
    When that same config has `MARESTAIL_JOBS=3`
    Then the concurrency token is `"3"`, still before `--incremental` and `--mutate`
    When that same config has `[run] jobs = 3` and the variable is unset
    Then the concurrency token is `"3"`, still before `--incremental` and `--mutate`

  Scenario: ex.mutation passes --concurrency from pick
    When a full run has no language key and no knob
    Then the local command is `watched("--no-filter", "--concurrency", "4")`
    And the remote builder called with 16 is `watched("--no-filter", "--concurrency", "16")`
    When `MARESTAIL_JOBS=3`
    Then the local command is `watched("--no-filter", "--concurrency", "3")` and the remote builder stays 16
    When `[run] jobs = 3` and the variable is unset
    Then the local command is that same list ending `"3"` and the remote builder stays 16
    When `muex_concurrency` is 0 and `[run] jobs` is 3
    Then the local command is `watched("--no-filter")` with no `--concurrency`
    When `muex_concurrency` is 0 and `MARESTAIL_JOBS=3`
    Then the local command ends `--concurrency`, `"3"`

  Scenario: rb.mutation puts --jobs before the subjects
    When the scoped session run has no knob
    Then the mutant command is `["bundle", "exec", "mutant", "run", "Admin::Boss*", "Named*", "User*"]`
    When `MARESTAIL_JOBS=3`
    Then it is `["bundle", "exec", "mutant", "run", "--jobs", "3", "Admin::Boss*", "Named*", "User*"]`
    When `[run] jobs = 3` and the variable is unset
    Then the mutant command is that same list
    And `["bundle", "info", "mutant"]` still precedes it

  Scenario: cs.mutation inserts --concurrency before the first -m
    When `command` is called as in `test_command` and jobs are unset
    Then the argv is `["stryker", "--skip-version-check", "--break-on-initial-test-failure", "--test-project", "<tests>", "--project", "App.csproj", "-O", "<out>", "-r", "json", "-r", "progress", "-m", "!**/Gen", "-m", "!**/Migr", "-m", "**/A.cs", "-m", "**/other/B.cs"]`
    When `command` is called the same way and `MARESTAIL_JOBS=3`
    Then `"--concurrency", "3"` is inserted after `"progress"` and before the first `"-m"`
    When `command` is called the same way, `[run] jobs = 3` and the variable is unset
    Then `"--concurrency", "3"` is inserted after `"progress"` and before the first `"-m"`
    When the full run from `test_reports_surviving_mutants` has jobs unset
    Then the stub's stryker argv ends `["-r", "progress", "-m", "!**/App/Gen"]` after `["-O", "<repo>/.marestail/stryker"]`
    When that full run has `MARESTAIL_JOBS=3`
    Then it ends `["-r", "progress", "--concurrency", "3", "-m", "!**/App/Gen"]`
    When that full run has `[run] jobs = 3` and the variable is unset
    Then it ends `["-r", "progress", "--concurrency", "3", "-m", "!**/App/Gen"]`

  Scenario: rs.mutation passes --jobs unless the run is in place
    When a full run has no `mutation_args` and no knob
    Then the stub receives `["cargo", "mutants", "--output", "<repo>/.marestail", "--no-shuffle", "--colors", "never", "--jobs", "2"]`
    When `MARESTAIL_JOBS=3`
    Then that list ends `"3"`
    When `[run] jobs = 3` and the variable is unset
    Then that list ends `"3"`
    When `mutation_jobs` is 4 and `[run] jobs` is 3 on a scoped `src/lib.rs` run
    Then the command still ends `["4", "--file", "src/lib.rs"]`
    When `mutation_jobs` is 4 and `MARESTAIL_JOBS=3`
    Then the command ends `["3", "--file", "src/lib.rs"]`
    When `mutation_args` is `["--in-place"]` and jobs are unset
    Then the command is `["cargo", "mutants", "--output", "<repo>/.marestail", "--no-shuffle", "--colors", "never", "--in-place"]`
    When `mutation_args` is `["--in-place"]` and `MARESTAIL_JOBS=3`
    Then the command is that same list
    When `mutation_args` is `["--in-place"]`, `[run] jobs = 3` and the variable is unset
    Then the command is that same list
    And each of those three contains `--in-place` and does not contain `--jobs`

  Scenario: java.mutation passes -Dthreads from pick
    When the two-thread full run has no knob
    Then the local argv is `["mvn", "-B", "-ntp", "test-compile", "org.pitest:pitest-maven:mutationCoverage", "-DtargetClasses=app.App,app.App$*,app.core.Core,app.core.Core$*", "-DoutputFormats=XML", "-DtimestampedReports=false", "-DreportsDirectory=<repo>/.marestail/pit", "-Dthreads=2", "-DtargetTests=app.*,app.core.*"]`
    And the builder called with 16 has `-Dthreads=16`
    When `MARESTAIL_JOBS=3`
    Then the local argv has `-Dthreads=3` and the builder called with 16 still has `-Dthreads=16`
    When `[run] jobs = 3` and the variable is unset
    Then the local argv has `-Dthreads=3` and the builder called with 16 still has `-Dthreads=16`
    When `mutation_threads` is 8 and `[run] jobs` is 3
    Then the local argv has `-Dthreads=8`
    When `mutation_threads` is 8 and `MARESTAIL_JOBS=3`
    Then the local argv has `-Dthreads=3`

  Scenario: er.mutation sizes a pool and keeps manifest order
    When jobs are unset
    Then `ThreadPoolExecutor` is constructed with `max_workers` 1
    When `MARESTAIL_JOBS=3`
    Then `max_workers` is 3
    When `[run] jobs = 3` and the variable is unset
    Then `max_workers` is 3
    And the pool class is bound as `er_mutation.ThreadPoolExecutor` and `map` is given the mutants
    When five mutants run under one worker and again under three, answered by command and not by a shared queue
    And mutant 1 line 1 fails `erlc` with exit 1, so it is `invalid`
    And mutant 2 line 2 compiles and `escript` exits 0, so it is `survived`
    And mutant 3 line 3 compiles and `escript` exits 1, so it is `killed`
    And mutant 4 line 4 compiles and `escript` exits 124, so it is `timeout`
    And mutant 5 line 5 compiles and `escript` exits 3, so it is `error`
    Then `ok` is false
    And the summary is `1 of 4 mutants not killed (1 failed to compile)`
    And the findings are `["src/a.erl:2 comparison mutant survived: < -> >="]`
    And `er-mutation.json` lists those five statuses in that order
    And those four are the same for one worker and for three
    And `Result.seconds` is not required to match
    And `test_time_budget` and `test_per_mutant_timeout` still pass with jobs unset

  Scenario: rs and rb sort before the cut of 60
    Given 70 survivors, `src/a.rs` lines 1 to 40 and `src/b.rs` lines 1 to 30, each `MissedMutant` named `swap` in function `add`, plus a successful baseline
    And the finding text is `<path>:<line> add: swap survived`
    When the outcomes are fed in that source order
    And again after `random.Random(0).shuffle` of that list
    Then both runs return the same 60 findings
    And the first is `src/a.rs:1 add: swap survived`
    And `src/a.rs:9 add: swap survived` comes before `src/a.rs:10 add: swap survived`
    And the 60th is `src/b.rs:20 add: swap survived`
    And `src/b.rs:21 add: swap survived` is absent
    And the summary is `70 of 70 mutants not killed`
    Given 70 mutant `subject_results`, `lib/a.rb` lines 1 to 40 identified `A#m:lib/a.rb:<line>` and `lib/b.rb` lines 1 to 30 identified `B#m:lib/b.rb:<line>`, each with one evil survivor
    When that report is judged in source order and again after `random.Random(0).shuffle`
    Then both runs return the same 60 findings
    And the first is `lib/a.rb:1 A#m: 1 mutant survived`
    And `lib/a.rb:9 A#m: 1 mutant survived` comes before `lib/a.rb:10 A#m: 1 mutant survived`
    And the 60th is `lib/b.rb:20 B#m: 1 mutant survived`
    And `lib/b.rb:21 B#m: 1 mutant survived` is absent
    And the summary is `70 of 70 mutants not killed`
    And py, ts, ex, cs, java and er do not reorder findings

  Scenario: a bad knob stops the run before the task is read
    When `pipeline_env` runs with jobs unset
    Then `jobs.run_jobs` is called once with the loaded config
    And the order is nice, then jobs, then the task read, then `pick_model`
    And `run_steps` is not called before that
    When `MARESTAIL_JOBS=0`
    Then the process raises SystemExit "MARESTAIL_JOBS must be a whole number 1 or more, got '0'"
    And the task is not read, `pick_model` is not called and `run_steps` is not called
    And stdout is empty
    When `[run] jobs` is 0 and the variable is unset
    Then the process raises SystemExit "[run] jobs must be a whole number 1 or more, got 0"
    And the same steps are skipped

  Scenario: a crashed mutation gate does not stop the next one
    When `py.mutation` raises SystemExit "MARESTAIL_JOBS must be a whole number 1 or more, got '0'" from its jobs call
    Then its result is not ok
    And its summary is `py.mutation crashed: SystemExit MARESTAIL_JOBS must be a whole number 1 or more, got '0'`
    And the rendered head is `[FAIL] py.mutation    ` plus that summary plus `  (0.0s)`, four spaces after the gate name, and `0.0` stands for the measured seconds to one decimal
    And `ts.mutation` still runs
    And its rendered line is `[ok  ] ts.mutation    skipped: no changed typescript sources  (0.0s)`
    And `marestail gate --only docs` with no `--tier` and no `[docs]` section does not read jobs
    And its stderr is `no gate ran: nothing in the fast tier matches --only and marestail.toml`

  Scenario: the template, the README, both skills and the layer
    Then `templates/marestail.toml` still has `# [run]` and `# nice = 19  # 0 leaves the process at the shell's priority; MARESTAIL_NICE overrides; children inherit`
    And the next line is `# jobs = 4  # parallel workers for every mutation gate; MARESTAIL_JOBS wins over it`
    And the python key line is `# mutation_workers = 4  # wins over [run] jobs for mutmut`
    And the rust key line is `# mutation_jobs = 2  # wins over [run] jobs for cargo-mutants`
    And the java key line is `# mutation_threads = 2  # wins over [run] jobs for PIT`
    And the paragraph after the one that starts `Mutation testing always runs on the diff` says `[run] jobs` or `MARESTAIL_JOBS` sets the parallel mutation workers, the variable wins, then `[python] mutation_workers` (mutmut `--max-children`), `[elixir] muex_concurrency` (muex `--concurrency`), `[rust] mutation_jobs` (cargo-mutants `--jobs`) and `[java] mutation_threads` (PIT `-Dthreads`), then `[run] jobs`, then the tool default
    And it names StrykerJS `--concurrency`, mutant `--jobs`, Stryker.NET `--concurrency` and the Erlang pool
    And it says the knob is capped at the CPU count, 0 is an error, and the mutant set and the verdict do not change
    And it says cargo-mutants is passed no `--jobs` when `--in-place` is in `mutation_args`
    And it says a pom `<threads>` beats `-Dthreads` and `templates/java-pitest.xml` sets none
    And the environment table has a `MARESTAIL_JOBS` row whose cell is `parallel workers for every mutation gate (1 or more, capped at the CPU count; empty means unset); wins over [run] jobs and the per-language worker keys`
    And both add-language skills, under `The gate contract`, have one bullet: a mutation gate passes its tool's parallel flag from `jobs.pick(ctx.config, "<section>", "<key>", <today's default>)`, or from `jobs.run_jobs(ctx.config)` when the language has no key of its own, and names that flag in the README
    And those two skill files stay identical
    And the `.importlinter` layer line that contains `marestail.context` and `marestail.ran_against` also contains `marestail.jobs`
    And `marestail.jobs` imports only `os`, `re` and `marestail.config`
    And `tests/conftest.py` hermetic calls `monkeypatch.delenv("MARESTAIL_JOBS", raising=False)`

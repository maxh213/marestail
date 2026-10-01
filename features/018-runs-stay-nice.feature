Feature: marestail run yields the machine

  `marestail run` drops itself to nice 19, idle I/O and an OOM score of 500
  after loading config and before picking a model or starting an agent.
  Children inherit, so every agent CLI, gate and Stop hook that run starts
  is niced with it. The port prints nothing. `marestail gate`, `marestail watch`,
  `marestail perf run` and a hand-run `marestail gate --hook` stay at the
  shell's priority. There is no `marestail hook` subcommand.

  `parse` returns `None` for off, the integer itself for 1 and above (`99`
  stays `99`, and so does the string `"99"`), and raises `SystemExit` for a bad value. `level` is
  `min(parsed, 19)`, or off when `parse` returns `None`. Boolean `True`
  is 19, not 1. The environment is always a string, so `MARESTAIL_NICE=true`
  is rejected. `apply` writes the characters `500` and no newline; the
  kernel reads that proc file back with a newline, so a live
  read strips to `500`. A `tmp_path` stand-in contains exactly `500`.

  Background:
    Given a temporary git repo whose `marestail.toml` has `[git] base = "main"`, `[perf] enabled = false` and `[practices] enabled = false`, and `tasks/t.md`
    And the agent is a stub on `MARESTAIL_CLAUDE` with `MARESTAIL_AGENT=claude`, writing its own nice, `ionice -p` output and `oom_score_adj` to a file, then a `VERDICT: PASS`
    And the launcher is nice 0, ionice `best-effort: prio 4` and `oom_score_adj` `0`, and `MARESTAIL_NICE` is unset unless a step sets it

  Scenario: a default run is nice 19, idle, oom 500, and says nothing about it
    Given `marestail.toml` has no `[run]` table
    When I run `marestail run tasks/t.md --from critic --to critic --auto --retries 1`
    Then the exit code is 0
    And stdout contains `pipeline complete` and does not contain `oom_score_adj`, `ionice` or `setpriority`
    And stderr is empty
    And the stub reads nice `19`, ionice output `idle`, and oom value `500`

  Scenario Outline: a numeric level is capped at 19 and then applied
    When the source is <source>
    Then `level` is <level>
    And `apply` calls `os.setpriority(os.PRIO_PROCESS, 0, <level>)`, then `ionice -c 3 -p` of its own pid with `check=False` and captured output, then writes the characters `500`

    Examples:
      | source | level |
      | no config | 19 |
      | empty config | 19 |
      | toml `[run]` with no `nice` key | 19 |
      | toml `nice = true` | 19 |
      | toml `nice = 5` | 5 |
      | toml `nice = 10` | 10 |
      | toml `nice = "10"` | 10 |
      | toml `nice = 99` | 19 |
      | `MARESTAIL_NICE=7` and toml `nice = 19` | 7 |
      | `MARESTAIL_NICE=99` and toml `nice = 5` | 19 |

  Scenario Outline: an off value does not touch priority, I/O class or the OOM score
    When the source is <source>
    Then `level` is off
    And `apply` returns before `setpriority`, `ionice` and the oom write

    Examples:
      | source |
      | toml `nice = false` |
      | toml `nice = 0` |
      | toml `nice = "0"` |
      | toml `nice = ""` |
      | value `None` |
      | `MARESTAIL_NICE=0` and toml `nice = 19` |
      | `MARESTAIL_NICE=` empty and toml `nice = 19` |

  Scenario: config and the environment reach a real run
    When toml `nice = 5` and `MARESTAIL_NICE` is unset
    Then the same critic run exits 0, stderr is empty, and the stub reads `5`, `idle`, `500`
    When `MARESTAIL_NICE=7` and toml `nice = 19`
    Then that run exits 0, stderr is empty, and the stub reads `7`, `idle`, `500`
    When `MARESTAIL_NICE=0` and toml `nice = 19`
    Then that run exits 0, stderr is empty, and the stub reads `0`, `best-effort: prio 4`, `0`
    When `MARESTAIL_NICE` is set to the empty string and toml `nice = 19`
    Then that run exits 0, stderr is empty, and the stub reads the same off triple

  Scenario Outline: a bad value stops the run before any agent
    When the source is <source>
    Then the process exits 1
    And stdout is empty
    And stderr is exactly `<message>\n`
    And the stub is not started

    Examples:
      | source | message |
      | `MARESTAIL_NICE=high` | nice must be an integer 0-19, got 'high' |
      | `MARESTAIL_NICE=1.5` | nice must be an integer 0-19, got '1.5' |
      | `MARESTAIL_NICE=-4` | nice must be an integer 0-19, got -4 |
      | `MARESTAIL_NICE=true` | nice must be an integer 0-19, got 'true' |
      | `MARESTAIL_NICE=false` | nice must be an integer 0-19, got 'false' |
      | toml `nice = -1` | nice must be an integer 0-19, got -1 |
      | toml `nice = 1.5` | nice must be an integer 0-19, got 1.5 |
      | toml `nice = "high"` | nice must be an integer 0-19, got 'high' |

  Scenario: a bad nice value is raised before a bad model
    Given `MARESTAIL_NICE=high`
    When I run `marestail run tasks/t.md --from critic --to critic --auto --model dandelion/route --agent grok`
    Then the exit code is 1
    And stderr is exactly `nice must be an integer 0-19, got 'high'\n`
    And stderr does not contain `drop --agent`
    And the stub is not started

  Scenario: a bad model still reports its own message when nice is valid
    Given `MARESTAIL_NICE` is unset and toml has no `[run] nice`
    When I run `marestail run tasks/t.md --from critic --to critic --auto --model dandelion/route --agent grok`
    Then the exit code is 1
    And stdout is empty
    And stderr is exactly `--model dandelion/route picks the backend and effort before every session; drop --agent and --effort\n`
    And the stub is not started

  Scenario: an ionice that exits 3 still lets the run finish
    When that critic run's `PATH` starts with an `ionice` that exits 3
    Then the exit code is 0 and stderr is empty
    And stdout contains `pipeline complete`
    And the stub file is `19`, `ionice-failed`, `500`

  Scenario: gate, watch, perf and a hand-run hook stay at the shell's priority
    When I run `marestail gate --tier fast --only docs` from the launcher
    Then the exit code is 0 and stdout is exactly an ok line, a blank line and `GATE PASSED`
    And the ok line matches `^\[ok  \] docs           docs match the code  \(\d+\.\d+s\)$`
    And there is no `scope:` line
    And the process is still nice 0, ionice `best-effort: prio 4` and oom value `0`
    When I run `marestail watch /tmp` with stdin from `/dev/null` and stdout a pipe
    Then the exit code is 1 and stderr contains `cbreak() returned ERR`
    And curses may write screen escapes to stdout before that error
    And a same-process call that catches that error is still nice 0, ionice `best-effort: prio 4` and oom value `0`
    When I run `marestail perf run nope --tree HEAD` in a checkout with no `.marestail/trees.json`
    Then the exit code is 2, stdout is empty, and stderr is exactly `no perf run in progress\n`
    And the process is still nice 0, ionice `best-effort: prio 4` and oom `0`
    When I run `marestail gate --hook` with stdin `{}` and every fast gate passes
    Then the exit code is 0, stdout is empty, stderr is empty, and the process is still nice 0 and oom `0`
    And `nice.apply` has one production caller, `run_pipeline`, the statement after `config = config_module.load(Path.cwd())` and before `pick_model`

  Scenario: a missing marestail.toml still fails before nice
    Given the cwd is `/tmp/nice-qa-empty` and that directory has no `marestail.toml`
    When I run `marestail run tasks/t.md --from critic --to critic --auto`
    Then the exit code is 1
    And stderr is exactly `no marestail.toml found above /tmp/nice-qa-empty\n`
    And the process is still nice 0 and oom `0`

  Scenario: the README gains main's paragraph and the environment row
    Then the first paragraph after the `## Use` example command block, before the `--effort` paragraph, is exactly, with U+2013 in both `1–19`s:
      """
      `marestail run` starts at nice 19 with idle I/O and a raised OOM score, so a fleet of pipelines yields the CPU and disk to you and is first in line if the kernel needs memory. Children inherit. `[run] nice = 0` or `MARESTAIL_NICE=0` turns it off; any other integer 1–19 is the level (`MARESTAIL_NICE` wins over the file). `marestail watch` and `marestail gate` stay at the shell's priority.
      """
    And the environment table gains this row and keeps every existing row, again with U+2013:
      """
      | `MARESTAIL_NICE` | priority for `marestail run`: 0 turns it off, 1–19 is the nice level (default 19); wins over `[run] nice` |
      """

  Scenario: import-linter lists the new module on the ran_against layer
    Then the colon-separated layer line that lists `marestail.ran_against` also lists `marestail.nice`
    And `marestail.nice` imports no `marestail` module except `marestail.config`
    And `DEFAULT` is 19, `OOM_SCORE` is 500 and `ENV` is `MARESTAIL_NICE`

  Scenario: pytest covers every branch without renicing itself
    Then `tests/test_nice.py` stubs `os.setpriority`, `subprocess.run` and the `/proc` path and builds `Config(root=tmp_path, raw=...)`
    And it has one parse assertion for `True`, `False`, `None`, `""`, `0`, `"0"`, `10`, `"10"`, `99` and `"99"` (both return 99, uncapped), `"high"`, `-1`, `"-4"`, `"1.5"` and `1.5`, each `SystemExit` matching the message above
    And its level assertions are the source tables above
    And its apply assertions are the default call, the off call, a non-zero `ionice` exit that still writes `500`, and each of `setpriority` `OSError`, `setpriority` `AttributeError`, `ionice` `OSError` and the oom write `OSError`, with the later steps still running
    And `pipeline_env` in `tests/test_runner_flow.py` stubs `nice.apply`, records one call with the loaded config before `pick_model`, and never calls the real `apply`
    And a bad nice value raises before `run_steps`
    And `tests/test_cli.py` asserts `marestail gate` never calls `nice.apply`
    And `tools/test-nice.py` is not added
    And the existing `run_pipeline` tests still pass

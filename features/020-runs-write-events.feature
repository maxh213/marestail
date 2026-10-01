Feature: Runs write one event line at every step boundary

  Every `marestail run` appends JSON lines to `.marestail/runs/<task>/events.jsonl`.
  `<task>` is the task file's stem. The runner only appends. It never truncates,
  reads, copies or pushes the file. `archive_handoffs` moves handoffs and
  `start-commit` on completion and leaves `events.jsonl` beside `timeline.json`.
  A skipped run that completes with no handoffs directory still unlinks
  `start-commit` and still leaves `events.jsonl`. A stopped run leaves the
  handoffs directory where it is, and `archive` is null.

  `marestail/events.py` imports only the standard library and `marestail.shell`.
  It has no clock: the caller passes `at` from `timeline.utc_now()`. The writer
  is a `Log` constructed with the events path and kept on the `Run`, so the
  warn-once flag is per run. `Log.append(event, fields, at, pid)` writes one
  line and stores `at` and `pid` unchanged. Two `Log`s warn separately.

  Each line is one `os.write` of `json.dumps` (default separators, no
  `sort_keys`) plus `\n`, through `os.open` with `O_WRONLY | O_APPEND | O_CREAT`
  and mode `0o644`, then `os.fsync`, then `os.close`. The `os` module attributes
  are used, so a test can patch `marestail.events.os`. The parent directory is
  created if missing. An open that succeeds is closed even when write or fsync
  raises. `OSError` prints once per `Log`, on stderr,
  `warning: cannot write run events to <path>: <error>`, and does not raise.
  Later appends on that `Log` still try and stay silent. Nothing an events
  write does changes stdout, the exit code or the pipeline's path.

  Keys, in order, are `v`, `at`, `event`, `pid`, then the event's own fields
  in the order below. Every listed field is present. No value is an omitted
  key; it is JSON `null`. `v` is the integer 1. `at` matches
  `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z`. `pid` is the integer
  `os.getpid()` of the process that called `run_pipeline` (`bin/marestail`
  execs Python). Paths are repo-relative with `/` when the file is under the
  config root, and absolute otherwise. A path that is not under the root must
  not raise. In particular `Path("t.md").resolve()` is not under `pipeline_env`'s
  `tmp_path`.

  | event | fields after pid |
  | run-start | task, task_path, branch, base, start_commit, from, to, scope, agent, model, effort |
  | step-start | step, role, kind, attempt |
  | step-end | step, role, attempt, outcome, bounce_to, head, handoff, seconds |
  | wait | reason, until |
  | proposal | step, path, files |
  | run-end | result, exit_code, head, stopped_at, reason, archive |

  `branch` is `git branch --show-current`, or null when that exits non-zero or
  prints nothing. The git error is not stored. `base` is
  `config.get("git", "base", "origin/master")`. `start_commit` is `head(config)`
  for this run, including a resume. Outside a repository that string is
  whatever `head()` returns, not null, and the test does not pin git's wording.
  The first start stays in `runs/<task>/start-commit` until a completion moves
  or unlinks it. `from` and `to` are the first and last roles of the window
  actually given to `run_steps`. `scope` is `hyper` when `state.hyper`, else
  `hard` when `state.hard`, else `changed` when `state.scope_changed`, else
  `all`. An unscoped run is `all` even though `state.scope_name` is `changed`.
  `agent` is null when `state.route` is set, otherwise `resolve_agent(state)`
  (so no `--agent` still records `claude`). `model` is `state.route` when
  routed, else the raw `state.model`, not `model_name()`; null when neither
  `--model` nor `[agent] model` gave one. `effort` is `state.effort`, null when
  unset. A routed run still rejects `--agent` and `--effort`.

  `window()` runs before `run-start`, because `from` and `to` come from it.
  `run-start` is after `record_start` and before `reproduce_first`. The SIGTERM
  handler is installed just before `run-start` and restored in a `finally` on
  every return and every exception. No SIGINT handler is installed. A failure
  before the handler writes no events file: bad nice, bad front matter, bad
  `--model`/`--agent`, bad scope, a missing focus path. An unknown role fails
  inside `window()`, so `start-commit` may already exist and `events.jsonl`
  does not. Replacing `run_steps` does not write `run-end`. `run-start` is
  still written and must not raise when the root is not a git repository.

  `step` is `report.stem`. `kind` is `worker` or `judge`. `attempt` is the
  integer printed in `== <role> (<step>) attempt N`. `step-start` is written
  after `started_at` is taken and before `invoke`, with `at` equal to that
  `started_at`. Workers write it in `worker_attempt`. Judges write it in
  `judged`. The judge gate in `run_judge` stays before the first `step-start`.

  `timeline.record` returns the step dict it appended and changes nothing else.
  Same keys, same order, `agent` and `verdict` still omitted when absent.
  `step-end.at` is that step's `ended_at`. `seconds` is
  `round((ended_at - started_at).total_seconds(), 3)` from those two strings.
  `head` is `head(config)` read after `record_attempt`. `handoff` is the
  report path when the file exists then, else null; a later archive does not
  rewrite it. `bounce_to` is null unless `outcome` is `bounce`.

  Worker `step-end` is written in `run_worker`, which is what knows whether
  the problems end the run. `done` means no problems. `failed` means the same
  role will be tried again. `stopped` means `run_worker` returns False: the
  repeat guard, or the last attempt `--retries N` allows. `--retries 0` is
  unlimited (`10000`), not zero attempts. The guard wins when both would fire,
  including `--retries 3` with the same problems three times. A judge's
  `outcome` is `pass`, `bounce` or `failed`, never `stopped`. `pass` and
  `bounce` are verdicts `commit_verdict` committed. A judge gate failure is a
  `bounce` to `judge.bounce_to` and does not print `GATE FAILED`. A missing
  verdict, a rejected perf verdict, or `VERDICT: AUTHOR` is `failed`.
  `perf-author-N` is not a step. A skipped judge writes no step. When
  `--retries N` runs out on a judge with no verdict, the made-up bounce commits
  nothing and writes no event; the next line is the rework's `step-start`.

  `wait` is written from `note_wait`. `reason` is `rate-limit` or
  `dandelion-unrouted`. `until` is `at` plus the `LIMIT_WAIT_SECONDS` then in
  force, in the same timestamp form. A duration of 0 does not prove the
  addition, because a copied `at` would pass. The check uses 90, patches
  `runner.time.sleep` so the run does not block, and sets `runner.LIMIT_WAITS`
  to 12 rather than trusting `MARESTAIL_LIMIT_WAITS`. It sits between that
  attempt's `step-start` and `step-end` and has no `step`. The
  `still rate limited after 12 waits` line writes nothing more.

  `proposal` is written only after `proposal.write_text` on the justified
  frozen-file path. `files` is the frozen list, in that order. A frozen change
  with no `## Config change` section, and a frozen edit left uncommitted, write
  no `proposal` line.

  `run-end` is written after the ending line is printed and before
  `proposals_summary`. `archive` is the repo-relative directory
  `archive_handoffs` actually moved the handoffs to, or null. `stopped_at` is
  the window role `run_steps` was on, or null when the run completed or failed
  before any step. During a rework the worker's own events still name the
  worker, and `stopped_at` stays the judge. `exit_code` is an integer.

  | ending | result | exit_code | stopped_at | reason |
  | every step passed, QA absent or said `app`, and `state.unseen` is false | complete | 0 | null | null |
  | every step passed, QA absent or said `app`, and `state.unseen` is true | complete | 3 | null | `pipeline complete, NOT verified by eye` |
  | QA said `harness` or `nothing` | complete | 3 | null | the line `ending_for` printed; `unseen` does not replace it |
  | a step returned False | stopped | 1 | that step | the stop line below |
  | the approval after the critic was declined | stopped | 1 | critic | `not approved` |
  | the bug could not be reproduced | stopped | 1 | the role in the printed line | that line, word for word |
  | any other exception | failed | 1, or the int `SystemExit.code` | the current step, or null | `<ExceptionType>: <first line>` |
  | KeyboardInterrupt | interrupted | 130 | the current step, or null | `SIGINT` |
  | the SIGTERM exception | interrupted | 143 | the current step, or null | `SIGTERM` |

  `SystemExit` with code `None` records 0. A non-integer code records 1. The
  exception is re-raised unchanged, except SIGTERM, which raises
  `SystemExit(143)` after the line is written. KeyboardInterrupt is re-raised,
  not turned into `SystemExit`. The SIGTERM handler raises a `BaseException`
  that is not an `Exception`, not `KeyboardInterrupt` and not `SystemExit`.
  SIGKILL writes nothing. A run is the `run-start` plus later lines with the
  same pid. The latest run of a task starts at the last `run-start`.

  Stop lines that are already printed are the event `reason` word for word.
  These three are new and are not printed: `<role> used all <N> attempts`
  (plural even when N is 1; N is the attempt limit, so `--retries 0` would say
  10000, and a judge never says it), `<judge> bounced to <target>, which is not
  a worker`, and `<judge> ran <MAX_JUDGE_ROUNDS> rounds without a pass`.
  `MAX_JUDGE_ROUNDS` stays 1000 in production. Stdout does not gain the string
  `events.jsonl`. A normal run's stderr is empty.

  The plain window, with no `[visual]` section, is specifier, critic, coder,
  cleaner, architect, design, practices, perf, hardener, qa. `--scope hyper`
  is specifier, critic, coder, architect, blast, hardener, qa. `visual` is
  inserted after hardener only when a `[visual]` section exists.

  In-process tests build a git repo the way `tests/test_timeline.py` does,
  patch `config_module.load` to `Config(root=repo, raw=...)`, stub
  `runner.nice.apply` except where the nice error itself is under test, and
  replace `runner.invoke`. Where the window includes a tier or the coder's
  audit, patch `runner.run_gates` and `runner.audit.problems`. Patch
  `runner.hunks.problems` when `--scope hyper` reaches coder or architect.
  No test calls an agent CLI. Tests that already ignore events may leave the
  file in `tmp_path`. The coder adds `tests/test_events.py` and extends the
  runner tests. The coder does not add files under `qa/`.

  Scenario: a specifier and a passing critic record a complete run
    Given a git repo on branch "main" whose marestail.toml sets `[git] base` to "main", `[perf] enabled` to false and `[practices] enabled` to false
    And "tasks/t.md" is `# Add one\n` with no front matter
    And HEAD before the run is commit S
    When `run_pipeline` is called on "tasks/t.md" with `--from specifier --to critic`, `--auto`, `--retries 0`, `--model m` and `--agent claude`
    And the specifier invoke commits `features/x.feature` with subject "add feature" and writes the handoff
    And the critic invoke writes `VERDICT: PASS`
    Then the events are exactly:
      | event | step | marker |
      | run-start | - | - |
      | step-start | 01-specifier | - |
      | step-end | 01-specifier | done |
      | step-start | 02-critic | - |
      | step-end | 02-critic | pass |
      | run-end | - | complete |
    And run-start has task "t", task_path "tasks/t.md", branch "main", base "main", start_commit S, from "specifier", to "critic", scope "all", agent "claude", model "m", effort null
    And 01-specifier is kind "worker" attempt 1, and 02-critic is kind "judge" attempt 1
    And 01-specifier's handoff is ".marestail/handoffs/t/01-specifier.md" and its bounce_to is null
    And 01-specifier's head is the SHA the critic invoke read on entry, and `git log -1 --format=%s` of it is "[claude/m] add feature"
    And 02-critic's head is a new commit whose subject is "[claude/m] critic verdict: PASS", different from the specifier head
    And 02-critic's handoff is ".marestail/handoffs/t/02-critic.md" and its bounce_to is null
    And each step-end head equals HEAD read at the next invoke's entry, and the last equals HEAD after the run
    And each step-start `at` equals that timeline step's started_at, each step-end `at` equals its ended_at, and seconds is their difference rounded to 3 places
    And run-end has exit_code 0, stopped_at null, reason null, and archive matching `^\.marestail/runs/t/handoffs-\d{8}T\d{6}(-\d+)?$`
    And the archive contains "01-specifier.md" and "02-critic.md", ".marestail/handoffs/t" is gone, and "runs/t/start-commit" now lives in the archive
    And "runs/t/events.jsonl" and "runs/t/timeline.json" stay in the run folder
    And stdout contains "== specifier (01-specifier) attempt 1", "== critic (02-critic) attempt 1", "   verdict PASS" and "pipeline complete"
    And stdout ends with "pipeline complete\n\n" and does not contain "events.jsonl", "GATE FAILED" or "NOT verified"
    And stderr is empty and the return value is 0
    And when the run-end line is written, stdout already contains "pipeline complete", and `proposals_summary` runs only after that line is in the file
    And every line has v 1, pid equal to `os.getpid()`, `at` in the form above, and exactly the keys of its event in the listed order
    And each raw line equals `json.dumps` of its object plus "\n"

  Scenario: a second run appends, and a broken first line is left in place
    Given the same repo and a file that already contains "not json\n"
    When the passing run above is executed twice
    Then the first line is still "not json"
    And the file has two run-start lines, both with this process's pid
    And the second run's lines follow the second run-start
    And the two start_commit values differ, because the first run moved HEAD
    And the writer never reads the file back

  Scenario: a bounce returns to the specifier and then passes
    Given the same repo and window as the passing run
    When the first critic writes "VERDICT: BOUNCE\n1. scenario missing\n" and the second writes "VERDICT: PASS\n"
    And both specifier invokes write a handoff and leave the tree clean
    Then the events are exactly:
      | event | step | marker |
      | run-start | - | - |
      | step-start | 01-specifier | - |
      | step-end | 01-specifier | done |
      | step-start | 02-critic | - |
      | step-end | 02-critic | bounce |
      | step-start | 03-specifier | - |
      | step-end | 03-specifier | done |
      | step-start | 04-critic | - |
      | step-end | 04-critic | pass |
      | run-end | - | complete |
    And 02-critic's bounce_to is "specifier" and 04-critic's bounce_to is null
    And stdout contains "   verdict BOUNCE to specifier" and "   verdict PASS" and "pipeline complete"

  Scenario: the same critic findings twice stop at the critic
    Given the same repo and window
    When both critic invokes write "VERDICT: BOUNCE\n1. scenario missing\n"
    And both specifier invokes write a handoff
    Then the events end:
      | event | step | marker |
      | step-start | 04-critic | - |
      | step-end | 04-critic | bounce |
      | run-end | - | stopped |
    And there is no 05-specifier
    And 04-critic's bounce_to is "specifier"
    And run-end has exit_code 1, stopped_at "critic" and reason "critic repeated the same findings twice; the worker is not making progress, stopping for a human"
    And stdout contains that reason and "pipeline stopped at critic"
    And the return value is 1

  Scenario: a worker that never writes a handoff trips the repeat guard
    Given the same repo and `--from specifier --to specifier` with `--retries 0`
    When every specifier invoke writes nothing
    Then there are three step-start lines for "01-specifier", attempts 1, 2 and 3, all kind "worker"
    And their step-end outcomes are "failed", "failed", "stopped"
    And every handoff is null and every bounce_to is null
    And run-end is stopped, exit_code 1, stopped_at "specifier", reason "specifier got the same problems back 3 times in a row; the worker is not making progress, stopping for a human"
    And stdout contains that reason, "pipeline stopped at specifier" and "missing handoff .marestail/handoffs/t/01-specifier.md"
    And stdout does not contain "used all"
    And the handoffs directory is still there and archive is null

  Scenario: retries and the repeat guard choose different reasons
    Given `--from coder --to coder` and `runner.run_gates` returning a failing py.tests result
    When `--retries 1` and the coder writes no handoff
    Then the only step-end is "01-coder" attempt 1 outcome "stopped"
    And the reason is "coder used all 1 attempts"
    And stdout contains "GATE FAILED: py.tests" and "pipeline stopped at coder" and does not contain "used all 1 attempts"
    When `--retries 2` and the same missing handoff happens twice
    Then the outcomes are "failed" then "stopped" and the reason is "coder used all 2 attempts"
    When `--retries 3` and the same missing handoff happens three times
    Then the third outcome is "stopped" and the reason is the coder repeat-guard line, not "coder used all 3 attempts"
    When `--retries 2`, the first invoke writes nothing and the gate fails, and the second writes a handoff, the gate returns no results, and `runner.audit.problems` returns []
    Then the outcomes are "failed" then "done" and run-end is complete with exit_code 0
    And without that audit stub the second outcome is "stopped" with reason "coder used all 2 attempts", because the handoff exists, coder has `audit=True`, and the fresh test repo has no `features/*.feature`

  Scenario: a justified frozen edit records a proposal and a revert
    Given the passing specifier/critic window
    When the first specifier commit appends "\n\n[events]\nkeep = false\n" to "marestail.toml" and writes a handoff whose "## Config change" section is "Allow the events file."
    And the second specifier commits `features/x.feature`, writes a handoff and does not touch "marestail.toml"
    And the critic writes `VERDICT: PASS`
    Then the events are exactly:
      | event | step | marker |
      | run-start | - | - |
      | step-start | 01-specifier | - |
      | proposal | 01-specifier | - |
      | step-end | 01-specifier | failed |
      | step-start | 03-specifier | - |
      | step-end | 03-specifier | done |
      | step-start | 04-critic | - |
      | step-end | 04-critic | pass |
      | run-end | - | complete |
    And the proposal path is ".marestail/handoffs/t/02-proposal.md" and files is ["marestail.toml"]
    And 01-specifier's head is the revert commit, whose subject is "[claude/m] Revert change to frozen files by 01-specifier, recorded as a proposal"
    And stdout contains "marestail.toml: frozen, reverted. Your reason was recorded as .marestail/handoffs/t/02-proposal.md for a human to consider after the run. Find a way within the current configuration."
    And stdout contains "## Config changes the agents asked for and were refused"
    And that summary is printed after run-end is in the file
    And the archived folder contains "02-proposal.md", while the proposal event still names the handoffs path

  Scenario: a frozen edit without a justification, and a dirty one, write no proposal
    Given `--from specifier --to specifier` and `--retries 1`
    When the invoke commits a "marestail.toml" change and the handoff has no "## Config change" section
    Then there is no proposal event
    And the step-end head's subject is "[claude/m] Revert change to frozen files by 01-specifier" and does not contain "recorded as a proposal"
    And stdout contains "marestail.toml is frozen for specifier; reverted. Work within the current configuration."
    When instead the invoke dirties "marestail.toml" and does not commit it
    Then there is no proposal event and HEAD does not gain a revert commit
    And stdout contains "marestail.toml is frozen for specifier" and does not contain "reverted"

  Scenario: a disabled or rulebook-less judge writes no step
    Given `--from <judge> --to <judge>` and `--auto`
    When the toml and tree are the row below
    Then stdout contains the skip line and "pipeline complete", and does not contain "== "
    And the events are only run-start and run-end complete, exit_code 0, stopped_at null, reason null, archive null
    And from and to are that judge, scope is "all", and "runs/<task>/start-commit" is gone while events.jsonl remains
    And no agent invoke runs

    Examples:
      | judge | setup | skip line |
      | practices | `[practices] enabled = false` | practices disabled in marestail.toml; skipping |
      | practices | practices left enabled, and no guidance file | practices: no guidance files; skipping |
      | design | no design section and no pattern rulebooks | design: no pattern rulebooks; skipping |
      | perf | `[perf] enabled = false` | perf disabled in marestail.toml; skipping |

  Scenario: perf authoring is not a step, and AUTHOR fails the attempt
    Given perf left enabled, no `perf/bench_*` file, `--from perf --to perf` and `--retries 2`
    And `perf_db.prepare` and `perf_db.release` do nothing that needs a database
    When the invokes are "perf-author-1", then "01-perf" writing "VERDICT: AUTHOR", then "perf-author-1", then "02-perf" writing "VERDICT: PASS"
    Then stdout contains "   perf-author-1: benches unchanged" twice and does not contain "== perf (perf-author-1)"
    And the events are run-start, step-start "01-perf" judge attempt 1, step-end "01-perf" failed, step-start "02-perf" judge attempt 2, step-end "02-perf" pass, run-end complete
    And the failed head equals start_commit, the pass head is the verdict commit, and the failed handoff is ".marestail/handoffs/t/01-perf.md"
    And run-start has from "perf" and to "perf"

  Scenario: a hardener rework that makes no progress stops at the hardener
    Given `--from hardener --to hardener`, `--retries 0`, `runner.run_gates` returning [] and `runner.audit.problems` returning []
    When the hardener writes "VERDICT: BOUNCE\n1. same problem\n" and every coder invoke writes nothing
    Then the events are run-start, step-start "01-hardener", step-end "01-hardener" bounce with bounce_to "coder", three "02-coder" attempts with outcomes "failed", "failed", "stopped", and run-end stopped
    And run-end has exit_code 1, stopped_at "hardener" and reason "coder got the same problems back 3 times in a row; the worker is not making progress, stopping for a human"
    And the coder events have role "coder"
    And stdout contains "pipeline stopped at hardener" and that reason
    And there is only one hardener attempt

  Scenario: a hardener gate failure bounces to the coder and then repeats
    Given the same hardener window, with `runner.run_gates` returning a failing py.tests result for tier "full" and [] for "fast"
    And `runner.audit.problems` returning []
    When every hardener invoke writes "VERDICT: PASS\n" and the coder writes a handoff
    Then 01-hardener and 03-hardener are bounce with bounce_to "coder", 02-coder is done, and there is no 04-coder
    And run-end is stopped at "hardener" with reason "hardener repeated the same findings twice; the worker is not making progress, stopping for a human"
    And the step-end objects have no gate key
    And the timeline steps for the hardener attempts have gate `[{"name": "py.tests", "seconds": 1.0, "ok": false}]`
    And stdout contains "   verdict BOUNCE" and does not contain "GATE FAILED"
    And the hardener gate ran before the first hardener step-start

  Scenario: a bounce to a judge writes the new reason and does not print it
    Given `--from critic --to critic`
    When the critic writes "VERDICT: BOUNCE hardener\n1. not a worker\n"
    Then the step-end is bounce with bounce_to "hardener"
    And run-end is stopped, exit_code 1, stopped_at "critic", reason "critic bounced to hardener, which is not a worker"
    And stdout contains "   verdict BOUNCE to hardener" and "pipeline stopped at critic" and does not contain "which is not a worker"

  Scenario: the judge round cap and the bounce cap stop without another rework
    Given `--from critic --to critic`, `runner.MAX_JUDGE_ROUNDS` set to 2 for the test only, and findings "1. finding A" then "1. finding B"
    When each bounce reworks a specifier that writes a handoff
    Then the events are 01-critic bounce, 02-specifier done, 03-critic bounce, 04-specifier done, run-end stopped
    And the reason is "critic ran 2 rounds without a pass"
    And stdout contains "pipeline stopped at critic" and does not contain "ran 2 rounds" or "ran 1000 rounds"
    And the production constant stays 1000
    Given the critic in that window replaced so `bounces` is 1, and the same two different findings
    Then after 03-critic bounce there is no 04-specifier
    And the reason is "critic still bouncing after 1 rounds; stopping for a human"
    And stdout contains that reason

  Scenario: a judge with no verdict writes no extra event for the made-up bounce
    Given `--from critic --to critic` and `--retries 1`
    When the first critic invoke writes nothing, the specifier writes a handoff, and the next critic writes "VERDICT: PASS\n"
    Then 01-critic's step-end is failed, its handoff is null and its head equals start_commit
    And the next event is step-start "02-specifier"
    And stdout contains "critic wrote no verdict; retrying"
    And no event's fields contain "produced no verdict"
    And the specifier prompt does contain "critic produced no verdict after 1 attempts"
    And the run ends complete

  Scenario: declining the critic's approval stops without a new stdout line
    Given `--from critic --to critic`, auto false, stdin reported as a tty, and the answer "n"
    When the critic writes "VERDICT: PASS\n"
    Then the events are step-start "01-critic", step-end "01-critic" pass, run-end stopped
    And run-end has exit_code 1, stopped_at "critic" and reason "not approved"
    And stdout contains "continue to coder? [y/N] " and does not contain "not approved" or "pipeline stopped"
    Given stdin not a tty and auto false
    Then stdout contains "non-interactive: continuing without approval (use --to critic to stop here)" and "pipeline complete"
    And run-end is complete with exit_code 0 and reason null

  Scenario: QA names what it ran against
    Given `--from qa --to qa`, `runner.run_gates` returning [], and a handoff that is the row's line
    And this window has no visual judge, so `state.unseen` stays false and `ran-against: app` is not claimed for every run
    Then run-end is complete with the row's exit code and reason, and stdout contains that reason when it is not null
    And a null reason still prints "pipeline complete" and does not print "NOT verified"

    Examples:
      | line | exit_code | reason |
      | ran-against: app | 0 | null |
      | ran-against: harness | 3 | pipeline complete, NOT verified against the running app (qa ran against a harness) |
      | ran-against: nothing | 3 | pipeline complete, NOT verified against the running app (qa ran against nothing) |

  Scenario: a blind visual fallback is complete but not verified by eye
    Given `[visual]` present and not disabled, and `qa/t.md` contains a visual block with route "/page" and selector "#box"
    And the task has no `where:` or `symptom:`, `qa/t.md` is committed, `--agent kilo`, no `--model`, and `runner.run_gates` returning []
    And `runner.run_backend` returning `(1, "rate limit exceeded")`, so the judge model falls back at once and does not sleep
    When `--from visual --to visual` and the invoke writes "VERDICT: PASS\n"
    Then run-end is complete, exit_code 3, stopped_at null, reason "pipeline complete, NOT verified by eye"
    And stdout contains that reason, "visual: claude-fable-5-1 is out of usage; judging with kilo" and "   verdict PASS (geometry only, pictures not seen)"
    When `--from visual --to qa` and, after that same blind pass, QA writes "ran-against: app\n"
    Then the reason, exit_code and stopped_at are the same, and stdout contains that reason
    When that QA writes "ran-against: harness\n" instead
    Then the reason is "pipeline complete, NOT verified against the running app (qa ran against a harness)", exit_code is 3, and stdout does not contain "NOT verified by eye"

  Scenario: a rate limit and a dandelion miss write wait lines
    Given `--from specifier --to specifier`, `--retries 1`, `runner.LIMIT_WAIT_SECONDS` set to 90, `runner.LIMIT_WAITS` set to 12, and `runner.time.sleep` replaced so it does not block
    When `runner.run_backend` returns `(1, "rate limit exceeded")` once and then `(0, "ok")`
    Then one wait with reason "rate-limit" sits between that attempt's step-start and step-end
    And the wait's until is its at plus 90 seconds, in the same timestamp form, and is not equal to at
    And stdout contains "   rate limited; waiting 1 min before retrying 01-specifier"
    When `runner.run_backend` always returns `(1, "rate limit exceeded")`
    Then that attempt has 12 wait lines and then a step-end stopped with reason "specifier used all 1 attempts"
    And each of those waits has until 90 seconds after its at
    And stdout contains "   01-specifier: still rate limited after 12 waits"
    Given the same 90 seconds, `LIMIT_WAITS` 12 and patched sleep, `--model dandelion/route`, `dandelion.require` a no-op, and `dandelion.choose` returning `(None, "no subscription has quota left")` once and then `Choice("claude-opus-5 high claude", "claude", "claude-opus-5", "high", {})`
    And `runner.run_backend` returning `(0, "ok")`
    Then run-start has agent null, model "dandelion/route" and effort null
    And one wait with reason "dandelion-unrouted" sits between the step-start and the step-end
    And that wait's until is its at plus 90 seconds
    And stdout contains "   dandelion/route: no subscription has quota left; waiting 1 min before retrying 01-specifier"

  Scenario: the real session line still says finished in
    Given the specifier/critic window and `runner.invoke` left in place
    And `runner.run_backend` writing the handoff or verdict the passing run needs and returning `(0, "ok")`
    And `runner.elapsed` returning 0
    Then stdout contains "   01-specifier finished in 0.0 min: ok" and "pipeline complete"
    And stderr is empty

  Scenario: an events directory warns once per run and changes nothing else
    Given two passing runs with `runner.elapsed` returning 0
    When the second run's `events.jsonl` is a directory
    Then the two stdout texts are equal and the two return values are equal
    And the second stderr is exactly one line, `warning: cannot write run events to <path>: <OSError>`, containing "events.jsonl"
    And a third `run_pipeline` on the same directory warns exactly once more

  Scenario: signals and other exceptions write a run-end and then propagate
    Given the specifier invoke raises the row's exception
    Then there is a step-start "01-specifier" with no step-end, and a run-end with the row's result, exit_code, stopped_at "specifier" and reason
    And the same exception still propagates
    And after the call, `signal.getsignal(signal.SIGTERM)` and `signal.getsignal(signal.SIGINT)` are what they were before
    And the test never sends a real signal

    Examples:
      | raised | result | exit_code | reason |
      | KeyboardInterrupt | interrupted | 130 | SIGINT |
      | the installed SIGTERM handler | interrupted | 143 | SIGTERM |
      | RuntimeError("boom") | failed | 1 | RuntimeError: boom |
      | RuntimeError("boom\nnext") | failed | 1 | RuntimeError: boom |
      | SystemExit(4) | failed | 4 | SystemExit: 4 |
      | SystemExit("nope") | failed | 1 | SystemExit: nope |
      | SystemExit() | failed | 0 | one trailing space |

    And the SystemExit() reason is the characters `SystemExit:` plus one space and nothing more
    And the SIGTERM row raises `SystemExit(143)` rather than the handler's own class
    And calling the handler directly raises a BaseException that is not an Exception
    Given the interrupt is raised from the coder invoke during a hardener rework
    Then the hardener step-end bounce is already written, the coder step-start has no step-end, and stopped_at is "hardener"

  Scenario: a bug that cannot be reproduced stops after run-start
    Given a task with "where: /page" and "symptom: the box is square", `[visual]` enabled, and `visual.reproduce` patched to return None
    When the window is `--from specifier --to specifier`
    Then the events are run-start then run-end stopped, exit_code 1, stopped_at "specifier"
    And the reason is "pipeline stopped before specifier: the bug could not be reproduced"
    And stdout contains that line and does not contain "pipeline stopped at" or "pipeline complete"
    And no step event is written and no browser is launched

  Scenario: scope, model, effort and branch are the values the run was given
    Given `--from specifier --to specifier` and a specifier that writes a handoff
    When the row's flags are passed
    Then run-start's scope, agent, model, effort and branch are the row

    Examples:
      | flags | scope | agent | model | effort | branch |
      | none, on branch main | all | claude | m | null | main |
      | `--scope changed` | changed | claude | m | null | main |
      | `--focus src/a.py` with that file committed | changed | claude | m | null | main |
      | `--scope hard --focus src/a.py` | hard | claude | m | null | main |
      | `--scope hyper` | hyper | claude | m | null | main |
      | `--effort low` | all | claude | m | low | main |
      | no `--model` and no `[agent] model` | all | claude | null | null | main |
      | no `--model`, `[agent] model` = "from-toml" | all | claude | from-toml | null | main |
      | no `--agent`, `[agent] backend` = "grok" | all | grok | m | null | main |
      | `[agent] effort` = "high" and no `--effort` | all | claude | m | high | main |
      | detached HEAD | all | claude | m | null | null |

    And a toml with no `[git]` table records base "origin/master"
    And a task file outside the repo records an absolute task_path and does not raise
    And `--scope hyper` with no `--from` or `--to`, gates, audit and hunks patched to [], records from "specifier" and to "qa"
    And the step roles are specifier, critic, coder, architect, blast, hardener, qa, in that order, and cleaner, design, practices and perf do not appear
    And without a `[visual]` section, `--from visual` exits 1 with stderr `unknown role visual; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa\n` and writes no events file
    And with a `[visual]` section and no `qa/<task>.md`, `--from visual --to visual` prints `visual: no qa/<task>.md; skipping`, writes no step, and run-start has from "visual" and to "visual"

  Scenario: failures before the window write no events file
    Given a child `marestail run` with `PYTHONPATH` set to this checkout
    When the row's command is run
    Then the exit code is 1, stdout is empty, stderr is exactly the row's text plus a newline, and `events.jsonl` does not exist

    Examples:
      | command | stderr |
      | `--model dandelion/route --agent grok` | --model dandelion/route picks the backend and effort before every session; drop --agent and --effort |
      | `MARESTAIL_NICE=high` | nice must be an integer 0-19, got 'high' |
      | `MARESTAIL_NICE=high` plus `--model dandelion/route --agent grok` | nice must be an integer 0-19, got 'high' |
      | `--scope hard` and no focus | --scope hard needs at least one focus path: pass --focus or set [focus] paths in marestail.toml |
      | `--scope all --focus src/a.py` | --focus cannot be combined with --scope all |
      | `--scope hyper --focus src/a.py` | --focus cannot be combined with --scope hyper; hyper gates the diff and nothing else |
      | `--focus src/missing` | focus path not found under <repo>: src/missing |
      | a task whose block has two dependencies and `stack = true` | <task>: stack = true needs exactly one dependency, got 2 |

    And `MARESTAIL_NICE=high` is the real `nice.apply`, not a stub
    And the focus-not-found stderr contains the repo path and "src/missing" and is that one line
    And a missing task file still raises FileNotFoundError naming that file, and writes no events file
    And `--scope hyper --from cleaner` exits 1 with stderr "unknown role cleaner; choose from specifier, critic, coder, architect, blast, hardener, qa\n"
    And that run has `runs/<task>/start-commit` and no `events.jsonl`
    And `--from nope` exits 1 with stderr "unknown role nope; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa\n"

  Scenario: the module writes the bytes it is given
    When `Log.append("wait", {"reason": "rate-limit", "until": "2026-09-28T14:03:07.412Z"}, "2026-09-28T14:03:07.412Z", 4)` runs
    Then the file is exactly `{"v": 1, "at": "2026-09-28T14:03:07.412Z", "event": "wait", "pid": 4, "reason": "rate-limit", "until": "2026-09-28T14:03:07.412Z"}\n`
    And a second append adds a second line
    And a field whose value is None is dumped as null and is not dropped
    And patching `marestail.events.os` shows one `open` with `O_WRONLY | O_APPEND | O_CREAT` and mode `0o644`, one `write` of those utf-8 bytes, one `fsync` and one `close`
    And the source of `marestail/events.py` does not call `datetime`, `time` or `utc_now`, and its only marestail import is `marestail.shell`

  Scenario: timeline, freeze, imports and the README stay in step
    When `timeline.record` is called twice the way `test_two_attempts_append_agree` does
    Then it returns each step, the returned `ended_at` is the one in `timeline.json`, and a worker step still has no `verdict` key
    And `freeze.frozen_paths` for the coder, given the existing timeline paths plus ".marestail/runs/t/events.jsonl" and "src/a.py", returns the two timeline paths and the events path
    And `test_coder_cannot_keep_timeline_artefacts` still expects only the two timeline paths
    And `.importlinter`'s layer that contains `marestail.report : marestail.config : marestail.changes : marestail._location` also contains `marestail.events` and still contains `marestail.task_file`
    And `foundations-import-nothing-above` lists `marestail.events`
    And a contract `events-stays-below-runner` forbids `marestail.events` from the same modules `timeline-stays-below-runner` forbids
    And the README paragraph that ends with the `nohup setsid tools/overnight.sh` sentence is unchanged
    And the next paragraph says `.marestail/runs/<task>/events.jsonl` gets one JSON line per run start, attempt start, attempt end, wait, proposal and run end; it is appended across resumes; the latest run starts at the last `run-start`; and a run with no `run-end` whose pid is gone was killed
    And a table there lists the six events and the fields in the table at the top of this feature
    And the Kilo paragraph still follows, and the environment section gains no `MARESTAIL_` name
    And `marestail/events.py` does not read the environment

  Scenario: existing commands keep their current text
    When `marestail --help` runs
    Then it contains `{gate,run,tasks,install,sonar,watch,perf,visual,route,graph,depth}` in that order and exits 0
    And `run --help` lists `--from`, `--to`, `--auto`, `--scope`, `--focus`, `--model`, `--retries`, `--effort`, `--agent` in that order
    And `--retries` still says "attempts per role; 0 means unlimited (default)"
    And `--auto` still says "skip the approval pause after the critic"
    And `tasks check` on a file whose block is `depends = []` exits 0 with empty stdout
    And `route --help` with dandelion absent exits 127 and mentions `dandelion is not installed`
    And `tests/test_runner_flow.py::test_run_pipeline_uses_config_defaults` still returns 7 and prints "\nperf changes\n"
    And `test_run_pipeline_routes_and_scopes` still prints "\n"
    And both write a run-start without raising, with branch null, and do not write a run-end
    And the order stays nice.apply, then task_file.read, then pick_model

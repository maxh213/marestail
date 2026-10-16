Feature: marestail status says where every run stands, and marestail abandon gives one up

  `marestail status [--json] [--all] [--queued] [PATH ...]` prints one table of
  every marestail run found under the PATHs, and `marestail abandon TARGET
  [TARGET ...] [--reason TEXT]` gives up on a run without deleting anything.
  The behaviour is the marestail-runs script's, with `events.jsonl` (020) read
  first and the script's log and handoff reading kept as the fallback for runs
  that predate events.

  A folder is a repo when it holds `.marestail/`. A PATH that is a repo is the
  only repo found under it; the scan never looks inside a repo for more. Any
  other PATH is walked child by child, sorted by name, at most two levels
  below the PATH, skipping symlinked folders, dot folders and the names
  `.git`, `.marestail`, `node_modules`, `deps`, `_build`, `target`, `vendor`,
  `.next`, `dist`, `build`, `mutants` and `tmp`. Each folder is visited once
  by its resolved path, so two overlapping PATHs list a repo once, and an
  unreadable folder is skipped quietly. With no PATH the scan root is the
  current directory, unless the current directory or one of its parents holds
  `.marestail/`, in which case the nearest such folder is the one PATH.
  `status` needs no `marestail.toml` and no git repository, exits 0 once it
  has scanned, and a PATH that is not a directory exits 2 with
  `marestail status: not a directory: <PATH>` on stderr before anything is
  printed.

  In one repo the tasks are the names of the directories in
  `.marestail/handoffs/` and `.marestail/runs/`, except names starting with
  `handoffs-`. Loose files there are not tasks. The task id is the task file's
  stem. There are seven states, spelled alike in the table and the JSON:
  `running`, `dead`, `stopped`, `never-started`, `complete`, `abandoned`,
  `leftover`.

  When `.marestail/runs/<task>/events.jsonl` exists and holds at least one
  valid `run-start`, the state comes from it alone. A valid line is a JSON
  object with `"v": 1` and a string `event`; a torn line, a blank line, a
  later version and an unknown event name are all skipped. The latest run is
  the last `run-start` and every valid line after it; earlier runs are
  ignored. A file with no valid `run-start` counts as no file and the task
  falls through to the legacy rules.

  | latest run | state |
  | run-end with result complete | complete |
  | run-end with result stopped | stopped |
  | run-end with result failed | stopped |
  | run-end with result interrupted | dead |
  | no run-end, and the run-start pid is this run | running |
  | no run-end, and it is not | dead |

  The pid is "this run" only when `/proc/<pid>/cmdline` exists and, split on
  NUL bytes, some token's basename is `cli.py` or `marestail` and the next
  token is `run`, some token after that `run` has `Path(token).stem` equal to
  the task id, and `/proc/<pid>/cwd` resolves to the repo or a folder inside
  it. Every `/proc` read that raises `OSError` counts as not this run. An
  `abandoned` marker turns `dead` or `stopped` into `abandoned` when the
  marker's mtime is at or after the latest `run-start`'s `at`; an older marker
  belongs to an earlier run and is ignored, and a `running` or `complete` run
  is never shown as abandoned.

  Every other task is read as the script reads it: the `pipeline.log` regexes
  `== role (NN-role) attempt N`, `finished in`, `verdict`, `rate limited;
  waiting ... retrying`, `pipeline complete`, `pipeline stopped at` and
  `stopping for a human`; otherwise the per-step files `NN-role.prompt.md` and
  `NN-role.json`, the live handoffs and the archived `handoffs-*/` folders.
  `leftover` means every live handoff is tracked by git or byte-identical to
  an archived file of the same name. `abandoned` means the
  `runs/<task>/abandoned` marker or a `handoffs-abandoned-*` or
  `handoffs-aborted-*` archive, with no fresh live handoffs and a state other
  than `complete`. A lone `handoffs-aborted-*` archive, with no log and no
  live handoffs, is `complete` (`archived`): the archive is already complete,
  so the abandoned override does not apply. When a marestail run process is
  alive in the repo, the newest legacy record that is already `dead` or
  `never-started` becomes `running`. A newer record in any other state does
  not block that, and an events-era record is never rewritten, even when it
  is newer. The processes counted pass the cmdline and cwd checks above, not
  the task-id check, and their pid is not the pid of an events-era `running`
  run in the same repo. The text becomes `in <step>` when that record has a
  current step, and `starting` when it has none. Its JSON `pid` is the lowest
  such pid.

  For a `dead` run, `last -x -F shutdown` runs once per invocation, and only
  when at least one run is dead. A shutdown at or up to 3600 seconds after the
  run's last activity adds `; machine shut down HH:MM` (local time) to the
  table text and makes the JSON `reason` `machine shut down <UTC ISO-8601 with
  Z>`. A missing `last` or a failed parse means no shutdowns.

  The table is the script's output, line for line: a header
  `marestail runs: N known, N running, N shown` plus `, N
  complete/abandoned/leftover hidden (use --all)` when any are hidden, a blank
  line, then the table or `(nothing to show)`. Columns are `Repo`, `Task`,
  `State`, `Where it stands`, `Last activity`, `Branch`. Rows sort by state in
  the order above, then newest activity first. `Repo` is the repo's path
  relative to the PATH it was found under, or the repo folder's own name when
  it is the PATH. `Last activity` is `%m-%d %H:%M` local plus `Nm ago` under
  one hour, `Nh ago` under two days, else `Nd ago`, or `?` when unknown.
  `Branch` is the record's branch, or `(detached)` when null. The state
  `never-started` keeps that spelling in the table.

  `Where it stands` for legacy runs is the script's `detail`, unchanged. For
  events-era runs it is: `running`: `in <step>`, `starting` before the first
  step-start, or `waiting for a rate limit until <until>` when the latest
  event is a `wait`; `dead` mid-step: `mid <step>`, where the latest
  step-start has no step-end; `dead` between steps: `after <step>
  (<outcome>)` from the latest step-end; `dead` with no step: `before the
  first step`; `dead` from a run-end `interrupted`: `interrupted` plus ` in
  <step>` when the run has a step id (that id, such as `03-coder`, not
  `stopped_at`, which is the role), otherwise `interrupted`; `stopped`: `at
  <stopped_at>` plus `: <reason>` when there is one, or `failed at
  <stopped_at>` when the result is `failed`; `complete`: `<N> steps`, the
  count of step-end lines in the run; `abandoned`: the marker's first line.

  `--all` also shows `complete`, `abandoned` and `leftover`. `--queued` adds,
  after a blank line, `Tasks in tasks/ that never ran:`, a blank line and a
  `Repo | Task` table. A queued task is a `tasks/*.md` file whose stem has no
  task folder in any worktree of the same repo; `README.md` in any case and
  `NN-<role>.md` names are not tasks. Worktrees are the same repo when
  `git config --get remote.origin.url`, else `git rev-parse --git-common-dir`,
  else the path, matches, and the first worktree scanned that holds the file
  is the one reported.

  `--json` prints `{"v": 1, "runs": [...], "queued": [...]}` with
  `json.dumps(..., indent=2)` and nothing else. `runs` is sorted by `repo`
  then `task` and always carries every run, whatever `--all` says. `queued` is
  `[]` without `--queued`; a queued entry is `{"repo": <absolute>, "task":
  <id>, "path": <absolute .md path>}`. Each run record has exactly these keys,
  in this order: `repo` (the resolved absolute path), `task`, `state`, `step`,
  `role`, `since`, `branch`, `head`, `pid`, `result`, `exit_code`,
  `stopped_at`, `reason`, `events`, `log`.

  | key | events era | legacy |
  | step | `step` of the latest step-start or step-end, else null | the script's last_step (its current_step when running), null when empty |
  | role | `role` of that same event, else null | the text after the first `-` in step, null when step is null |
  | since | `at` of the latest kept line, copied as written | the last activity as UTC `YYYY-MM-DDTHH:MM:SSZ`, null when none |
  | branch | `branch` of run-start | `git branch --show-current`, null when empty or git fails |
  | head | `head` of run-end, else of the latest step-end, else `start_commit` | null |
  | pid | the latest run-start pid, whatever the state | when running, the lowest unattributed pipeline pid in the repo; else null |
  | result | `result` of run-end, else null | `complete` or `stopped` when pipeline.log ends that way, else null |
  | exit_code | `exit_code` of run-end, else null | null |
  | stopped_at | `stopped_at` of run-end, else null | null |
  | reason | for abandoned, the marker's reason or null; for dead, the shutdown text or null, never the run-end reason; otherwise the run-end's reason or null | stopped: `worker repeated the same problems 3x` or `runner asked for a human`; dead: the shutdown text or null; abandoned: the marker's reason or null; else null |
  | events | absolute path of events.jsonl | null |
  | log | absolute path of `runs/<task>/pipeline.log` when it exists, else null | same |

  For `abandoned`, `since` is the marker's mtime as UTC
  `YYYY-MM-DDTHH:MM:SSZ`, in both eras.

  `marestail abandon` takes `REPO/TASK`, split on the last `/`. `REPO` is a
  path, absolute or relative to the current directory, that must hold
  `.marestail/`; a bare `TASK` with no `/` means the repo found as with
  `status` and no PATH. When no such repo exists the error is `<TARGET>: no
  .marestail folder at <path>`, where `<path>` is the repo part as given, or
  the current directory for a bare task. It refuses, with `<TARGET>: a
  marestail pipeline is running in <repo>, not touching it`, when the task
  reads as `running` or when a run process in the repo is not attributed to an
  events-era run. When neither `runs/<task>/` nor `handoffs/<task>/` exists:
  `<TARGET>: no run or handoffs folder for that task`. Otherwise it creates
  `runs/<task>/` if needed, renames `handoffs/<task>/` to
  `runs/<task>/handoffs-abandoned-<stamp>/` when that folder holds any `.md`
  file (stamp is `time.strftime("%Y%m%dT%H%M%S")`), writes
  `runs/<task>/abandoned` and prints `<TARGET>: abandoned` plus `, N handoffs
  moved to <path relative to the repo>` when it moved any. The marker is
  exactly two lines: `abandoned YYYY-MM-DD HH:MM` plus `: <reason>` when a
  reason is given, then `Move the handoffs-abandoned-* folder back to
  .marestail/handoffs/<task>/ and delete this file to revive it.` Errors go to
  stderr, each target is handled on its own, and the exit code is 1 when any
  target failed, else 0. It deletes nothing, runs no git command that writes,
  and appends nothing to `events.jsonl`.

  All of it lives in `marestail/status.py`, which imports nothing from
  marestail but `marestail.shell` and runs `git` and `last` through
  `marestail.shell.run`. It reads `/proc` through one function that lists the
  machine's processes as `(pid, tokens, cwd)`, called once per invocation, and
  through no other path; it does not use `ps`. The events file name is defined
  in `status.py` (020 did not export it). The public surface is
  `read_run(repo, task)` returning the one record the table and JSON are built
  from, `scan(paths)` returning the records and the queued tasks, and
  `abandon(targets, reason) -> int`. `.importlinter` gains `marestail.status`
  on the layer that holds `marestail.timeline : marestail.ran_against` and in
  the `source_modules` of `foundations-import-nothing-above`.

  In the exact-text scenarios the time zone is UTC and "now" is 2026-10-16
  12:00:00 UTC. The shared fixture is a folder `fleet/` holding two repos.
  `alpha/` (branch `main`) holds: `011-live`, an events-era run at pid 4242
  with step-starts `01-specifier` (step-end `done`, head `bbb222`) and
  `03-coder` at `2026-10-16T11:58:00.000Z`, and a live process whose cmdline
  is `python3 -u /opt/marestail/marestail/cli.py run tasks/011-live.md --auto`
  with cwd `alpha`; `010-killed`, an events-era run at pid 999999 with
  `01-specifier` done and `02-critic` started at `2026-10-16T11:10:00.000Z`,
  and no such process; `009-paused`, an events-era run at pid 5555 whose
  run-end is `stopped`, exit 1, stopped_at `critic`, reason `not approved` at
  `2026-10-16T10:20:00.000Z`; `008-fresh`, an empty run folder with mtime
  2026-10-16 10:50; `007-done`, an events-era complete run of two steps whose
  run-end head is `ddd444` at `2026-10-16T10:00:00.000Z`; `006-given-up`, an
  events-era stopped run whose run-start is at `2026-10-16T08:00:00.000Z`,
  plus an `abandoned` marker with mtime 2026-10-16 09:00 whose first line is
  `abandoned 2026-10-16 09:00: superseded by 007-done`; and
  `.marestail/handoffs/005-restored/01-specifier.md`, tracked by git, with no
  run folder. `beta/` (branch `main`) holds `004-old`, a legacy run whose
  `pipeline.log` (mtime 2026-10-15 08:00) ends mid step:
  `== specifier (01-specifier) attempt 1`, `   01-specifier finished in 2.0
  min: ok`, `== coder (02-coder) attempt 1`.

  Scenario: the default table lists unfinished runs, ordered by state, then newest first
    Given the fleet fixture
    When I run `marestail status fleet`
    Then the exit code is 0
    And stdout is exactly:
      """
      marestail runs: 8 known, 1 running, 5 shown, 3 complete/abandoned/leftover hidden (use --all)

      | Repo  | Task       | State         | Where it stands               | Last activity         | Branch |
      |-------|------------|---------------|-------------------------------|-----------------------|--------|
      | alpha | 011-live   | running       | in 03-coder                   | 10-16 11:58 (2m ago)  | main   |
      | alpha | 010-killed | dead          | mid 02-critic                 | 10-16 11:10 (50m ago) | main   |
      | beta  | 004-old    | dead          | mid 02-coder                  | 10-15 08:00 (28h ago) | main   |
      | alpha | 009-paused | stopped       | at critic: not approved       | 10-16 10:20 (1h ago)  | main   |
      | alpha | 008-fresh  | never-started | no handoffs, no steps, no log | 10-16 10:50 (1h ago)  | main   |
      """
    And stderr is empty

  Scenario: --all adds complete, abandoned and leftover rows after the rest
    Given the fleet fixture
    When I run `marestail status --all fleet`
    Then the exit code is 0, and stdout is exactly:
      """
      marestail runs: 8 known, 1 running, 8 shown

      | Repo  | Task         | State         | Where it stands                                      | Last activity         | Branch |
      |-------|--------------|---------------|------------------------------------------------------|-----------------------|--------|
      | alpha | 011-live     | running       | in 03-coder                                          | 10-16 11:58 (2m ago)  | main   |
      | alpha | 010-killed   | dead          | mid 02-critic                                        | 10-16 11:10 (50m ago) | main   |
      | beta  | 004-old      | dead          | mid 02-coder                                         | 10-15 08:00 (28h ago) | main   |
      | alpha | 009-paused   | stopped       | at critic: not approved                              | 10-16 10:20 (1h ago)  | main   |
      | alpha | 008-fresh    | never-started | no handoffs, no steps, no log                        | 10-16 10:50 (1h ago)  | main   |
      | alpha | 007-done     | complete      | 2 steps                                              | 10-16 10:00 (2h ago)  | main   |
      | alpha | 006-given-up | abandoned     | abandoned 2026-10-16 09:00: superseded by 007-done   | 10-16 09:00 (3h ago)  | main   |
      | alpha | 005-restored | leftover      | handoff copies from git or a checkout, no run folder | ?                     | main   |
      """
    When I run `marestail status` in an empty folder
    Then the exit code is 0 and stdout is exactly:
      """
      marestail runs: 0 known, 0 running, 0 shown

      (nothing to show)
      """

  Scenario: events decide the state, and the pid decides running against dead
    Given the fleet fixture
    Then `read_run` on alpha's rows returns the row's state and text:

      | run | state | text |
      | 011-live | running | in 03-coder |
      | 010-killed | dead | mid 02-critic |
      | 009-paused | stopped | at critic: not approved |
      | 007-done | complete | 2 steps |
      | 006-given-up | abandoned | abandoned 2026-10-16 09:00: superseded by 007-done |

    And a run whose only step-start has no step-end and whose pid is alive is `running` with `in <step>`
    And a running run with no step-start yet says `starting`
    And a running run whose latest event is a `wait` with until `2026-10-16T12:30:00.000Z` says `waiting for a rate limit until 2026-10-16T12:30:00.000Z`
    And a run-end with result `failed` reads `stopped` with `failed at <stopped_at>` plus `: <reason>` when there is one, and the JSON `result` stays `failed`
    And a run whose events are step-start `03-coder` role `coder`, then run-end result `interrupted`, stopped_at `coder`, reason `SIGINT`, reads `dead` with text `interrupted in 03-coder`, step `03-coder`, role `coder`, stopped_at `coder`, result `interrupted`, and reason null
    And a run with no step-start and run-end result `interrupted`, stopped_at null, reason `SIGINT`, reads `dead` with text `interrupted`, step null, stopped_at null, result `interrupted`, and reason null
    And a dead run whose latest step-end is `02-critic` with outcome `pass` says `after 02-critic (pass)`
    And a dead run with no step at all says `before the first step`
    And the run-start pid is "this run" only with all of: `/proc/<pid>/cmdline` present, a token whose basename is `cli.py` or `marestail` followed by `run`, a later token whose stem is the task id, and `/proc/<pid>/cwd` inside the repo
    And the same file reads `dead` when the pid is absent from `/proc`, when the cmdline is `sleep 100`, when the cmdline is a `run` of another task, when the cwd is another repo, and when the `/proc` read raises `OSError`

  Scenario: the latest run is the last run-start, and invalid lines are skipped
    Given an events file holding a complete first run and then a run-start with one step-start and no run-end, and no live process
    Then the state is `dead`, and the first run's events change nothing
    Given the reverse order, a dead first run and a complete second run
    Then the state is `complete`
    Given a file whose lines are a torn JSON fragment, a blank line, a `"v": 2` line, an unknown event name, and a valid run
    Then the state comes from the valid lines alone, and `since` is the `at` of the latest kept line
    Given a file with no valid run-start at all
    Then the task falls through to the legacy rules and the JSON `events` is null

  Scenario: the abandoned marker wins over dead and stopped only when it is newer than the latest run-start
    Given an events-era dead run whose run-start is at `2026-10-16T08:00:00.000Z`
    When the marker's mtime is 2026-10-16 09:00
    Then the state is `abandoned`, `since` is `2026-10-16T09:00:00Z`, `reason` is the marker's reason, and the text is the marker's first line
    When the marker's mtime is 2026-10-16 07:00
    Then the state is `dead` and the marker is ignored
    Given an events-era complete run beside such a newer marker
    Then the state stays `complete`

  Scenario Outline: a run with no events file is read from pipeline.log and the handoff folders
    Given a repo whose task `<task>` has the log or files <fixture>
    When I run `marestail status`
    Then the row is `<state>` with the text `<text>`

    Examples:
      | task | fixture | state | text |
      | t1 | a log with 4 `==` steps then `pipeline complete`, no archive | complete | 4 steps |
      | t2 | the same plus one `handoffs-*` archive | complete | 4 steps, archived |
      | t3 | a log ending `pipeline stopped at 03-coder` and a line `stopping for a human` | stopped | at 03-coder, worker repeated the same problems 3x |
      | t4 | a log ending `pipeline stopped at 03-coder` without that line | stopped | at 03-coder, runner asked for a human |
      | t5 | a log whose last step is not done and holds `rate limited; waiting 1 min before retrying 03-coder` | dead | in the rate-limit wait before 03-coder |
      | t6 | a log whose last step `03-coder` is not done | dead | mid 03-coder |
      | t7 | the same plus a live handoff `03-coder.md` | dead | mid 03-coder (its handoff was written, the runner never logged it) |
      | t8 | a log whose last step `03-perf` is done and whose last line is `filling perf samples` | dead | after 03-perf, in perf sampling |
      | t9 | a log whose last step `03-coder` is done with a `verdict PASS` line | dead | after 03-coder finished (verdict PASS) |
      | t10 | the same without a verdict line | dead | after 03-coder finished |
      | t11 | one `handoffs-*` archive and no live handoffs, no log | complete | archived |
      | t12 | the same plus a byte-identical live copy of the archived handoff | complete | archived, leftover copies in handoffs/ |
      | t13 | a run folder and nothing else | never-started | no handoffs, no steps, no log |
      | t14 | only a live handoff `02-critic.md`, no run folder, untracked | dead | after 02-critic wrote its handoff (no pipeline.log) |
      | t15 | `03-coder.prompt.md` and no json | dead | mid 03-coder: prompt written, no result (no pipeline.log) |
      | t16 | `03-coder.prompt.md` and a 0-byte `03-coder.json` | dead | after 03-coder returned nothing, 0-byte result (no pipeline.log) |
      | t17 | `03-coder.prompt.md`, a non-empty json, and the live handoff `03-coder.md` | dead | after 03-coder wrote its handoff, then nothing (no pipeline.log) |
      | t18 | `03-coder.prompt.md` and a non-empty json, no handoff | dead | after 03-coder finished, then nothing (no pipeline.log) |
      | t19 | every live handoff tracked by git, no run folder | leftover | handoff copies from git or a checkout, no run folder |
      | t20 | an `abandoned` marker beside a dead legacy run | abandoned | the marker's first line |
      | t21 | a log mid step and a `handoffs-aborted-*` archive, no fresh handoffs | abandoned | aborted archive, nothing since |
      | t22 | one `handoffs-aborted-*` archive and no live handoffs, no log | complete | archived |

  Scenario: an unattributed pipeline promotes only the newest legacy dead or never-started row
    Given the fleet fixture
    And a live process in alpha, pid 77, cmdline `python3 -u /opt/marestail/marestail/cli.py run tasks/other.md`, cwd alpha
    Then `008-fresh` is `running` with text `starting` and JSON `pid` 77
    And `010-killed` stays `dead` with text `mid 02-critic`
    And `011-live` stays `running` with text `in 03-coder` and JSON `pid` 4242

  Scenario: a newer legacy stopped row does not block promotion of an older mid-step
    Given a repo `gamma` on branch `main` with no events files
    And task `020-halted` has a pipeline.log ending `pipeline stopped at 04-coder` with no `stopping for a human` line, mtime 2026-10-16 11:00 UTC
    And task `019-cut` has a pipeline.log `== coder (03-coder) attempt 1` and no finished line, mtime 2026-10-16 10:00 UTC
    And the live processes in `gamma` are pid 80 and pid 50, both cmdline `python3 -u /opt/marestail/marestail/cli.py run tasks/other.md`, cwd `gamma`, neither an events-era run's pid
    When I run `marestail status gamma`
    Then `019-cut` is `running` with text `in 03-coder` and JSON `pid` 50
    And `020-halted` stays `stopped` with text `at 04-coder, runner asked for a human`

  Scenario: an events-era running pid does not promote a legacy row
    Given the fleet fixture, whose only live process is the 011-live shim at pid 4242
    Then `008-fresh` stays `never-started` with text `no handoffs, no steps, no log`
    And `010-killed` stays `dead` with text `mid 02-critic`

  Scenario: a shutdown within the hour explains a dead run
    Given a dead run whose last activity is 2026-10-16 11:00 UTC
    When `last -x -F shutdown` prints a line with `Thu Oct 16 11:10:00 2026`
    Then the text gains `; machine shut down 11:10` and the JSON `reason` is `machine shut down 2026-10-16T11:10:00Z`
    When the only shutdown is at `Thu Oct 16 13:01:00 2026`
    Then neither the note nor the reason appears
    When `last` is missing or its output fails to parse
    Then the run is still `dead`, with no note, and the exit code is 0
    And `last` runs at most once per invocation, and not at all when nothing is dead

  Scenario: the scan finds repos two levels down, once each, and skips noise
    Given `fleet/alpha` is a repo, `fleet/team/gamma` is a repo, `fleet/team/deep/delta` is a repo, and `fleet/team/deep` is not
    And folders named `fleet/node_modules/x`, `fleet/.hidden/x` and `fleet/tmp/x` each hold a `.marestail`
    And `fleet/link` is a symlink to `fleet/alpha`
    When I run `marestail status fleet`
    Then the repos found are `alpha` and `team/gamma`, and nothing under `node_modules`, `.hidden`, `tmp`, `link` or `team/deep`
    When I run `marestail status fleet fleet/team`
    Then `gamma` is listed once
    When I run `marestail status fleet/alpha`
    Then the Repo column is `alpha`
    When I run `marestail status` from a folder inside `fleet/alpha`, with no PATH
    Then the one PATH is `fleet/alpha` and the Repo column is `alpha`
    When I run `marestail status fleet/no-such`
    Then the exit code is 2, stdout is empty, and stderr is `marestail status: not a directory: fleet/no-such`

  Scenario: --queued lists task files that never ran, once per clone
    Given two worktrees `w1` and `w2` of one clone, `w1` scanned first
    And `w1/tasks/012-next.md` and `w2/tasks/012-next.md` both exist and neither worktree has a task folder for `012-next`
    And `w1/tasks/013-done.md` exists and `w2` has a run folder for `013-done`
    And `w1/tasks/README.md` and `w1/tasks/03-coder.md` exist
    When I run `marestail status --queued fleet`
    Then the runs table is followed by a blank line, `Tasks in tasks/ that never ran:`, a blank line, and:
      """
      | Repo | Task     |
      |------|----------|
      | w1   | 012-next |
      """
    And `013-done`, `README.md` and `03-coder.md` do not appear
    And the JSON queued entry is `{"repo": <w1 absolute>, "task": "012-next", "path": <w1/tasks/012-next.md absolute>}`

  Scenario: --json prints every run with a fixed key order
    Given the fleet fixture
    When I run `marestail status --json fleet`
    Then the exit code is 0, and stdout parses as `{"v": 1, "runs": [...], "queued": []}` and nothing else is printed
    And `runs` has all 8 records although `--all` was not given, sorted by repo then task
    And every record has exactly the keys `repo`, `task`, `state`, `step`, `role`, `since`, `branch`, `head`, `pid`, `result`, `exit_code`, `stopped_at`, `reason`, `events`, `log`, in that order
    And the `011-live` record is:
      """
      {
        "repo": "<alpha absolute>",
        "task": "011-live",
        "state": "running",
        "step": "03-coder",
        "role": "coder",
        "since": "2026-10-16T11:58:00.000Z",
        "branch": "main",
        "head": "bbb222",
        "pid": 4242,
        "result": null,
        "exit_code": null,
        "stopped_at": null,
        "reason": null,
        "events": "<alpha absolute>/.marestail/runs/011-live/events.jsonl",
        "log": null
      }
      """
    And `009-paused` has `result` `stopped`, `exit_code` 1, `stopped_at` `critic`, `reason` `not approved` and `pid` 5555
    And `007-done` has `head` `ddd444`, `result` `complete`, `exit_code` 0 and its run-start pid
    And `004-old` has `step` `02-coder`, `role` `coder`, `since` `2026-10-15T08:00:00Z`, `head` null, `pid` null, `result` null, `events` null and `log` its pipeline.log's absolute path
    And `008-fresh` has `step` null, `role` null and `since` `2026-10-16T10:50:00Z`
    And `005-restored` has `since` null and `state` `leftover`
    And `006-given-up` has `since` `2026-10-16T09:00:00Z` and `reason` `superseded by 007-done`
    When I run `marestail status --json --queued fleet`
    Then `queued` is `[]`, because the fleet fixture has no `tasks/*.md`

  Scenario: abandon moves the handoffs aside and writes the marker
    Given a repo `alpha` whose task `009-paused` reads `stopped` and whose `handoffs/009-paused/` holds `01-specifier.md` and `02-critic.md`
    When I run `marestail abandon alpha/009-paused --reason superseded` at 2026-10-16 12:00:00
    Then the exit code is 0
    And stdout is `alpha/009-paused: abandoned, 2 handoffs moved to .marestail/runs/009-paused/handoffs-abandoned-20261016T120000`
    And `handoffs/009-paused/` is gone and `runs/009-paused/handoffs-abandoned-20261016T120000/` holds the two files
    And `runs/009-paused/abandoned` is exactly:
      """
      abandoned 2026-10-16 12:00: superseded
      Move the handoffs-abandoned-* folder back to .marestail/handoffs/<task>/ and delete this file to revive it.
      """
    And a later `marestail status` shows the task `abandoned` with the marker's first line as its text
    And `events.jsonl` is unchanged, nothing is deleted, and no git command that writes ran
    When I run `marestail abandon alpha/009-paused` with no reason and no `.md` in the handoffs folder
    Then the marker's first line is `abandoned 2026-10-16 12:00` and stdout is `alpha/009-paused: abandoned`
    When I run `marestail abandon 008-fresh` from inside `alpha`
    Then the repo is found as with `status` and no PATH, the marker is written, and stdout is `008-fresh: abandoned`

  Scenario: abandon refuses a running events-era task
    Given the fleet fixture, where `011-live` reads `running`
    When I run `marestail abandon alpha/011-live`
    Then the exit code is 1, stdout is empty, and no marker is written
    And stderr is `alpha/011-live: a marestail pipeline is running in <alpha absolute>, not touching it\n`

  Scenario: abandon refuses while an unattributed pipeline is alive
    Given the fleet fixture
    And a live process in alpha, pid 77, cmdline `python3 -u /opt/marestail/marestail/cli.py run tasks/other.md`, cwd alpha
    When I run `marestail abandon alpha/010-killed`
    Then the exit code is 1, stdout is empty, and nothing is written
    And stderr is `alpha/010-killed: a marestail pipeline is running in <alpha absolute>, not touching it\n`

  Scenario: abandon reports a missing task, a missing repo, and a bare task outside any repo
    Given the fleet fixture
    When I run `marestail abandon alpha/099-nope`
    Then the exit code is 1, stdout is empty, and stderr is `alpha/099-nope: no run or handoffs folder for that task\n`
    When I run `marestail abandon beta-nope/010-killed` where `beta-nope` holds no `.marestail`
    Then the exit code is 1, stdout is empty, and stderr is `beta-nope/010-killed: no .marestail folder at beta-nope\n`
    When I run `marestail abandon 099-nope` from `/tmp/qa-021/bare`, a directory that holds no `.marestail` and whose parents hold none
    Then the exit code is 1, stdout is empty, and stderr is `099-nope: no .marestail folder at /tmp/qa-021/bare\n`

  Scenario: one failed target does not stop the others, and an attributed shim does not block them
    Given the fleet fixture
    And the only live process is the 011-live shim, pid 4242, cmdline `python3 -u /opt/marestail/marestail/cli.py run tasks/011-live.md --auto`, cwd alpha
    When I run `marestail abandon alpha/099-nope alpha/009-paused`
    Then the exit code is 1
    And stderr is `alpha/099-nope: no run or handoffs folder for that task\n`
    And stdout is `alpha/009-paused: abandoned\n`
    And `alpha/.marestail/runs/009-paused/abandoned` is written
    And the attributed 011-live shim does not block `009-paused`

  Scenario: status and abandon join the CLI straight after watch
    When I run `marestail --help`
    Then the command list is `{gate,run,tasks,install,sonar,watch,status,abandon,dash,perf,visual,route,remote,graph,depth}`
    And `status` and `abandon` parse `[--json] [--all] [--queued] [PATH ...]` and `TARGET [TARGET ...] [--reason TEXT]`, each handler importing `marestail.status` inside the function as `run_command` imports the runner
    And `status --help` and `abandon --help` exit 0
    And `marestail queue` is still an invalid choice
    And `tests/test_cli.py` `test_help_lists_commands_in_order`, `tests/test_green_repo.py` `test_help_lists_every_subcommand` and `tests/test_events.py` `test_existing_commands_keep_text` are the assertions that gain `status,abandon` after `watch`
    And `test_help_lists_remote_and_status_and_queue_stay_absent` drops `status` from the absent list and keeps only `queue` absent

  Scenario: the module stays at the foundations layer and nothing else moves
    Then `marestail/status.py` imports only the standard library and `marestail.shell`, and its source never calls `ps`
    And the one `/proc` reader is the single function that lists `(pid, tokens, cwd)` for the machine, called once per invocation
    And `.importlinter` lists `marestail.status` on the `marestail.timeline : marestail.ran_against` layer and in `foundations-import-nothing-above`
    And the README gains a `## Status` section after `## Watch` with the two command lines, the seven states with a one-line meaning each, the scan rule, the `"v": 1` JSON shape with its keys, and what `abandon` moves and writes and how to undo it
    And no new environment variable appears
    And no file under `marestail/tui/` or `.marestail/phases/` changes, and `marestail run` and `marestail watch` behave as before
    And every existing test behaves as before except `test_help_lists_commands_in_order`, `test_help_lists_every_subcommand` and `test_existing_commands_keep_text`, which gain `status,abandon` after `watch`, and `test_help_lists_remote_and_status_and_queue_stay_absent`, which keeps only `queue` absent
    And `tests/test_status.py` and the `tests/test_cli.py` additions are in-process and hermetic: run folders under `tmp_path`, the process list stubbed with `monkeypatch`, `git` and `last` stubbed with the `fake_run` fixture or run only in tmp git repos, `time.time` and `time.strftime` patched where a test asserts ages or stamps, and no real process started or signalled

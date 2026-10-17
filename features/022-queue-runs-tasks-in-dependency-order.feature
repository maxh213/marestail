Feature: marestail queue runs a folder of tasks in dependency order

  `marestail queue [PATH ...] [--to ROLE] [--from ROLE] [--here] [--retry TASK-ID ...]
  [--agent A] [--model M] [--effort E] [--scope S] [--focus PATH ...]` checks task files,
  orders them topologically (ties broken by file name), and runs them one at a time via
  `marestail run --auto`. Each task runs on its branch (or on the checked-out branch under
  `--here`). A failed task holds back only its dependants. Rerunning resumes where it left off.

  PATH defaults to `tasks/`. Every task file must reside within the repository root (the folder
  holding `marestail.toml`). Flags `--agent`, `--model`, `--effort`, `--scope`, and `--focus`
  are passed unchanged to each child run. `--to` is passed to all runs; when omitted, runs go
  through the full pipeline including qa. `--from` is passed only to the first task started.
  `--to` and `--from` are checked first with `pipeline.window(from, to, ...)`; an unknown role
  exits 2 with `unknown role <name>; choose from ...`.

  Before the first task, the queue runs pre-flight checks in order and exits 2 at the first failure:
  1. Lock: `.marestail/queue/lock` holds the PID (`O_CREAT | O_EXCL`). If present and PID is alive,
     it stops with `another marestail queue (pid N) is running in this repository`. If dead, the
     stale lock is replaced. The lock is removed on every exit path.
  2. Task check: runs 019's checker (`tasks check`) over the paths. Reports `<path>: <problem>`.
     Missing front matter or duplicate task IDs across folders fail.
  3. Tracked files: a task file ignored by git fails with `<path> is ignored by git; the queue runs only committed task files`.
  4. HEAD is a branch: detached HEAD fails with `HEAD is detached; check out a branch first`.
  5. Clean tree: `git status --porcelain --untracked-files=all` (ignoring `.marestail/`) must be clean,
     failing with `the working tree has uncommitted changes; commit or stash them first:`.
  6. No run in progress: 021's reader checks runs; any `running` run fails with
     `a marestail run is running here (<task-id>, pid N); the queue would change the tree under it`.
  7. `--retry` IDs: each must be in the set and have status `stopped` or `failed`. Otherwise fails with
     `--retry <id>: not a task in this queue` or `--retry <id>: it is <status>, not stopped or failed`.
  8. Push/PR settings: `[queue] push` / `MARESTAIL_QUEUE_PUSH` and `[queue] pr` / `MARESTAIL_QUEUE_PR`
     must be valid booleans. PR on requires push on. Push requires remote to exist. PR requires `gh` CLI.
  9. Without `--here`: fetches base remote if present, then checks `git rev-parse --verify --quiet <base>^{commit}`.
     If invalid, fails with `[git] base <base> does not exist; set it in marestail.toml`.
  State file: `.marestail/queue/state.json` must have `"v": 1` and be valid JSON, or exits 2 with
  `.marestail/queue/state.json: <problem>`.

  Branches without `--here`:
  - `depends = []`: branch `marestail/<id>` from `[git] base`, starts at once. Reuses existing branch if present.
  - `stack = true`: continues on dependency's recorded branch when dependency is done.
  - `stack = false`: branch `marestail/<id>` from `[git] base`, starts when every dependency's recorded
    head is an ancestor of base after fetch (`git merge-base --is-ancestor <head> <base>`). If not, task
    is `blocked` with `waiting for <dep> (<short head>) to be merged into <base>`.
  - Task file on branch: if missing or different from start copy, committed as `queue: task <id>`.
  - At the end, switches back to starting branch if clean. If dirty, logs `left on <branch>: the working tree has uncommitted changes`.
  With `--here`: all tasks run on the current branch without branch switching, fetching, or ancestor check.
  The first task ending `failed` or `stopped` stops the queue immediately; remaining tasks get no status, exit 1.

  Child runs execute `[sys.executable, "-u", <cli.py>, "run", <task_path>, "--auto", ...]` with
  `start_new_session=True` and cwd at repo root. Environment sets `MARESTAIL_LIMIT_WAITS=36` if unset.
  Outcomes recorded from 021 reader:
  - Exited before run-start: exit 2, `marestail run exited <code> before it started the pipeline`.
  - Complete, exit 0: `done`, reason null.
  - Complete, exit 3: `stopped`, reason `complete but NOT verified against the running app`.
  - Stopped: `stopped`, reason `stopped at <stopped_at>: <reason>`.
  - Failed: `failed`, reason from record.
  - Interrupted / no run-end: `failed`, reason `marestail run ended without finishing (exit <code>) at <role>`.

  State is written atomically to `.marestail/queue/state.json`. `queue.log` logs `<UTC>Z <word> <task-id> <detail>`
  and prints to stdout with `queue: ` prefix. Push runs after done, failed, stopped. PR created only after done
  when `gh pr list` returns 0. Exit codes: 0 (all done), 1 (failed/stopped/blocked/stopped early),
  2 (refusal or early exit), 130/143 (SIGINT/SIGTERM, child process group signalled with SIGTERM then SIGKILL).

  Scenario: tasks run in dependency order with branching, stacking, ancestor blocking, and return to start
    Given a git repository on branch "main" with "[git] base = \"origin/main\"" and remote "origin"
    And task files in "tasks/":
      | path | depends | stack | title |
      | tasks/a.md | [] | null | Task A |
      | tasks/b.md | ["a"] | true | Task B |
      | tasks/c.md | ["a"] | false | Task C |
      | tasks/d.md | [] | null | Task D |
    When I run `marestail queue`
    Then tasks execute in topological order "a", "b", "d"
    And "a" runs on new branch "marestail/a" from "origin/main" and finishes done at head "aaa111"
    And "b" switches to "marestail/a" on top of "aaa111" and finishes done at head "bbb222"
    And "c" fetches "origin" and is blocked with reason "waiting for a (aaa111) to be merged into origin/main"
    And "d" runs on new branch "marestail/d" from "origin/main" and finishes done at head "ddd444"
    And the queue switches back to branch "main"
    And the exit code is 1
    And ".marestail/queue/state.json" records:
      | task | status | branch | head | reason |
      | a | done | marestail/a | aaa111 | null |
      | b | done | marestail/a | bbb222 | null |
      | c | blocked | null | null | waiting for a (aaa111) to be merged into origin/main |
      | d | done | marestail/d | ddd444 | null |
    And stdout contains:
      """
      queue: start a branch marestail/a from origin/main
      queue: done a aaa111
      queue: start b branch marestail/a
      queue: done b bbb222
      queue: blocked c waiting for a (aaa111) to be merged into origin/main
      queue: start d branch marestail/d from origin/main
      queue: done d ddd444
      """

  Scenario: a merge commit into base unblocks stack=false tasks on rerun
    Given the repository state from the previous run where "a", "b", "d" are done and "c" is blocked
    And branch "marestail/a" is merged into "main" on "origin" with a merge commit and pushed
    When I run `marestail queue`
    Then tasks "a", "b", and "d" are skipped as already done
    And "c" fetches "origin", confirms "aaa111" is an ancestor of "origin/main", and creates "marestail/c"
    And "c" runs on "marestail/c" and finishes done at head "ccc333"
    And the queue switches back to branch "main"
    And the exit code is 0
    And ".marestail/queue/state.json" records "c" as status "done" on branch "marestail/c" at head "ccc333"

  Scenario: a failure holds back only dependants and --retry resumes from the failed role
    Given a git repository on branch "main" with tasks "a.md", "b.md", "c.md", "d.md"
    And task "a" fails during role "coder" with reason "tests failed"
    When I run `marestail queue`
    Then "a" is recorded as failed with reason "tests failed"
    And "b" and "c" are blocked with reason "a failed"
    And "d" runs on "marestail/d" and finishes done
    And the exit code is 1
    When I run `marestail queue` again without --retry
    Then no child runs are started and the exit code is 1
    When I run `marestail queue --retry a`
    Then "a" is resumed from role "coder" with child flag "--from coder"
    And "b" and "c" are evaluated after "a" completes

  Scenario: --here runs on the checked-out branch and stops at the first failure
    Given a git repository checked out on branch "marestail/green-the-repo"
    And task files "tasks/a.md", "tasks/b.md", "tasks/c.md", "tasks/d.md"
    When I run `marestail queue --here`
    Then all tasks run on branch "marestail/green-the-repo" without creating or switching branches
    And task "c" does not wait for "a" to be merged into base
    When task "a" fails during its run
    Then the queue stops immediately without starting "b", "c", or "d"
    And "b", "c", and "d" receive no status in state.json
    And the exit code is 1

  Scenario: a squash merge into base leaves stack=false dependants blocked
    Given a git repository where task "a" finished done at head "aaa111" on "marestail/a"
    And task "c" has "depends = [\"a\"]" and "stack = false"
    When "marestail/a" is squash-merged into "origin/main" so "aaa111" is not an ancestor of base
    And I run `marestail queue tasks/c.md`
    Then "c" remains blocked with reason "waiting for a (aaa111) to be merged into origin/main"
    And no child run is started
    And the exit code is 1

  Scenario: external dependencies are resolved through status and legacy runs block non-here branches
    Given a task "tasks/e.md" with depends "021-dep" and "stack = true"
    When "021-dep" is complete in 021 reader with events branch "marestail/021-dep" and head "eee111"
    Then "e" starts on branch "marestail/021-dep" and runs
    When "021-dep" has no completed run in 021 reader
    Then "e" is blocked with reason "021-dep is not in this queue and has not completed"
    When "021-dep" is complete in 021 reader via legacy logs with null events
    Then "e" is blocked with reason "021-dep has no recorded branch; run it with --here or rerun it through the queue"
    When running with "--here" against the same legacy completed "021-dep"
    Then "e" counts as ready and runs on the checked-out branch

  Scenario: a task file missing on a branch created from base is committed onto the branch
    Given task "tasks/a.md" exists on "main" but does not exist on "origin/main"
    When I run `marestail queue tasks/a.md`
    Then branch "marestail/a" is created from "origin/main"
    And "tasks/a.md" is copied to the branch and committed with message "queue: task a"
    And the child run executes with "tasks/a.md" present

  Scenario Outline: pre-flight checks refuse before starting any child or creating branches
    Given a repository setup with <condition>
    When I run `marestail queue`
    Then the exit code is 2
    And stderr contains "<message>"
    And no child run is started
    And no git branch is created

    Examples:
      | condition | message |
      | a live queue lock with PID 12345 in `.marestail/queue/lock` | another marestail queue (pid 12345) is running in this repository |
      | task file "tasks/bad.md" with no front matter | tasks/bad.md: no front matter: line 1 must be +++ |
      | duplicate task ID "tasks/a/019-x.md" and "tasks/b/019-x.md" | tasks/b/019-x.md: task id 019-x is also tasks/a/019-x.md |
      | task file "tasks/ignored.md" gitignored | tasks/ignored.md is ignored by git; the queue runs only committed task files |
      | detached HEAD | HEAD is detached; check out a branch first |
      | untracked file "untracked.py" in working tree | the working tree has uncommitted changes; commit or stash them first: |
      | an active run for task "011-live" with PID 54321 in 021 reader | a marestail run is running here (011-live, pid 54321); the queue would change the tree under it |
      | `--retry unknown-id` | --retry unknown-id: not a task in this queue |
      | `--retry done-task` for a task recorded as done | --retry done-task: it is done, not stopped or failed |
      | `--to invalid_role` | unknown role invalid_role; choose from |
      | `[queue] push = "yes"` in marestail.toml | [queue] push must be true or false, got 'yes' |
      | `MARESTAIL_QUEUE_PUSH=yes` in environment | MARESTAIL_QUEUE_PUSH must be 1, 0, true or false, got 'yes' |
      | `[queue] pr = true` with `[queue] push = false` | [queue] pr needs [queue] push |
      | push enabled but remote "origin" does not exist in git | [queue] push is on but this repository has no remote named origin |
      | `[git] base = "origin/nonexistent"` which does not resolve | [git] base origin/nonexistent does not exist; set it in marestail.toml |
      | `.marestail/queue/state.json` has `"v": 2` | .marestail/queue/state.json: unknown version 2 |

  Scenario: child runs receive exact arguments, environment limits, and map outcomes
    Given a task "tasks/a.md" and arguments "--to hardener --agent claude --model m --effort high --scope changed --focus f1 --focus f2"
    When I run `marestail queue tasks/a.md --from coder --to hardener --agent claude --model m --effort high --scope changed --focus f1 --focus f2`
    Then the first child process is started with exact argv:
      """
      run tasks/a.md --auto --from coder --to hardener --agent claude --model m --effort high --scope changed --focus f1 --focus f2
      """
    And the child environment has "MARESTAIL_LIMIT_WAITS=36" when unset in parent
    When the child exits 0 with 021 result "complete"
    Then task "a" is recorded "done" with reason null
    When the child exits 3 with 021 result "complete"
    Then task "a" is recorded "stopped" with reason "complete but NOT verified against the running app"
    When the child exits 1 with 021 result "stopped" at "critic" and reason "not approved"
    Then task "a" is recorded "stopped" with reason "stopped at critic: not approved"
    When the child exits 1 with 021 result "failed" and reason "build error"
    Then task "a" is recorded "failed" with reason "build error"
    When the child exits 1 with 021 result "interrupted" at role "coder"
    Then task "a" is recorded "failed" with reason "marestail run ended without finishing (exit 1) at coder"
    When the child exits 2 before writing events.jsonl
    Then the queue aborts with exit code 2 and message "marestail run exited 2 before it started the pipeline"

  Scenario: push branches after every run and open pull requests after done tasks
    Given `MARESTAIL_QUEUE_PUSH=1` and `MARESTAIL_QUEUE_PR=1`
    When task "a" on branch "marestail/a" finishes done
    Then "git push --quiet -u origin marestail/a" is executed
    And "gh pr list --head marestail/a --state open --json number --jq length" is executed
    And when pr list returns "0", "gh pr create --base main --head marestail/a --title Task A --body-file .marestail/queue/pr-a.md" is executed with "GH_PROMPT_DISABLED=1"
    And ".marestail/queue/pr-a.md" contains:
      """
      `marestail queue` opened this pull request after task `a` finished on `marestail/a`.

      Tasks done on this branch:
      - a: Task A

      Each commit is stamped with the role and model that made it.
      """
    When task "b" on branch "marestail/b" finishes failed
    Then "git push --quiet -u origin marestail/b" is executed
    And "gh pr list" is not executed for task "b"

  Scenario: interruption signals kill the child process group and leave state ready for resume
    Given a running child task "a" with process group PGID 67890
    When the queue receives SIGTERM
    Then the queue sends SIGTERM to process group 67890 via `os.killpg`
    And if the child does not exit within 30 seconds, the queue sends SIGKILL to process group 67890
    And task "a" keeps its existing status in state.json
    And queue.log records "interrupted a by signal 15; the next run resumes from coder"
    And the lock file ".marestail/queue/lock" is removed
    And the queue exits with code 143

  Scenario: a dirty tree before branch switch stops the queue
    Given task "a" finishes done on "marestail/a"
    And an uncommitted file "leak.txt" is left in the working tree
    When the queue attempts to switch branch for task "b"
    Then the queue stops before starting "b"
    And queue.log records "stopped before b: the working tree has uncommitted changes"
    And remaining tasks get no status in state.json
    And the queue stays on "marestail/a" and exits 1
    And stdout contains "left on marestail/a: the working tree has uncommitted changes"

  Scenario: queue command integrates into CLI and existing endpoints continue working
    When I run `marestail --help`
    Then stdout contains `{gate,run,tasks,queue,install,sonar,watch,status,abandon,dash,perf,visual,route,remote,graph,depth}`
    And stdout contains "queue       run a folder of tasks in dependency order"
    When I run `marestail queue --help`
    Then the exit code is 0
    And stdout contains "PATH"
    And stdout contains "--to"
    And stdout contains "--from"
    And stdout contains "--here"
    And stdout contains "--retry"
    When I run `marestail run` on a task file without front matter
    Then the run proceeds through the pipeline normally
    And `marestail tasks check`, `marestail status`, and `marestail gate` behave as before

Feature: marestail queue runs a folder of tasks in dependency order

  `marestail queue` checks the task files, orders them, and runs one child at a time.
  The child argv is `[sys.executable, "-u", <this checkout's marestail/cli.py>, "run",
  <path relative to the repo root>, "--auto", ...]` with cwd the repo root (the folder
  holding `marestail.toml`) and `start_new_session=True`. It is not whatever `marestail`
  is on `PATH`. Flag order after `--auto` is `--from`, `--to`, `--agent`, `--model`,
  `--effort`, `--scope`, then one `--focus` per path. A flag the user did not pass is
  absent, not empty. `--retries` is never passed. `--from` is added only for the first
  task this invocation starts, unless that task is a resume or `--retry` (those keep
  their own role). `--to` is passed to every run. With no `--to`, the argv has no `--to`.

  Checks run in this order and the first failure exits 2. Nothing has started, no branch
  has been created, and the only file written is the lock, which is removed on the way
  out. A live foreign lock is left as it was.

  1. `--to` and `--from` via `pipeline.window(from, to, "hyper" if scope == "hyper" else None)`,
     before the lock. A string SystemExit from pipeline is printed and the process exits 2.
  2. Lock `.marestail/queue/lock`, created with `O_CREAT|O_EXCL`, bytes `<pid>\n`.
     Alive means `/proc/<pid>` exists.
  3. 019's checker. Same `<path>: <problem>` lines as `marestail tasks check`, on stdout.
     The queue exits 2; `tasks check` of the same paths still exits 1.
  4. Resolved path inside the repo root. Otherwise stderr
     `<path> is outside the repository; the queue runs only task files inside <root>`
     with `<path>` as given and `<root>` the resolved directory of `marestail.toml`.
  5. Not gitignored.
  6. `git symbolic-ref --quiet --short HEAD` succeeds.
  7. Clean tree: `git status --porcelain --untracked-files=all`, drop paths under
     `.marestail/`, take the path (the name after ` -> ` when renamed), at most 20.
  8. No 021 run with state `running`.
  9. If `.marestail/queue/state.json` exists it is JSON and `"v"` is 1. A missing file
     is an empty task map.
  10. Each `--retry` id is in the set and its stored status is `stopped` or `failed`.
  11. Push and PR settings.
  12. Without `--here`: `git fetch --quiet <remote>` when `[git] base` is `<remote>/<branch>`
      and `git remote` lists `<remote>`, then `git rev-parse --verify --quiet <base>^{commit}`.
      A base with no remote part is not fetched. `--here` does not fetch and does not
      require the base to exist.

  Checker lines go to stdout. Every other refusal goes to stderr. Both streams are
  otherwise empty for that refusal.

  Each log line is `<YYYY-MM-DDTHH:MM:SS>Z <rest>`, appended to `.marestail/queue/queue.log`
  and printed as `queue: ` plus that line. Task events use `<word> <id> <detail>`:
  `start <id> branch <branch> from <where>[ --from <role>]` (`<where>` is the base,
  `existing`, the dependency branch, or `here`), `skip <id> done`,
  `done <id> <short> <minutes>`, `failed <id> <reason>`, `stopped <id> <reason>`,
  `blocked <id> <reason>`, `pushed <id> <remote> <branch>`,
  `push-failed <id> <branch>: <last line>`, `pr-opened <id> <branch>`,
  `pr-failed <id> <branch>: <last line>`,
  `interrupted <id> by signal <n>; the next run resumes from <role>`.
  `<short>` is `git rev-parse --short` of the recorded head. `<minutes>` is the whole
  minutes from the child's start to its exit. `end` has no task id:
  `end done <n> failed <n> stopped <n> blocked <n>`. Three operational lines also have
  no task-id slot; the rest of the line is the sentence:
  `stopped before <id>: the working tree has uncommitted changes`,
  `left on <branch>: the working tree has uncommitted changes`,
  `fetch <remote> failed: <last line of git fetch stderr>`.

  State is `os.replace` of a temp file in `.marestail/queue/` onto `state.json`.
  `head` is 40 hex or null. `finished_at` is `YYYY-MM-DDTHH:MM:SSZ`. `to` is the
  `--to` role or null. `exit_code` is the child's code or null. `reason` is null only
  when status is `done`. Entries whose id is outside this invocation's set are copied
  through unchanged. The queue reads 021's `result` (021's state folds `failed` into
  `stopped`). An in-set `failed` dependency blocks dependants, direct or not, with
  `<id> failed`. An in-set `stopped` dependency does the same with `<id> stopped`.

  Scenario: file name breaks ties only after dependencies are ordered
    Given "tasks/001.md" has `depends = ["002"]` and `stack = true` and "tasks/002.md" has `depends = []`
    When I run `marestail queue`
    Then the first child path is "tasks/002.md" and the second is "tasks/001.md"

  Scenario: branches, stacking, and a blocked ancestor check
    Given the clock is frozen at 2026-10-17T12:00:00Z and advances one minute per child
    And the repo is on "main" with "[git] base = origin/main" and remote "origin"
    And state.json already holds task "z" done on "marestail/z" at "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", finished_at "2026-10-16T00:00:00Z", to null, exit_code 0, reason null
    And the committed tasks are a (depends []), b (depends a, stack true), c (depends a, stack false), d (depends [])
    And each child's commit is the HEAD `git rev-parse` shows after that child: a "111111111111111111111111111111111111111a" (short "1111111"), b "222222222222222222222222222222222222222b" (short "2222222"), d "444444444444444444444444444444444444444d" (short "4444444")
    When I run `marestail queue`
    Then a is created with `git switch --quiet --no-track --create marestail/a origin/main` and has no upstream
    And b is `git switch --quiet marestail/a` and is not created from origin/main
    And c starts no child
    And d is created from origin/main as "marestail/d"
    And the queue is back on "main" and the exit code is 1
    And stdout is:
      """
      queue: 2026-10-17T12:00:00Z start a branch marestail/a from origin/main
      queue: 2026-10-17T12:01:00Z done a 1111111 1
      queue: 2026-10-17T12:01:00Z start b branch marestail/a from marestail/a
      queue: 2026-10-17T12:02:00Z done b 2222222 1
      queue: 2026-10-17T12:02:00Z blocked c waiting for a (1111111) to be merged into origin/main
      queue: 2026-10-17T12:02:00Z start d branch marestail/d from origin/main
      queue: 2026-10-17T12:03:00Z done d 4444444 1
      queue: 2026-10-17T12:03:00Z end done 3 failed 0 stopped 0 blocked 1
      """
    And state.json equals that run's tasks plus the unchanged "z" entry, with a, b and d done at those 40-hex heads, exit_code 0, to null, reason null, finished_at 12:01:00Z, 12:02:00Z and 12:03:00Z, and c blocked with branch null, head null, exit_code null, finished_at "2026-10-17T12:02:00Z", reason "waiting for a (1111111) to be merged into origin/main"
    And the short form is not stored in "head"

  Scenario: a merge commit of marestail/a unblocks c
    Given the state and commits from the branch scenario: a and b done on "marestail/a" at "111111111111111111111111111111111111111a" and "222222222222222222222222222222222222222b", c blocked, d done on "marestail/d" at "444444444444444444444444444444444444444d", z unchanged
    And "marestail/a" is merged into the bare origin's main with a merge commit and that main is what a later fetch calls origin/main
    And `git merge-base --is-ancestor 111111111111111111111111111111111111111a origin/main` exits 0
    When I run `marestail queue`
    Then the only child is c
    And the log skips a, b and d with `skip a done`, `skip b done` and `skip d done`
    And "marestail/c" is created from that origin/main, so c's parent is `git rev-parse origin/main` from before c's commit
    And c's commit is "333333333333333333333333333333333333333c" (short "3333333") and c is done at that 40-hex head
    And the queue is back on "main" and the exit code is 0
    And the end line is `end done 4 failed 0 stopped 0 blocked 0`

  Scenario: a failure blocks dependants only, including through b
    Given a, b (depends a, stack true), c (depends a, stack false), d (depends []), and e (depends b, stack true)
    And a's child writes result "failed", reason "tests failed", and its last step role is "coder"
    When I run `marestail queue`
    Then a is failed with reason "tests failed"
    And b, c and e are blocked with reason "a failed"
    And e's reason is not "b failed" and not "b blocked"
    And d runs on "marestail/d" and is done
    And the exit code is 1

  Scenario: a plain rerun does not start a failed task again
    Given a is failed with reason "tests failed", b and c and e are blocked with "a failed", and d is done
    When I run `marestail queue`
    Then no child starts, those statuses stay, and the exit code is 1

  Scenario: --retry a resumes a, then b runs and c stays blocked
    Given a is failed at role "coder", b and c and e are blocked with "a failed", and d is done
    And this invocation's fake for a returns result "complete" and exit_code 0
    When I run `marestail queue --retry a`
    Then a's argv is `[sys.executable, "-u", <cli.py>, "run", "tasks/a.md", "--auto", "--from", "coder"]`
    And after a is done, b's argv is `[sys.executable, "-u", <cli.py>, "run", "tasks/b.md", "--auto"]` with no `--from`, on branch "marestail/a"
    And c is blocked with `waiting for a (<short of a's new head>) to be merged into origin/main`
    And e follows b
    And d is skipped
    And the exit code is 1

  Scenario: a stopped dependency uses the same shape as a failed one
    Given b depends on a and a's child returns result "stopped", stopped_at "critic", reason "not approved"
    When I run `marestail queue`
    Then a is stopped with reason "stopped at critic: not approved"
    And b is blocked with reason "a stopped"
    And the exit code is 1

  Scenario: --here runs all four on the checked-out branch
    Given HEAD is "marestail/green-the-repo" and origin's URL is not a git repository
    And a, b, c and d are the four tasks above, and every child returns complete exit 0
    When I run `marestail queue --here`
    Then all four children run on "marestail/green-the-repo"
    And no branch "marestail/a" exists and the log has no `fetch ` line
    And c is done, not blocked
    And the exit code is 0

  Scenario Outline: --here stops at the first failed or stopped task
    Given HEAD is "marestail/green-the-repo" and a's child returns result "<result>"
    When I run `marestail queue --here`
    Then a is "<status>" and b, c and d have no key in state.json
    And no child starts for b, c or d
    And the exit code is 1

    Examples:
      | result  | status  |
      | failed  | failed  |
      | stopped | stopped |

  Scenario: a squash merge leaves c blocked
    Given a is done at "111111111111111111111111111111111111111a" on "marestail/a" and c depends on a with stack false
    And that commit was squash-merged, so `git merge-base --is-ancestor 111111111111111111111111111111111111111a origin/main` exits 1
    When I run `marestail queue tasks/c.md`
    Then c is blocked with reason "waiting for a (1111111) to be merged into origin/main"
    And no child starts and the exit code is 1

  Scenario Outline: a dependency outside the set
    Given "tasks/e.md" depends on "021-dep" with stack <stack>
    And 021's reader reports <record>
    When I run `marestail queue <flags>`
    Then <outcome>

    Examples:
      | stack | flags | record | outcome |
      | true |  | complete, events branch marestail/021-dep, head 111111111111111111111111111111111111111a | e's child runs on marestail/021-dep |
      | true |  | no run | blocked "021-dep is not in this queue and has not completed", no child, exit 1 |
      | true |  | result stopped | the same not-in-queue sentence, not "021-dep failed" |
      | true |  | result failed | the same not-in-queue sentence, not "021-dep failed" |
      | true |  | legacy complete, events null | blocked "021-dep has no recorded branch; run it with --here or rerun it through the queue" |
      | false |  | legacy complete, events null | the same no-recorded-branch sentence |
      | true | --here | legacy complete, events null | e runs on the checked-out branch and is not blocked |

  Scenario: a missing task file is committed onto the new branch
    Given "tasks/a.md" is committed on "main" and is absent from origin/main
    When I run `marestail queue tasks/a.md`
    Then after `git switch --create`, the queue writes the start-time bytes and `git commit --quiet -m "queue: task a" -- tasks/a.md`
    And `git log -1 --format=%s` on that commit is "queue: task a" and the only path is "tasks/a.md"
    And the child then runs

  Scenario: an existing task branch is switched to and not reset
    Given "marestail/a" already exists and its tip commit message is "keep me", which is not origin/main
    When I run `marestail queue tasks/a.md`
    Then the queue runs `git switch --quiet marestail/a` and does not reset
    And the child's parent is the "keep me" commit

  Scenario: --here does not commit the task file
    Given HEAD already contains "tasks/a.md"
    When I run `marestail queue --here tasks/a.md`
    Then no commit message is "queue: task a"

  Scenario Outline: the queue refuses before any child or branch
    D in the running-run row is that shim's decimal pid.

    Given a fresh repo whose only fault is <fault>
    When I run `<command>`
    Then the exit code is 2, stdout is "<stdout>", stderr is "<stderr>"
    And no child starts, no branch is created, and the lock is gone
    And an existing state.json is not rewritten

    Examples:
      | fault | command | stdout | stderr |
      | tasks/bad.md is "# bad" with no fence | marestail queue | tasks/bad.md: no front matter: line 1 must be +++ |  |
      | committed tasks/a/019-x.md and tasks/b/019-x.md, both `depends = []` | marestail queue tasks/a tasks/b | tasks/b/019-x.md: duplicate task id 019-x: also tasks/a/019-x.md |  |
      | tasks/ignored.md is valid and listed in .gitignore | marestail queue |  | tasks/ignored.md is ignored by git; the queue runs only committed task files |
      | HEAD detached | marestail queue |  | HEAD is detached; check out a branch first |
      | shim cli.py cwd the repo, argv ends in run tasks/011-live.md, events run-start of that pid, no run-end | marestail queue |  | a marestail run is running here (011-live, pid D); the queue would change the tree under it |
      | no task 099-nope | marestail queue --retry 099-nope |  | --retry 099-nope: not a task in this queue |
      | d stored done | marestail queue --retry d |  | --retry d: it is done, not stopped or failed |
      | none | marestail queue --to nonexistent |  | unknown role nonexistent; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa |
      | none | marestail queue --from nonexistent |  | unknown role nonexistent; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa |
      | none | marestail queue --scope hyper --to cleaner |  | unknown role cleaner; choose from specifier, critic, coder, architect, blast, hardener, qa |
      | [queue] push = "yes" | marestail queue |  | [queue] push must be true or false, got 'yes' |
      | MARESTAIL_QUEUE_PUSH=yes | marestail queue |  | MARESTAIL_QUEUE_PUSH must be 1, 0, true or false, got 'yes' |
      | [queue] pr = true and push false | marestail queue |  | [queue] pr needs [queue] push |
      | push true, no git remote, base main | marestail queue |  | [queue] push is on but this repository has no remote named origin |
      | push true, pr true, PATH has no gh | marestail queue |  | [queue] pr needs the gh CLI on PATH |
      | base origin/nonexistent | marestail queue |  | [git] base origin/nonexistent does not exist; set it in marestail.toml |
      | state.json bytes {"v": 2} | marestail queue |  | .marestail/queue/state.json: v must be 1, got 2 |
      | state.json bytes { | marestail queue |  | .marestail/queue/state.json: not valid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1) |
      | valid ../else/a.md outside /tmp/qa-022/outside/repo | marestail queue ../else/a.md |  | ../else/a.md is outside the repository; the queue runs only task files inside /tmp/qa-022/outside/repo |

  Scenario: a live lock is left in place
    Given the lock holds live pid 12345
    When I run `marestail queue`
    Then stderr is "another marestail queue (pid 12345) is running in this repository"
    And the lock is still "12345\n"
    And no child starts and the exit code is 2

  Scenario: a bad role is rejected before the lock
    Given the lock holds live pid 12345
    When I run `marestail queue --to nonexistent`
    Then stderr is "unknown role nonexistent; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa"
    And stdout has no "another marestail queue"
    And the lock is still "12345\n"
    And the exit code is 2

  Scenario: a stale lock is replaced and the next check speaks
    Given the lock holds pid 999999 and /proc/999999 does not exist and there is no tasks directory
    When I run `marestail queue`
    Then stdout is "tasks: no such file or folder"
    And stderr has no "another marestail queue"
    And the exit code is 2 and the lock is gone

  Scenario: an untracked file under .marestail does not fail the clean check
    Given ".marestail/notes.txt" is untracked and "[queue] push = \"yes\""
    When I run `marestail queue`
    Then stderr is "[queue] push must be true or false, got 'yes'"
    And stderr has no "uncommitted changes"
    And the exit code is 2

  Scenario: a modified tracked task file is dirty
    Given committed "tasks/a.md" is then edited
    When I run `marestail queue`
    Then the exit code is 2 and stderr is:
      """
      the working tree has uncommitted changes; commit or stash them first:
      tasks/a.md
      """

  Scenario: an untracked file outside .marestail is dirty
    Given "untracked.py" is untracked
    When I run `marestail queue`
    Then the exit code is 2 and stderr is:
      """
      the working tree has uncommitted changes; commit or stash them first:
      untracked.py
      """

  Scenario: the clean check prints at most 20 paths
    Given untracked files p01 through p21 and git status lists them in that order
    When I run `marestail queue`
    Then stderr lists p01 through p20 and does not list p21
    And the exit code is 2

  Scenario: the first child gets every flag and the second does not get --from
    Given "tasks/a.md" and "tasks/d.md" both have depends []
    When I run `marestail queue --from coder --to hardener --agent claude --model m --effort high --scope changed --focus x --focus y`
    Then a's argv is [sys.executable, "-u", <this checkout's marestail/cli.py>, "run", "tasks/a.md", "--auto", "--from", "coder", "--to", "hardener", "--agent", "claude", "--model", "m", "--effort", "high", "--scope", "changed", "--focus", "x", "--focus", "y"]
    And d's argv is the same with "tasks/d.md" and without "--from" and "coder"
    And both children have cwd the repo root, start_new_session True, and no "--retries"
    And a's state "to" is "hardener", "exit_code" is 0, and "finished_at" is the frozen UTC stamp

  Scenario: omitting --to omits the flag
    When I run `marestail queue tasks/a.md`
    Then the child argv has no "--to"

  Scenario Outline: MARESTAIL_LIMIT_WAITS is 36 only when unset
    Given the parent environment <parent>
    When I run `marestail queue tasks/a.md`
    Then the child environment MARESTAIL_LIMIT_WAITS is "<child>"

    Examples:
      | parent | child |
      | unset | 36 |
      | 5 | 5 |

  Scenario Outline: the queue maps a child it did not signal
    Given the queue did not signal the child
    When I run `marestail queue tasks/a.md` and the child <ending>
    Then <recorded>

    Examples:
      | ending | recorded |
      | exits 2 and writes no run-start, or the record pid is not the child | exit 2, stderr "marestail run exited 2 before it started the pipeline", no state key for a |
      | result complete, exit_code 0 | status done, reason null, queue exit 0 |
      | result complete, exit_code 3 | status stopped, reason "complete but NOT verified against the running app", queue exit 1 |
      | result stopped, stopped_at critic, reason "not approved" | status stopped, reason "stopped at critic: not approved", queue exit 1 |
      | result failed, reason "build error" | status failed, reason "build error", queue exit 1 |
      | result interrupted, or no run-end, exit 1, role coder | status failed, reason "marestail run ended without finishing (exit 1) at coder", queue exit 1 |

  Scenario Outline: resume comes from the reader unless --retry or --from says otherwise
    Given 021 reports <record> for task a and a has no queue status yet
    When I run `marestail queue tasks/a.md`
    Then <outcome>

    Examples:
      | record | outcome |
      | dead, role coder, pid not alive | child argv contains "--from" "coder" |
      | result stopped, stopped_at critic, reason "not approved" | state stopped with reason "stopped at critic: not approved", no child |
      | result failed, reason "build error" | state failed with reason "build error", no child |
      | abandoned | child argv has no "--from" |
      | leftover | child argv has no "--from" |
      | no run | child argv has no "--from" |

  Scenario: an explicit --from applies only to the first task started
    Given a has no run and b is dead at role qa, and neither depends on the other
    When I run `marestail queue --from specifier`
    Then a's argv contains "--from" "specifier"
    And b's argv contains "--from" "qa" and not "specifier"

  Scenario: --retry after exit 3 resumes from qa
    Given a is stopped with reason "complete but NOT verified against the running app" and the reader role is qa
    When I run `marestail queue --retry a`
    Then a's argv contains "--from" "qa"

  Scenario Outline: push follows a task that ran, and a blocked task is not pushed
    Given MARESTAIL_QUEUE_PUSH=1
    When the child for a ends <ending> and the next task is <next>
    Then origin has branch "marestail/a" at a's recorded head
    And <next> is not pushed

    Examples:
      | ending | next |
      | complete exit 0 | a blocked dependant |
      | result failed | a blocked dependant |
      | result stopped | a blocked dependant |

  Scenario: push off does not push
    Given "[queue] push = true" and MARESTAIL_QUEUE_PUSH=0
    When a's child returns complete and the exit code is 0
    Then origin has no "marestail/a"

  Scenario: a failed push is logged and the exit code stays
    Given push is on and origin's URL is not a git repository and a's child returns complete exit 0
    When I run `marestail queue tasks/a.md`
    Then the log line is `push-failed a marestail/a: and the repository exists.`
    And a stays done and the exit code is 0

  Scenario: a fetch failure is logged and the queue continues
    Given origin's URL is not a git repository and c depends on a with stack false
    When I run `marestail queue` and a's child returns complete
    Then the log contains `fetch origin failed: and the repository exists.`
    And a is done and c is blocked and the exit code is 1

  Scenario: gh pr list 0 opens one pull request
    Given MARESTAIL_QUEUE_PUSH=1, MARESTAIL_QUEUE_PR=1, and a's body begins "# Task A"
    And `gh pr list --head marestail/a --state open --json number --jq length` prints 0
    When I run `marestail queue tasks/a.md` and a's child returns complete exit 0
    Then the queue runs `gh pr create --base main --head marestail/a --title "Task A" --body-file .marestail/queue/pr-a.md` once, with GH_PROMPT_DISABLED=1
    And the log contains `pr-opened a marestail/a`
    And ".marestail/queue/pr-a.md" is:
      """
      `marestail queue` opened this pull request after task `a` finished on `marestail/a`.

      Tasks done on this branch:
      - a: Task A

      Each commit is stamped with the role and model that made it.
      """

  Scenario: a later done task on that branch does not rewrite the body
    Given the body file above was written after a, and b is stacked on a and then finishes done
    And `gh pr list` for "marestail/a" prints 1
    When that second task finishes
    Then `gh pr create` is not run again and ".marestail/queue/pr-a.md" is byte for byte unchanged

  Scenario: a failed task does not ask gh about a pull request
    Given push and pr are on
    When a's child returns result failed
    Then `git push` runs and `gh pr list` is not run

  Scenario: gh pr list 1 does not create
    Given push and pr are on and `gh pr list` prints 1
    When a's child returns complete exit 0
    Then `gh pr create` is not run

  Scenario: a task with no heading uses its id as the title
    Given push and pr are on, `gh pr list` prints 0, and the task body has no "# " line
    When that task finishes done
    Then `gh pr create` is called with `--title` equal to the task id

  Scenario: MARESTAIL_QUEUE_PR=true beats a false key
    Given "[queue] pr = false", "[queue] push = true", and MARESTAIL_QUEUE_PR=true
    When a finishes done and `gh pr list` prints 0
    Then `gh pr create` is run

  Scenario: an empty push variable counts as off and beats the key
    Given "[queue] push = true", "[queue] pr = true", and MARESTAIL_QUEUE_PUSH is empty
    When I run `marestail queue`
    Then the exit code is 2 and stderr is "[queue] pr needs [queue] push"

  Scenario: a gh failure does not change the outcome
    Given push and pr are on, `gh pr list` prints 0, and `gh pr create` exits 1 with last stderr line "auth required"
    When a finishes done
    Then the log contains `pr-failed a marestail/a: auth required`
    And a stays done and the exit code is 0

  Scenario: SIGTERM kills the child group and leaves the task unrecorded as failed
    Given a's child is still running in its own session and its reader role is coder
    When the queue receives SIGTERM
    Then it `os.killpg`s the child's group with SIGTERM, waits up to 30 seconds, and SIGKILLs the group if it is still alive
    And the group has no live process after the queue exits
    And a's state entry is unchanged
    And the log line is `interrupted a by signal 15; the next run resumes from coder`
    And nothing is pushed, the branch is not switched back, the lock is gone, and the exit code is 143
    And a's status is not the "ended without finishing" failure

  Scenario: SIGINT between tasks starts nothing else
    Given a has just been recorded done and b has not started
    When the queue receives SIGINT
    Then no child starts for b, nothing is pushed, HEAD stays on a's branch, the lock is gone, and the exit code is 130

  Scenario: dirt that appears after a done child stops the next switch
    Given a's child has exited done and only then is "leak.txt" created
    When the queue is about to switch for b
    Then the log contains `stopped before b: the working tree has uncommitted changes`
    And b has no state key and the exit code is 1
    And the log contains `left on marestail/a: the working tree has uncommitted changes`
    And HEAD stays on "marestail/a"

  Scenario: no PATH argument checks tasks/
    Given "tasks/bad.md" has no front matter and "other/bad.md" has no front matter
    When I run `marestail queue`
    Then stdout is "tasks/bad.md: no front matter: line 1 must be +++"
    And stdout has no "other/bad.md"
    And the exit code is 2

  Scenario: help, the layer, the README, and the commands that already exist
    When I run `marestail --help`
    Then stdout contains "{gate,run,tasks,queue,install,sonar,watch,status,abandon,dash,perf,visual,route,remote,graph,depth}"
    And stdout contains the line "    queue               run a folder of tasks in dependency order"
    And tests/test_cli.py COMMAND_CHOICES, test_help_lists_remote_and_status_and_queue_stay_absent, and test_status_and_abandon_join_the_cli no longer treat queue as an invalid choice
    And tests/test_green_repo.py test_help_lists_every_subcommand and tests/test_events.py test_existing_commands_keep_text list queue after tasks
    And ".importlinter" has a layers line "marestail.queue" directly under "marestail.cli"
    And contract pipeline-belongs-to-orchestration is named "The role list and how a scope reshapes it are known only to the runner, the prompt builder and the queue" and its allowed_importers include marestail.queue
    And "gh" is in FORBIDDEN_BINARIES in tests/conftest.py
    And README has "## Queue" as the next heading after the Overnight section, whose text is unchanged through the line before "## Queue"
    And that Queue section covers the command, the a/b/c/d branch rules, --here, resume and --retry, push and pr, the merge-commit note, state.json and queue.log, and the exit codes
    And it does not backtick a tasks/ path, in a code block or out of one, because the docs gate reads every line
    And the environment table has the MARESTAIL_QUEUE_PUSH and MARESTAIL_QUEUE_PR rows from the task
    And the MARESTAIL_LIMIT_WAITS cell still says the default is 12 and also says marestail queue sets 36 for its runs when it is unset
    And tools/overnight.sh is unchanged and no path under .marestail/phases/ is added, edited or deleted

  Scenario: queue help lists the queue flags and not --retries
    When I run `marestail queue --help`
    Then the exit code is 0
    And stdout lists --to, --from, --here, --retry, --agent, --model, --effort, --scope and --focus
    And stdout does not list --retries

  Scenario: marestail run still accepts a file with no front matter
    Given "plain.md" is the bytes "# plain"
    When I run `marestail run plain.md --to not-a-role`
    Then the exit code is 1 and stderr is "unknown role not-a-role; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa"
    And neither stream contains "no front matter"

  Scenario: tasks check still rejects a file with no front matter
    When I run `marestail tasks check plain.md`
    Then the exit code is 1 and stdout is "plain.md: no front matter: line 1 must be +++"

  Scenario: status help is unchanged
    When I run `marestail status --help`
    Then the exit code is 0 and stdout contains "--json", "--all" and "--queued"

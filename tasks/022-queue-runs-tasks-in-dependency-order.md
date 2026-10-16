+++
depends = ["021-status-says-where-every-run-stands"]
stack = true
+++
# 022 — `marestail queue` runs a folder of tasks in dependency order

After this task, someone with a folder of task files can type `marestail queue` and walk away. marestail checks the files, puts them in dependency order and runs them one at a time, each through `marestail run --auto`. Each task goes on the right branch. If a task fails, only the tasks that depend on it are held back. marestail records what finished and can push each branch and open its PR. Running the same command again carries on where it left off. Today that job is done by hand-written drivers: `.marestail/phases/run-queue-all-opus.sh`, `run-queue-all.sh`, `run-queue.sh` and `run-queue-016.sh`, each wrapping `tools/overnight.sh`. They live in gitignored files, run one hard-coded branch, and keep their own state file. They also stop the whole queue at the first failure, even when later tasks don't depend on it. A scheduler that runs marestail on several machines needs the same thing as a command with a stable state file. So does one person with one laptop.

This builds on 019 (front matter and `marestail tasks check`), 020 (`.marestail/runs/<task-id>/events.jsonl`) and 021 (`marestail status` and its run reader). It uses their modules as they ship them. It reimplements none of them.

## Command

```
marestail queue [PATH ...] [--to ROLE] [--from ROLE] [--here] [--retry TASK-ID ...]
                [--agent A] [--model M] [--effort E] [--scope S] [--focus PATH ...]
```

- `PATH` is a folder or `.md` files, as for `marestail tasks check`. The default is `tasks/`. Paths are resolved from the current directory. The repository root is the folder holding `marestail.toml` (`config.load`, `marestail/config.py:151`). Every task file must be inside that root.
- `--agent`, `--model`, `--effort`, `--scope` and `--focus` (repeatable) take the same choices as `marestail run` (`marestail/cli.py:164-175`). They are passed unchanged to every run. `--retries` is not passed, so every run keeps the default of unlimited attempts per role.
- `--to ROLE` is passed to every run. When `--to` is left out, no `--to` is passed and each run goes through the whole pipeline, qa included, as `marestail run` does. The queue has no hardener default (see the table below).
- `--from ROLE` is passed only to the first task this invocation starts. Tasks that are skipped or blocked don't count as starting.
- Before anything else, `--to` and `--from` are checked against the pipeline with `pipeline.window(from, to, "hyper" if scope == "hyper" else None)` (`marestail/pipeline.py:107`). An unknown role exits 2 with pipeline's own message (`unknown role X; choose from ...`, `pipeline.py:87`). To allow this, add `marestail.queue` to `allowed_importers` of the `pipeline-belongs-to-orchestration` contract in `.importlinter`, and rename the contract to say the role list is known to "the runner, the prompt builder and the queue".
- `--retry TASK-ID` can be repeated. See Resume.
- The command lives in `marestail/queue.py`. `marestail/cli.py` gets `add_queue` and a `queue_command` handler that imports `marestail.queue` lazily, as `run_command` does (`cli.py:407`). In `.importlinter`, `marestail.queue` goes in a new layer of its own directly below `marestail.cli`. That puts it above every module it imports: config, context, pipeline, shell, 019's task-file module and 021's status module.

## Before the first task

The queue works through these checks in order and exits 2 at the first one that fails. By then it has started nothing, created no branch and written nothing except the lock.

1. Lock: `.marestail/queue/lock` holds the queue's pid and is created with `O_CREAT | O_EXCL`. If the file is there and its pid is alive, the queue stops with `another marestail queue (pid N) is running in this repository`. If the pid is dead, the lock is stale and gets replaced. The queue removes the lock on every exit path.
2. Task check: 019's checker, the same function behind `marestail tasks check`, runs over the given paths. The queue prints its problems in the same `<path>: <problem>` lines. The set of tasks is exactly the set `tasks check` checks for those paths. A file with no front matter is a problem here, even though `marestail run` accepts one. Two files with the same id (the same file name in two folders) are a problem too: `<path>: task id 019-x is also <other path>`.
3. Task files are tracked: a task file that git ignores fails with `<path> is ignored by git; the queue runs only committed task files`. This covers anything under `.marestail/`, including the gitignored phase files in `.marestail/phases/`.
4. HEAD is a branch: `git symbolic-ref --quiet --short HEAD`. A detached HEAD fails with `HEAD is detached; check out a branch first`.
5. Clean tree (next section).
6. No run in progress: 021's reader lists the runs of this repository. If any run is `running`, the queue stops with `a marestail run is running here (<task-id>, pid N); the queue would change the tree under it`. This applies with `--here` as well, because two pipelines on one working tree reject each other's uncommitted files.
7. `--retry` ids: each one must be a task in the set, and its status (see Resume) must be `stopped` or `failed`. Otherwise the queue stops with `--retry <id>: not a task in this queue` or `--retry <id>: it is <status>, not stopped or failed`.
8. Push and PR settings (see Push and PR).
9. Without `--here`: the queue fetches (see Branches), then confirms that `[git] base` resolves with `git rev-parse --verify --quiet <base>^{commit}`. The base comes from `config.get("git", "base", DEFAULT_BASE)`, and `DEFAULT_BASE` is `origin/master` (`marestail/context.py:18`). If it doesn't resolve, the queue stops with `` [git] base <base> does not exist; set it in marestail.toml ``.

## Clean working tree

The check runs the runner's command, `git status --porcelain --untracked-files=all` (`marestail/runner.py:102`). It drops any path under `.marestail/`, which is the runner's own `outside_work` rule at `runner.py:968`. Gitignored files never appear in that output. Everything else counts as dirty, and that includes untracked and modified task files. The failure message is `the working tree has uncommitted changes; commit or stash them first:`, followed by up to 20 paths.

Task files get no exception. `marestail run` itself can't live with an uncommitted file. After every worker, `verify_worker` (`runner.py:852`) turns any uncommitted path into an `uncommitted changes` problem (`runner.py:866`). After a judge, `discard_edits` runs `git clean -fdq -e <handoff> -e .marestail/` (`runner.py:996-997`), which would delete an untracked task file. That is why the drivers commit each phase before its run (`human: queue task NNN`).

The check runs at start, in every mode. Without `--here` it runs again before every branch switch. A dirty tree at a later switch stops the queue: it logs `stopped before <id>: the working tree has uncommitted changes`, gives the remaining tasks no status, and exits 1.

## Order

Tasks run in topological order, with ties broken by file name. Take the task whose dependencies inside the set are all ordered and whose file name sorts first. Then repeat. There is one child run at a time.

A dependency that isn't in the set must still be done, as defined below, or the task is `blocked`: `<dep> is not in this queue and has not completed`.

A dependency counts as done in either of two cases:

- the queue's state says `done`, or
- 021's reader says its latest run is `complete`. The queue then records that dependency as `done`, taking `branch` and `head` from the record. The branch comes from that run's `run-start` event.

A record from a run with no events.jsonl (021's legacy path, `events` null) has no trustworthy branch or head: 021 fills `branch` with the repo's current checkout and `head` with null. The queue treats both as unknown for such a record.

## Branches

Without `--here`:

| task | branch | starts when |
|---|---|---|
| `depends = []` | `marestail/<task-id>` from `[git] base` | at once |
| `stack = true` | the dependency's recorded branch, continued | the dependency is done |
| `stack = false` | `marestail/<task-id>` from `[git] base` | every dependency's recorded `head` is an ancestor of base after a fetch |

- Fetch: `[git] base` of the form `<remote>/<branch>`, where `<remote>` is listed by `git remote`, is fetched with `git fetch --quiet <remote>`. This happens once at start and again before each `stack = false` check. A base with no remote part (such as a local `main`) is never fetched. If a fetch fails, the queue logs `fetch <remote> failed: <last line>` and carries on with the refs it already has.
- New branch: `git switch --quiet --no-track --create marestail/<id> <base>`. `--no-track` stops the new branch tracking the base. If `marestail/<id>` already exists (from a resume or an earlier attempt), the queue runs `git switch --quiet marestail/<id>` and reuses it without resetting it.
- Stacked: `git switch --quiet <branch>`. Two tasks stacked on the same dependency run one after the other on that branch, in queue order.
- Ancestor test: `git merge-base --is-ancestor <head> <base>`. Exit 0 means merged and exit 1 means not yet. Until every dependency passes, the task is `blocked` with `waiting for <dep> (<short head>) to be merged into <base>`. A squash or rebase merge never makes the head an ancestor, so such a dependency stays blocked. The README says to merge queue PRs with a merge commit.
- A dependency that is done but has an unknown `branch` (null, or from a legacy record) blocks a `stack = true` task with `<dep> has no recorded branch; run it with --here or rerun it through the queue`. An unknown `head` blocks a `stack = false` task with the same wording.
- Task file on the branch: a branch made from base may not hold the task file. After switching, the queue compares the file at that path with the copy it read at start. If the file is missing or different, the queue writes the start-time copy and commits that path alone with `git commit --quiet -m "queue: task <id>" -- <path>`. Under `--here` the start check has already made HEAD hold every task file, so the queue commits nothing.
- At the end, if the queue switched branches and the tree is clean, it switches back to the branch it started on. If the tree is dirty, it stays put and logs `left on <branch>: the working tree has uncommitted changes`.

With `--here`, every task runs on the branch that is checked out, in queue order. The stack flags and the "merged into base" condition are ignored. A dependency only has to be done. Nothing is fetched and no branch is created or switched. One rule is tighter than without `--here`: the first task that ends `failed` or `stopped` stops the queue. Every task after it would be built on top of that task's partial commits on the same branch. The remaining tasks get no status and the queue exits 1.

### Worked example

`tasks/` holds `a.md` (`depends = []`), `b.md` (`depends = ["a"]`, `stack = true`), `c.md` (`depends = ["a"]`, `stack = false`) and `d.md` (`depends = []`). `[git] base = "origin/main"`. The queue starts on `main`.

The order is a, b, c, d. At the start a and d are ready and a sorts first. Once a is done, b, c and d are ready, in that order.

1. **All pass, nothing merged yet.**
   - a runs on the new branch `marestail/a` from `origin/main` and ends done at head `A1`.
   - b switches to `marestail/a` and runs on top of `A1`. It ends done at `B1`.
   - c: the queue fetches `origin`. `A1` is not an ancestor of `origin/main`, so c is `blocked` with `waiting for a (A1) to be merged into origin/main`.
   - d runs on the new branch `marestail/d` from `origin/main` and ends done.
   - The queue switches back to `main` and exits 1, because c isn't done.
   - state.json: a `done` on `marestail/a` at `A1`, b `done` on `marestail/a` at `B1`, c `blocked`, d `done` on `marestail/d`.
2. **After `marestail/a` is merged into main with a merge commit and pushed.** The same command skips a, b and d without starting a run. For c, it fetches, finds `A1` is an ancestor of `origin/main`, and creates `marestail/c` from `origin/main`, which now holds a's and b's commits. c runs. The queue exits 0 when c is done.
3. **a fails.** a is recorded `failed`. b and c are `blocked` with `a failed`, and so is anything that depends on b or c, directly or not. d is independent, so it still runs on `marestail/d`. The queue exits 1. Rerunning the same command starts no run: a is still `failed`, b and c are still blocked, and d is done. `marestail queue --retry a` resumes a (see Resume), and then b and c follow the rules above.
4. **The same folder with `--here` on `marestail/green-the-repo`.** a, b, c and d all run on `marestail/green-the-repo`, in that order. c doesn't wait for a merge. If a fails, the queue stops at once, b, c and d get no status, and it exits 1.

### The green phases under `--here`

The green phases use one long-lived branch, `marestail/green-the-repo`, with every phase stacked on the one before and run to the hardener. Its PR is already open. Under the queue the caller does what the drivers did before the loop:

```
cp .marestail/phases/019-*.md ... .marestail/phases/025-*.md tasks/
git add tasks/ && git commit -m "human: queue tasks 019-025"
MARESTAIL_QUEUE_PUSH=1 MARESTAIL_QUEUE_PR=1 systemd-inhibit --what=idle --who=marestail --why="marestail green" \
  marestail queue --here --to hardener --agent claude --model claude-opus-5-5 tasks/019-*.md ... tasks/025-*.md
```

- `tasks check` passes: each phase depends on the one before, and 019 depends on `018-runs-stay-nice`, which the old driver copied into `tasks/`.
- 018 isn't in the set and has no events.jsonl. 021's legacy reader calls its run `complete` because its handoffs were archived, so 018 counts as done. `--here` needs no branch or head from it.
- The phases run one after another on `marestail/green-the-repo`.
- `--from specifier` on the first run replaces `FIRST_START_FROM`.
- The branch is pushed after every phase. `gh pr list` finds the open PR, so no second PR is created.
- 000–018 have no front matter, so `marestail queue` with no arguments on green's `tasks/` refuses to start. The green chain names its files.

## Starting a run

- Command: `[sys.executable, "-u", <marestail/cli.py of this checkout>, "run", <task path relative to the root>, "--auto", ...]`, the same way `bin/marestail` starts marestail (`exec python3 -u .../marestail/cli.py`). The flags follow in this order: `--from` (when given or resumed), `--to`, `--agent`, `--model`, `--effort`, `--scope`, then one `--focus` per path. Using its own checkout means the queue runs the same marestail it is, whatever `marestail` is on `PATH`. `--auto` is always passed, because nobody is there to approve the critic (`cli.py:65`, `runner.py:320`).
- The child's cwd is the repository root. It inherits the queue's stdout and stderr, so its output goes wherever the queue's does, as a `marestail run` in a terminal would. It is started with `start_new_session=True`, which lets the queue signal its whole process group, agent CLIs included.
- Environment: the queue's own, plus `MARESTAIL_LIMIT_WAITS=36` when that variable is unset. That keeps `overnight.sh`'s six hours of rate-limit waits (`tools/overnight.sh:5`) for unattended queues. The runner's own default stays 12 (`runner.py:55`). If the variable is set, its value is passed unchanged.
- When the child exits, the queue reads the task's latest run with 021's reader, for this repository and task id, and records the outcome:

| what the queue sees | status | reason |
|---|---|---|
| no record, or the record's `pid` is not the child's pid (the child exited before `run-start`, e.g. a bad `--model`/`--agent` pair, `runner.py:250`) | none: the queue stops and exits 2 | `marestail run exited <code> before it started the pipeline` |
| `result` `complete`, `exit_code` 0 | `done` | null |
| `result` `complete`, `exit_code` 3 (qa ran against a harness or nothing, `marestail/ran_against.py:12`) | `stopped` | `complete but NOT verified against the running app` |
| `result` `stopped` | `stopped` | `stopped at <stopped_at>: <reason>` |
| `result` `failed` | `failed` | the record's `reason` |
| `result` `interrupted`, or no `run-end`, when the queue didn't signal the child | `failed` | `marestail run ended without finishing (exit <code>) at <role>` |

Exit code 3 is a stop, as 005 made it for `overnight.sh`. After checking the app by hand, a person can accept the task by setting its status to `"done"` in state.json.

## State and log

`.marestail/queue/state.json` is rewritten after every task: the queue writes a temp file in the same folder, then `os.replace`s it.

```json
{"v": 1, "tasks": {"a": {"status": "done", "branch": "marestail/a", "head": "<40-hex sha>",
  "finished_at": "2026-09-28T17:02:11Z", "to": "hardener", "exit_code": 0, "reason": null}}}
```

- `status` is `done`, `failed`, `blocked` or `stopped`.
- `branch` and `head` are the branch the task ran on and `git rev-parse HEAD` after its run. Both are null for `blocked`.
- `finished_at` is UTC ISO-8601 with `Z`.
- `to` is the `--to` passed, or null.
- `exit_code` is the child's exit code, or null.
- `reason` is null for `done` and a one-line string otherwise.
- Tasks outside this invocation's set keep their entries unchanged.
- A state.json whose `v` isn't 1, or that isn't valid JSON, stops the queue before the first task with exit 2 and `.marestail/queue/state.json: <problem>`.

`.marestail/queue/queue.log` gets one line per event, and the queue prints the same line to stdout with a `queue: ` prefix. Each line is `<UTC time>Z <word> <task-id> <detail>`, where the word is one of: `start` (branch, where it came from, any `--from`), `skip` (done), `done` (short head, minutes), `failed`, `stopped`, `blocked`, `pushed`, `push-failed`, `pr-opened`, `pr-failed`, `interrupted`, or `end` (counts per status).

## Resume

- A rerun skips tasks whose status is `done`.
- `blocked` isn't sticky. It is worked out again on every run.
- `stopped` and `failed` are sticky. The task isn't started, and it holds back its dependants, until `--retry <id>` names it. `failed` is sticky as well as `stopped` so that a queue rerun by a timer, or after a reboot, doesn't spend quota repeating the same crash.
- A task with no status in state.json gets it from 021's reader:
  - `complete` → `done`
  - a run that ended `stopped` or `failed` → the same status, recorded
  - `dead`, or `result` `interrupted` → resume
  - `never-started`, `abandoned`, `leftover` or no run → start from the beginning
- Resume role: `--from <role>`, where `role` is the role field in 021's record for the task's latest run. For a dead or interrupted run that is the role that was running. For a stopped or failed one it is the role it ended at. A null role starts the run from the beginning.
- `--retry <id>` clears the task's sticky status and resumes it from that role. So `--retry a` after a stopped at the critic runs `marestail run tasks/a.md --auto --from critic ...`. After an exit-3 `stopped` it resumes from `qa`.
- An explicit `--from` wins over the resume role for the first task started, and only for that task.

## Push and PR

Two settings, each an env var that wins over a `marestail.toml` key:

| setting | key | env var | default |
|---|---|---|---|
| push each branch after each task | `[queue] push` | `MARESTAIL_QUEUE_PUSH` | false |
| open a PR per branch | `[queue] pr` | `MARESTAIL_QUEUE_PR` | false |

- Accepted values: the key must be a TOML bool. The env var takes `1` or `true` for on, and `0`, `false` or the empty string for off.
- Anything else exits 2 before the first task: `` [queue] push must be true or false, got 'yes' `` or `MARESTAIL_QUEUE_PUSH must be 1, 0, true or false, got 'yes'`.
- `pr` on with `push` off exits 2: `[queue] pr needs [queue] push`.
- Remote: the `<remote>` part of `[git] base` when `git remote` lists it, otherwise `origin`. If push is on and that remote doesn't exist, the queue exits 2: `[queue] push is on but this repository has no remote named origin`.
- PR on and `shutil.which("gh")` is None: exit 2 with `[queue] pr needs the gh CLI on PATH`.
- Push runs after every task that ran, whether it ended done, failed or stopped. It doesn't run for a blocked task, a skipped task or an interrupted one. The command is `git push --quiet -u <remote> <branch>`.
- A failed push logs `push-failed <id> <branch>: <last line>` and doesn't change the task's status or the queue's exit code. That matches the drivers, which never stopped on a push that failed after a phase (`run-queue-all-opus.sh:47`).
- PR, only after a push that followed a `done` task: `gh pr list --head <branch> --state open --json number --jq length`. When that prints `0`, the queue runs `gh pr create --base <base branch> --head <branch> --title <title> --body-file .marestail/queue/pr-<id>.md`, with `GH_PROMPT_DISABLED=1` in its environment.
  - `<base branch>` is `[git] base` without its remote part (`origin/main` → `main`).
  - `<title>` is the first `# ` line of the task body with the `# ` removed, or the task id if there is none.
  - No PR is opened for a failed or stopped task, because its branch isn't ready for review.
  - A gh failure logs `pr-failed <id> <branch>: <last line>` and changes nothing else.
- PR body, written by the queue:

```
`marestail queue` opened this pull request after task `<id>` finished on `<branch>`.

Tasks done on this branch:
- <id>: <title>

Each commit is stamped with the role and model that made it.
```

  The list holds every task in state.json with this branch and status `done`, in queue order. The body isn't edited again later. Commits pushed by later tasks on the same branch show up in the PR by themselves.

## Exit codes and signals

| code | when |
|---|---|
| 0 | every task in the set is done, whether run now or skipped as already done |
| 1 | the queue ran, and at least one task is failed, stopped or blocked, or it stopped early (dirty tree at a switch, or the first failure under `--here`) |
| 2 | it refused before the first task (every check above, a bad role, bad settings, bad state.json), or a child exited before `run-start` |
| 130 / 143 | interrupted by SIGINT / SIGTERM |

The queue handles SIGINT and SIGTERM.

- While a child runs, the queue sends SIGTERM to the child's process group with `os.killpg`. It waits up to 30 seconds, then sends SIGKILL to the group.
- The queue kills the child rather than leaving it running. Without the queue, nothing would push its branch, record its outcome or start its dependants. A rerun would also find it `running` and refuse to start.
- The interrupted task's status in state.json is left as it was. It isn't marked failed. queue.log gets `interrupted <id> by signal N; the next run resumes from <role>`, with the role from 021's reader after the child has gone. The next rerun resumes it as a dead or interrupted run.
- Between tasks, a signal stops the queue before it starts the next task.
- Either way the queue doesn't push, doesn't switch back to the starting branch, removes its lock, and exits 128 + the signal number.
- If the queue is killed with SIGKILL, the child keeps running in its own session. The next queue then sees a `running` run and refuses to start until it ends.

## What the drivers did, and where each behaviour goes

| driver behaviour | where it goes |
|---|---|
| `STOP_AT` defaulting to hardener (`overnight.sh:6`) | dropped as a default. `--to hardener` is the caller's choice. A queue that skipped qa by itself would hide the step that checks the running app. |
| the summary `.marestail/runs/overnight-<stamp>.md` (`overnight.sh:9,45-51`) | dropped. queue.log, state.json, `marestail status` and each run's `timeline.md` cover it. `tools/overnight.sh` itself stays. |
| `FIRST_START_FROM` / `START_FROM` for the first phase (`run-queue-all-opus.sh:37`) | kept as `--from`, first task only, and resumes also find the role automatically |
| the state file `.marestail/runs/all-phases.state` | replaced by `.marestail/queue/state.json`. The old file isn't read. |
| push after each phase, and on failure (`run-queue-all-opus.sh:43,47`) | kept, behind `[queue] push` |
| the initial `git push -u` that stops the driver when it fails (`:19`) | dropped. The first push happens after the first task, and a failed push is only logged. |
| opening the PR once, from `pr-body.md` (`:20-22`) | kept, behind `[queue] pr`, after the first done task. The body is generated. A hand-written body is left to the caller (`gh pr edit`). |
| the `human: queue task NNN` commit copying a phase into `tasks/` (`:33-35`) | left to the caller. The queue runs files where they are and needs them committed. It commits a task file only onto a branch made from base that lacks it (`queue: task <id>`). |
| stopping everything at the first failure (`:41-45`) | only under `--here`. Otherwise only dependants are blocked. |
| `systemd-inhibit --what=idle` (`:38`) | left to the caller: `systemd-inhibit ... marestail queue ...` |
| nvm and `PATH` setup (`:4-7`) | left to the caller: it is machine setup, not marestail's |
| unsetting `CLAUDECODE`, `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_ENTRYPOINT` (`:8`) | left to the caller. `marestail run` doesn't do it either (nothing in `marestail/` reads `CLAUDECODE`). If nested claude sessions need it, it belongs in the backends, not the queue. |
| `AGENT`, `MODEL`, `EFFORT`, `SCOPE`, `FOCUS` env passthrough (`overnight.sh:15-38`) | replaced by the flags |
| `MARESTAIL_LIMIT_WAITS=36` (`overnight.sh:5`) | kept, for children only, when unset |
| waiting on another driver's pid (`run-queue.sh:10`, `run-queue-016.sh:18`) | dropped. The lock and the running-run check refuse instead of waiting. |

## README

- Add a `## Queue` section after `## Overnight`. It covers:
  - the command line
  - the order and branch rules, with the a/b/c/d example in short
  - `--here`
  - resume and `--retry`
  - `[queue] push` and `[queue] pr`
  - the merge-commit note
  - state.json and queue.log
  - the exit codes
- Add rows to the environment table:
  - `` | `MARESTAIL_QUEUE_PUSH` | `1`/`true` or `0`/`false`: push each branch after each queued task; wins over `[queue] push` | ``
  - `` | `MARESTAIL_QUEUE_PR` | `1`/`true` or `0`/`false`: open a PR per queued branch; wins over `[queue] pr` | ``
- Update the `MARESTAIL_LIMIT_WAITS` row to say `marestail queue` sets 36 for its runs when it is unset.
- The docs gate fails a backticked path under `tasks/` that doesn't exist (`marestail/gates/docs.py:143`), so the README names example tasks without backticks, or inside a code block.
- Keep `## Overnight` as it is. `tests/test_timeline.py:492-495` reads it.

## Must not break

- `marestail run` on a task file with no front matter, and every flag it has.
- The existing per-run files under `.marestail/runs/<task>/`: the `NN-role.prompt.md` and `.json` step files, `timeline.json` and `timeline.md` (`marestail/timeline.py:11-12`). Also the handoff layout under `.marestail/handoffs/<task>/` and its archive on completion (`runner.py:1138`). Green writes no pipeline.log: nothing in `marestail/` names one.
- `tools/overnight.sh` and its test (`tests/test_timeline.py:498`).
- The queue driver scripts in `.marestail/phases/`. Never edit, move or delete anything under `.marestail/phases/`, because a live driver runs from there. The queue never reads `.marestail/runs/all-phases.state` or `driver-queue.log`.
- `marestail status`, `tasks check` and events.jsonl as 019–021 ship them. The queue only reads them.

## Tests

`tests/test_queue.py` and a parser test in `tests/test_cli.py`. Everything runs in-process. No real agent session and no real `marestail run` are started. Each test uses:

- `git_repo` (`tests/conftest.py:117`) with `[git] base = "origin/main"` in its `marestail.toml`, and a local bare repository (`git init --bare`) added as `origin`, with `main` pushed and fetched.
- The queue's child-start seam replaced by a fake. The fake records the argv and env it was given, makes one commit on the current branch, and appends 020-shaped `run-start` / `step-start` / `step-end` / `run-end` lines, carrying the fake's pid, to `.marestail/runs/<id>/events.jsonl`. It then returns a handle with that pid, `wait()` and `returncode`. Those events are read back through 021's real reader. Where a case needs a state the fake can't produce (`running`, `dead` with a live-or-dead pid), stub 021's reader instead.
- `gh` faked through the queue's command runner, with its calls recorded. Also add `"gh"` to `FORBIDDEN_BINARIES` in `tests/conftest.py:136`, so no test can reach the real gh.
- `os.killpg` stubbed wherever signals are tested.

Cases:

- Order: a, b, c, d as in the example. `002` with `depends = []` and `001` with `depends = ["002"]` run 002 then 001. A dependency outside the set that isn't complete blocks with the message above. One that is complete per 021 lets its dependant run.
- Example scenario 1: branches, parents (`git merge-base`), state.json exactly as described, exit 1, back on `main` at the end.
- Scenario 2: push a merge of `marestail/a` into the bare repo's `main`. The rerun starts one child (c), `marestail/c` starts from the new `origin/main`, and the queue exits 0.
- Scenario 3: a's fake ends `failed`. b and c are blocked with `a failed`, d runs, and the queue exits 1. A plain rerun starts no child. `--retry a` starts a with `--from` set to the fake's last role.
- Scenario 4 (`--here`): no branch is created and all four run on the current branch. A failure in a stops the queue with b, c and d unstarted.
- A squash-merged dependency stays blocked.
- A legacy record (`events` null) blocks with the stated message wherever the queue needs its branch or head; under `--here` it counts as done.
- The task file commit: a task file that exists only on the starting branch is committed on its new branch as `queue: task <id>`. Under `--here` nothing is committed.
- Refusals, each exit 2 with nothing started and no branch created:
  - a `tasks check` problem
  - a duplicate id
  - an ignored task file
  - a detached HEAD
  - an untracked file outside `.marestail/`
  - an uncommitted task file
  - a running run
  - a live lock
  - an unknown `--to` or `--from`
  - `--retry` on an unknown id, and `--retry` on a done task
  - each bad push or PR setting
  - pr without push
  - push with no remote
  - pr with no gh
  - an unreadable state.json or `"v": 2`
- These are not refusals: an untracked file under `.marestail/` passes the clean check, and a stale lock is replaced.
- Child argv: exactly `[sys.executable, "-u", <cli.py>, "run", "tasks/a.md", "--auto", "--from", "coder", "--to", "hardener", "--agent", "claude", "--model", "m", "--effort", "high", "--scope", "changed", "--focus", "x", "--focus", "y"]` for the first task. The second task has no `--from`.
- `MARESTAIL_LIMIT_WAITS`: unset gives the child `36`, and `5` stays `5`.
- Outcome table: one test per row, including exit 3 → `stopped`, and a child that exits before `run-start` → exit 2 with no state entry.
- Resume from 021's reader: dead at coder → `--from coder`. Ended stopped outside the queue → recorded `stopped` and not started. Abandoned → no `--from`.
- Push: after done, failed and stopped, the bare repo has the branch at the recorded head. After a blocked task it is not pushed. With push off, nothing is pushed. A failed push is logged and the exit code is unchanged.
- PR:
  - `pr list` answers `0`: `gh pr create` is called once, with the exact argv and a body file matching the template.
  - `pr list` answers `1`: no create.
  - A failed task: no `pr list` call.
- Env over key: `MARESTAIL_QUEUE_PUSH=0` beats `[queue] push = true`, and `MARESTAIL_QUEUE_PR=true` beats `[queue] pr = false`.
- Signals: SIGTERM during a child calls `killpg(pid, SIGTERM)`. When `wait` times out, `killpg(pid, SIGKILL)` follows. The task's state entry is unchanged, the log has the `interrupted` line, the lock is removed, nothing is pushed, and the exit is 143. SIGINT between tasks starts no further child and exits 130.
- state.json is replaced atomically, and entries for tasks outside the set survive a run.
- `marestail queue` with no PATH checks `tasks/`.

## Done when

`marestail queue` behaves as above and `tests/test_queue.py` covers every branch of `marestail/queue.py`. The existing tests still pass. `.importlinter` places `marestail.queue` and lets it import `marestail.pipeline`. The README has the Queue section and the two env rows. Nothing under `.marestail/phases/` has changed.

# QA: 022 — `marestail queue` runs a folder of tasks in dependency order

Run from the `marestail-green` checkout root with the python venv active. Test fixtures are built under `/tmp/qa-022`. The proof is the terminal output, the git branches and commits, `.marestail/queue/state.json`, `.marestail/queue/queue.log`, and pull requests created.

All child runs execute in-process or via fake runners in unit tests; at the CLI they invoke `marestail run --auto` with `start_new_session=True`.

## Procedure

1. `help ok`.
   - Run `bin/marestail --help`. Expected: exit 0, subcommands choice list contains `{gate,run,tasks,queue,install,sonar,watch,status,abandon,dash,perf,visual,route,remote,graph,depth}` with `queue` placed directly after `tasks`. Description line: `    queue       run a folder of tasks in dependency order`.
   - Run `bin/marestail queue --help`. Expected: exit 0, lists positional `[PATH ...]`, options `--to ROLE`, `--from ROLE`, `--here`, `--retry TASK-ID`, `--agent A`, `--model M`, `--effort E`, `--scope S`, `--focus PATH`.
   - `queue` is no longer an invalid choice.

2. `preflight ok`.
   In `/tmp/qa-022/preflight`, initialize a git repo on branch `main` with `[git] base = "origin/main"` in `marestail.toml` and a local bare origin.
   - Lock: write PID of the current shell into `.marestail/queue/lock`. Run `bin/marestail queue`: exits 2, stderr `another marestail queue (pid <pid>) is running in this repository\n`, stdout empty. Write dead PID 999999 to lock: next run removes stale lock and does not refuse.
   - Tasks check: write `tasks/bad.md` with no front matter. Run `bin/marestail queue`: exits 2, prints `tasks/bad.md: no front matter: line 1 must be +++`.
   - Duplicate ID: create `folder1/019-x.md` and `folder2/019-x.md`. Run `bin/marestail queue folder1/ folder2/`: exits 2, prints duplicate ID error.
   - Gitignored file: place `tasks/ignored.md` in `.gitignore`. Run `bin/marestail queue`: exits 2, stderr `tasks/ignored.md is ignored by git; the queue runs only committed task files\n`.
   - Detached HEAD: `git checkout --detach HEAD`. Run `bin/marestail queue`: exits 2, stderr `HEAD is detached; check out a branch first\n`. Switch back to `main`.
   - Dirty tree: create untracked `dirty.txt`. Run `bin/marestail queue`: exits 2, stderr `the working tree has uncommitted changes; commit or stash them first:\n  dirty.txt\n`. Untracked file inside `.marestail/` (e.g. `.marestail/notes.txt`) passes clean check.
   - Running run: start a background task `011-live` so 021 reader reports it running. Run `bin/marestail queue`: exits 2, stderr `a marestail run is running here (011-live, pid <pid>); the queue would change the tree under it\n`. Stop the background task.
   - Role check: run `bin/marestail queue --to nonexistent`: exits 2, stderr `unknown role nonexistent; choose from ...\n`.
   - Settings check: `MARESTAIL_QUEUE_PUSH=yes bin/marestail queue`: exits 2, stderr `MARESTAIL_QUEUE_PUSH must be 1, 0, true or false, got 'yes'\n`. Set `[queue] pr = true` with `[queue] push = false`: exits 2, stderr `[queue] pr needs [queue] push\n`.
   - Base check: set `[git] base = "origin/nonexistent"`: exits 2, stderr `[git] base origin/nonexistent does not exist; set it in marestail.toml\n`.
   - State file: write `.marestail/queue/state.json` with `{"v": 2}`: exits 2, stderr `.marestail/queue/state.json: unknown version 2\n`.

3. `branches ok` (Worked example scenarios 1 and 2).
   In `/tmp/qa-022/flow`, initialize a git repo with bare origin and branch `main`. Create committed tasks:
   - `tasks/a.md`: `depends = []`, title `# Task A`
   - `tasks/b.md`: `depends = ["a"]`, `stack = true`, title `# Task B`
   - `tasks/c.md`: `depends = ["a"]`, `stack = false`, title `# Task C`
   - `tasks/d.md`: `depends = []`, title `# Task D`
   - Run `bin/marestail queue`.
     - Expected: `a` runs on new branch `marestail/a` to head `A1`.
     - `b` switches to `marestail/a` on top of `A1` and runs to head `B1`.
     - `c` fetches `origin`, finds `A1` is not an ancestor of `origin/main`, logs `blocked c waiting for a (A1) to be merged into origin/main`.
     - `d` runs on new branch `marestail/d` from `origin/main` to head `D1`.
     - Queue switches back to `main`, exits 1.
     - `.marestail/queue/state.json` records `a` done on `marestail/a` at `A1`, `b` done on `marestail/a` at `B1`, `c` blocked, `d` done on `marestail/d` at `D1`.
     - `.marestail/queue/queue.log` records start, done, blocked, end lines.
   - In origin bare repo, merge `marestail/a` into `main` with a merge commit and push.
   - Rerun `bin/marestail queue`.
     - Expected: `a`, `b`, `d` are skipped as done.
     - `c` fetches origin, confirms `A1` is an ancestor of `origin/main`, switches to `marestail/c` created from `origin/main`, runs to head `C1`, ends done.
     - Queue switches back to `main`, exits 0.
     - `.marestail/queue/state.json` records all four tasks done.

4. `failure and retry ok` (Scenario 3).
   In `/tmp/qa-022/fail`, setup the same four tasks but configure `a`'s runner to fail during `coder`.
   - Run `bin/marestail queue`.
     - Expected: `a` recorded `failed` in state.json.
     - `b` and `c` recorded `blocked` with reason `a failed`.
     - `d` runs on `marestail/d` and ends `done`.
     - Exit code 1.
   - Rerunning `bin/marestail queue` starts no child runs; `a` stays failed, `b` and `c` blocked, `d` done.
   - Run `bin/marestail queue --retry 099-nope`. Expected: exits 2, stderr `--retry 099-nope: not a task in this queue\n`.
   - Run `bin/marestail queue --retry d`. Expected: exits 2, stderr `--retry d: it is done, not stopped or failed\n`.
   - Run `bin/marestail queue --retry a`. Expected: clears `a`'s sticky status, resumes `a` with `--from coder`. When `a` finishes, `b` and `c` follow the normal queue rules.

5. `here ok` (Scenario 4).
   In `/tmp/qa-022/here`, checkout branch `marestail/green-the-repo`.
   - Run `bin/marestail queue --here tasks/a.md tasks/b.md tasks/c.md tasks/d.md`.
     - Expected: all four tasks execute on `marestail/green-the-repo` without creating or switching branches.
     - `c` does not wait for `a` to be merged into base.
   - If `a` fails:
     - Queue stops immediately at the first failure.
     - Tasks `b`, `c`, and `d` receive no status in state.json.
     - Exit code 1.

6. `external and legacy ok`.
   - Task `tasks/e.md` depends on external task `021-done` with `stack = true`.
   - When `021-done` has an events-era complete run with branch `marestail/021-done` in 021 reader, `e` runs on `marestail/021-done`.
   - When `021-done` has not run or ended stopped, `e` is blocked with `021-done is not in this queue and has not completed`.
   - When `021-done` is complete via legacy run (null events), `e` is blocked with `021-done has no recorded branch; run it with --here or rerun it through the queue`.
   - Under `--here`, legacy complete `021-done` counts as done and `e` runs on the checked-out branch.

7. `task file commit ok`.
   - Task `tasks/a.md` exists on starting branch `main` but not on base `origin/main`.
   - When queue switches to `marestail/a` created from `origin/main`, it copies `tasks/a.md` from start time and commits with message `queue: task a`.
   - Under `--here`, no commit is made because the checked-out branch already has the task file.

8. `push and pr ok`.
   Run queue with `MARESTAIL_QUEUE_PUSH=1` and `MARESTAIL_QUEUE_PR=1`.
   - After each task finishes (done, failed, or stopped), `git push --quiet -u origin <branch>` is run.
   - After a task finishes `done`, `gh pr list --head <branch> --state open --json number --jq length` is run.
   - When `gh pr list` outputs `0`, `gh pr create` is run with `--base main --head <branch> --title <title> --body-file .marestail/queue/pr-<id>.md` and environment `GH_PROMPT_DISABLED=1`.
   - `.marestail/queue/pr-<id>.md` lists all done tasks on that branch in queue order.
   - When `gh pr list` outputs `1`, no PR is created.
   - For a failed or stopped task, no PR is opened.

9. `signals ok`.
   Start a queue running a long child task. Send SIGTERM to the queue process.
   - Expected: queue sends SIGTERM to the child's process group with `os.killpg`. If still running after 30 seconds, sends SIGKILL.
   - The interrupted task's state in `.marestail/queue/state.json` is preserved (not marked failed).
   - `.marestail/queue/queue.log` logs `interrupted <id> by signal 15; the next run resumes from <role>`.
   - `.marestail/queue/lock` is removed.
   - Queue exits 143.
   - Sending SIGINT between tasks stops before the next task and exits 130.

10. `dirty switch ok`.
    Without `--here`, create uncommitted file `dirty.txt` during task `a`'s run.
    - When `a` finishes, queue checks tree before switching branch for `b`.
    - Expected: queue stops before `b`, logs `stopped before b: the working tree has uncommitted changes`, leaves remaining tasks without status in state.json, and exits 1.
    - Leaves the repository on `marestail/a` with log message `left on marestail/a: the working tree has uncommitted changes`.

11. `existing ok`.
    - `bin/marestail run tasks/022-queue-runs-tasks-in-dependency-order.md` runs as before.
    - `bin/marestail tasks check tasks/` exits 0.
    - `bin/marestail status` and `bin/marestail abandon` function as before.
    - `bin/marestail gate --tier fast` passes.
    - `README.md` contains the `## Queue` section after `## Overnight` and environment rows for `MARESTAIL_QUEUE_PUSH` and `MARESTAIL_QUEUE_PR`.
    - `.importlinter` places `marestail.queue` in a layer below `cli` and allows it to import `pipeline`.

12. `pytest ok`.
    Run `.venv/bin/pytest tests/test_queue.py tests/test_cli.py -q --tb=line`.
    Expected: all pass. `tests/test_queue.py` covers every branch of `marestail/queue.py` with in-process mocks and stubs. `tests/test_cli.py` verifies parser options and subcommand ordering.

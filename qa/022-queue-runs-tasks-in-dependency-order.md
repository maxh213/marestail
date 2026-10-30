# QA: 022 — `marestail queue` runs a folder of tasks in dependency order

Run `bin/marestail` from the marestail-green checkout. Each step uses a new repo under `/tmp/qa-022/<step>`: `git init -q -b main`, `user.name` Test, `user.email` test@example.com, `commit.gpgsign` false, `marestail.toml` with `[git]\nbase = "origin/main"\n`, and a bare origin whose `main` is fetched, unless the step says otherwise. Commit every task file the step does not call dirty, ignored, or outside.

Checker problems are stdout. Every other refusal is stderr. The other stream is empty. Exit 2 writes no branch and leaves no lock.

Run steps do not start a live agent. The child is the fake from the task: one commit on the current branch, 020-shaped events for that pid, then the handle returns. Freeze the clock at `2026-10-17T12:00:00Z` and advance one minute per child. The commit the fake leaves is the 40-hex below; `git rev-parse --short` of it is the seven-character token in the log. `head` in state.json is the 40-hex, never the short form.

## What you see

`bin/marestail --help` (the gap is fifteen spaces, the same column as `tasks`):

```
    tasks               check task files
    queue               run a folder of tasks in dependency order
```

The choice list is `{gate,run,tasks,queue,install,sonar,watch,status,abandon,dash,perf,visual,route,remote,graph,depth}`.

Stdout for the branch run, and the same lines in `.marestail/queue/queue.log` without the `queue: ` prefix:

```
queue: 2026-10-17T12:00:00Z start a branch marestail/a from origin/main
queue: 2026-10-17T12:01:00Z done a 1111111 1
queue: 2026-10-17T12:01:00Z start b branch marestail/a from marestail/a
queue: 2026-10-17T12:02:00Z done b 2222222 1
queue: 2026-10-17T12:02:00Z blocked c waiting for a (1111111) to be merged into origin/main
queue: 2026-10-17T12:02:00Z start d branch marestail/d from origin/main
queue: 2026-10-17T12:03:00Z done d 4444444 1
queue: 2026-10-17T12:03:00Z end done 3 failed 0 stopped 0 blocked 1
```

`1111111` is `git rev-parse --short 111111111111111111111111111111111111111a`. The state heads are that 40-hex, `222222222222222222222222222222222222222b`, and `444444444444444444444444444444444444444d`.

## Procedure

1. Help, layer, README.
   - `bin/marestail --help` exits 0 and contains the choice list and the `queue` line in the fence. `bin/marestail queue --help` exits 0, lists `--to`, `--from`, `--here`, `--retry`, `--agent`, `--model`, `--effort`, `--scope` and `--focus`, and does not list `--retries`.
   - `tests/test_cli.py` `COMMAND_CHOICES`, `test_help_lists_remote_and_status_and_queue_stay_absent`, and `test_status_and_abandon_join_the_cli` no longer contain `invalid choice: 'queue'`. `tests/test_green_repo.py` `test_help_lists_every_subcommand` and `tests/test_events.py` `test_existing_commands_keep_text` list `queue` after `tasks`.
   - `.importlinter` has its own layers line `marestail.queue` directly under `marestail.cli`. Contract `pipeline-belongs-to-orchestration` is named `The role list and how a scope reshapes it are known only to the runner, the prompt builder and the queue`, and `marestail.queue` is an allowed importer.
   - `gh` is in `FORBIDDEN_BINARIES` in `tests/conftest.py`.
   - README: `## Queue` is the next heading after the Overnight section. The characters from `## Overnight` through the line before `## Queue` are unchanged (`tests/test_timeline.py` `test_readme_documents_timeline_artefacts`). The Queue section covers the command, a/b/c/d branches, `--here`, resume and `--retry`, push and pr, the merge-commit note, state.json and queue.log, and the exit codes. It does not backtick a `tasks/` path on any line. The environment table has:

```
| `MARESTAIL_QUEUE_PUSH` | `1`/`true` or `0`/`false`: push each branch after each queued task; wins over `[queue] push` |
| `MARESTAIL_QUEUE_PR` | `1`/`true` or `0`/`false`: open a PR per queued branch; wins over `[queue] pr` |
```

   The `MARESTAIL_LIMIT_WAITS` cell still says the default is 12 and also says `marestail queue` sets 36 for its runs when it is unset. `git diff -- tools/overnight.sh` is empty. Nothing under `.marestail/phases/` is added, edited, or deleted.

2. Refusals. One fresh repo per row. Only that fault. Exit 2, no child, no new branch, lock gone, state.json not rewritten. Stdout and stderr are the two cells and nothing else. The running-run row's D is the shim's decimal pid: `python3 -u shim/cli.py run tasks/011-live.md` with cwd the repo, and `events.jsonl` has that pid's `run-start` and no `run-end`.

   | fault | command | stdout | stderr |
   |---|---|---|---|
   | `tasks/bad.md` is `# bad` | `bin/marestail queue` | `tasks/bad.md: no front matter: line 1 must be +++` | empty |
   | committed valid `tasks/a/019-x.md` and `tasks/b/019-x.md` | `bin/marestail queue tasks/a tasks/b` | `tasks/b/019-x.md: duplicate task id 019-x: also tasks/a/019-x.md` | empty |
   | valid `tasks/ignored.md` in `.gitignore` | `bin/marestail queue` | empty | `tasks/ignored.md is ignored by git; the queue runs only committed task files` |
   | detached HEAD | `bin/marestail queue` | empty | `HEAD is detached; check out a branch first` |
   | the live shim above | `bin/marestail queue` | empty | `a marestail run is running here (011-live, pid D); the queue would change the tree under it` |
   | no task `099-nope` | `bin/marestail queue --retry 099-nope` | empty | `--retry 099-nope: not a task in this queue` |
   | `d` stored done | `bin/marestail queue --retry d` | empty | `--retry d: it is done, not stopped or failed` |
   | none | `bin/marestail queue --to nonexistent` | empty | `unknown role nonexistent; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa` |
   | none | `bin/marestail queue --from nonexistent` | empty | the same choose-from line with `nonexistent` |
   | none | `bin/marestail queue --scope hyper --to cleaner` | empty | `unknown role cleaner; choose from specifier, critic, coder, architect, blast, hardener, qa` |
   | `[queue] push = "yes"` | `bin/marestail queue` | empty | `[queue] push must be true or false, got 'yes'` |
   | `MARESTAIL_QUEUE_PUSH=yes` | `bin/marestail queue` | empty | `MARESTAIL_QUEUE_PUSH must be 1, 0, true or false, got 'yes'` |
   | `[queue] pr = true`, push false | `bin/marestail queue` | empty | `[queue] pr needs [queue] push` |
   | push true, no remote, base `main` | `bin/marestail queue` | empty | `[queue] push is on but this repository has no remote named origin` |
   | push true, pr true, `PATH` has no `gh` | `bin/marestail queue` | empty | `[queue] pr needs the gh CLI on PATH` |
   | base `origin/nonexistent` | `bin/marestail queue` | empty | `[git] base origin/nonexistent does not exist; set it in marestail.toml` |
   | state.json is `{"v": 2}` | `bin/marestail queue` | empty | `.marestail/queue/state.json: v must be 1, got 2` |
   | state.json is `{` | `bin/marestail queue` | empty | `.marestail/queue/state.json: not valid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)` |
   | valid `../else/a.md`, root `/tmp/qa-022/outside/repo` | `bin/marestail queue ../else/a.md` | empty | `../else/a.md is outside the repository; the queue runs only task files inside /tmp/qa-022/outside/repo` |

   `bin/marestail tasks check tasks/a tasks/b` on the duplicate repo exits 1 with that same stdout line.

3. Lock and clean check.
   - Live lock `12345\n` and `bin/marestail queue`: exit 2, stderr `another marestail queue (pid 12345) is running in this repository`, lock still `12345\n`.
   - That same live lock plus `--to nonexistent`: exit 2, stderr the `unknown role nonexistent; choose from` line, no `another marestail queue`, lock still `12345\n`.
   - Lock pid `999999` with no `/proc/999999`, and no `tasks/` directory: exit 2, stdout `tasks: no such file or folder`, stderr does not contain `another marestail queue`, lock absent.
   - Untracked `.marestail/notes.txt` and `[queue] push = "yes"`: exit 2, stderr `[queue] push must be true or false, got 'yes'`, and stderr does not contain `uncommitted changes`.
   - Committed `tasks/a.md` then edited: exit 2, stderr `the working tree has uncommitted changes; commit or stash them first:` then a line `tasks/a.md`.
   - Untracked `untracked.py`: the same sentence, then a line `untracked.py`.
   - Untracked `p01` through `p21`, in that status order: the sentence, then `p01` through `p20`, and not `p21`.

4. Branch run. Tasks a (`depends = []`), b (`depends = ["a"]`, `stack = true`), c (`depends = ["a"]`, `stack = false`), d (`depends = []`), all committed. state.json already has `z` done on `marestail/z` at `zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz`, `finished_at` `2026-10-16T00:00:00Z`, `to` null, `exit_code` 0, `reason` null. Run `bin/marestail queue` with the fake and the frozen clock.
   - Expected: the fence above. `marestail/a` has no upstream. `b` was switched onto `marestail/a`, not created from `origin/main`. No child for c. `git branch --show-current` is `main`. Exit 1.
   - a, b, d are done, `exit_code` 0, `to` null, `reason` null, `finished_at` `2026-10-17T12:01:00Z`, `2026-10-17T12:02:00Z`, `2026-10-17T12:03:00Z`, heads the three 40-hex values. c is blocked, `branch` null, `head` null, `exit_code` null, `finished_at` `2026-10-17T12:02:00Z`, reason `waiting for a (1111111) to be merged into origin/main`. `z` is unchanged. The write was `os.replace` of a temp file in `.marestail/queue/`.

5. Merge, restated. Start from step 4's commits and state: a and b done on `marestail/a` at `111111111111111111111111111111111111111a` and `222222222222222222222222222222222222222b`, c blocked, d done on `marestail/d` at `444444444444444444444444444444444444444d`. Merge `marestail/a` into the bare repo's `main` with a merge commit. `git merge-base --is-ancestor 111111111111111111111111111111111111111a origin/main` exits 0. Run `bin/marestail queue` again.
   - Expected: one child, c. Log lines `skip a done`, `skip b done`, `skip d done`. `marestail/c` is created from that `origin/main` (`git rev-parse marestail/c^` equals `origin/main` from before c's commit). c is done at `333333333333333333333333333333333333333c` (short `3333333`). Back on `main`. Exit 0. End line `end done 4 failed 0 stopped 0 blocked 0`.

6. Failure and retry. Add e (`depends = ["b"]`, `stack = true`). The fake for a returns result `failed`, reason `tests failed`, last role `coder`.
   - Expected: a failed with `tests failed`. b, c, and e blocked with `a failed` (e's reason is not `b failed`). d done on `marestail/d`. Exit 1.
   - Run `bin/marestail queue` again. Expected: no child, same statuses, exit 1.
   - Run `bin/marestail queue --retry a` with a's fake now returning complete exit 0. Expected: a's argv is `[sys.executable, "-u", <this checkout's marestail/cli.py>, "run", "tasks/a.md", "--auto", "--from", "coder"]`. Then b's argv is the same shape for `tasks/b.md` with no `--from`, on `marestail/a`. c stays blocked with `waiting for a (<short>) to be merged into origin/main`. d is skipped. Exit 1.
   - A separate repo where a's child returns result `stopped`, `stopped_at` `critic`, reason `not approved`: a is stopped with `stopped at critic: not approved`, b is blocked with `a stopped`, exit 1.

7. `--here`. On branch `marestail/green-the-repo`, origin's URL is not a repository. Four children return complete.
   - Expected: all four run on that branch, no `marestail/a`, no `fetch ` line, c is done, exit 0.
   - A second repo, same branch, a's child returns `failed`: a is failed, b, c, and d have no key, no further child, exit 1. A third repo with result `stopped`: the same, with a `stopped`.

8. Squash and outside dependencies. Each bullet is its own repo.
   - a done at `111111111111111111111111111111111111111a`, squash-merged so the ancestor test exits 1. `bin/marestail queue tasks/c.md`: c blocked with `waiting for a (1111111) to be merged into origin/main`, no child, exit 1.
   - e depends on `021-dep`, `stack = true`, reader complete with branch `marestail/021-dep` and that 40-hex head: e's child runs on `marestail/021-dep`.
   - Same e, reader has no run, or result `stopped`, or result `failed`: blocked `021-dep is not in this queue and has not completed`, not `021-dep failed`, exit 1.
   - Legacy complete, `events` null, `stack = true`: blocked `021-dep has no recorded branch; run it with --here or rerun it through the queue`. `stack = false` uses that same sentence.
   - That legacy record with `--here`: e runs on the checked-out branch.

9. Task file and an existing branch.
   - `tasks/a.md` is on `main` and absent from `origin/main`. After the switch the queue commits only that path with message `queue: task a`, then the child runs.
   - `marestail/a` already tips at a commit whose message is `keep me`. The queue switches to it and does not reset. The child's parent is `keep me`.
   - `--here` on a branch that already has `tasks/a.md`: no commit message is `queue: task a`.

10. Argv and limits. `bin/marestail queue --from coder --to hardener --agent claude --model m --effort high --scope changed --focus x --focus y` with a and d both `depends = []`.
    - a's argv is `[sys.executable, "-u", <cli.py>, "run", "tasks/a.md", "--auto", "--from", "coder", "--to", "hardener", "--agent", "claude", "--model", "m", "--effort", "high", "--scope", "changed", "--focus", "x", "--focus", "y"]`. cwd is the repo root. `start_new_session` is true. No `--retries`.
    - d's argv is that list with `tasks/d.md` and without `--from` / `coder`.
    - a's state `to` is `hardener`, `exit_code` is 0, `finished_at` is the frozen stamp.
    - `bin/marestail queue tasks/a.md` with no `--to`: the argv has no `--to`.
    - Unset `MARESTAIL_LIMIT_WAITS` gives the child `36`. A parent value `5` stays `5`.

11. Outcomes, the queue not having signalled the child. One repo each.
    - Child exits 2 with no `run-start`: exit 2, stderr `marestail run exited 2 before it started the pipeline`, no state key for a.
    - A record whose pid is not the child's pid: the same exit 2 and the same stderr.
    - complete, exit 0: `done`, reason null, queue exit 0.
    - complete, exit 3: `stopped`, reason `complete but NOT verified against the running app`, queue exit 1.
    - stopped at critic, reason `not approved`: `stopped`, reason `stopped at critic: not approved`, queue exit 1.
    - failed, reason `build error`: `failed`, reason `build error`, queue exit 1.
    - interrupted, or no `run-end`, exit 1, role coder: `failed`, reason `marestail run ended without finishing (exit 1) at coder`, queue exit 1.

12. Resume. One repo each. a has no queue status yet.
    - Reader says dead at coder: the argv contains `--from` `coder`.
    - Reader says result stopped at critic: state `stopped` with `stopped at critic: not approved`, no child.
    - Reader says result failed, reason `build error`: state `failed` with that reason, no child.
    - abandoned, leftover, or no run: the argv has no `--from`.
    - a has no run, b is dead at qa, neither depends on the other, command `--from specifier`: a gets `--from specifier`, b gets `--from qa`.
    - a is the exit-3 stopped row and the reader role is qa: `bin/marestail queue --retry a` passes `--from qa`.

13. Push and PR.
    - `MARESTAIL_QUEUE_PUSH=1`. After a child ends done, failed, or stopped, `git push --quiet -u origin <branch>` has run and the bare repo has that branch at the recorded head. A following blocked task is not pushed.
    - `[queue] push = true` and `MARESTAIL_QUEUE_PUSH=0`: origin has no `marestail/a`, and the task can still be done (exit 0).
    - Push on, origin's URL is not a repository, a's child complete: log `push-failed a marestail/a: and the repository exists.`, a stays done, exit 0.
    - Same bad origin, c depends on a with `stack = false`, a's child complete: log `fetch origin failed: and the repository exists.`, a done, c blocked, exit 1.
    - Push and pr on, body begins `# Task A`, `gh pr list --head marestail/a --state open --json number --jq length` prints `0`: one `gh pr create --base main --head marestail/a --title Task A --body-file .marestail/queue/pr-a.md` with `GH_PROMPT_DISABLED=1`. Log `pr-opened a marestail/a`. The body file is the fence in the feature (the three paragraphs, task `a`, title `Task A`).
    - b then finishes done on that same branch and `gh pr list` prints `1`: no second `gh pr create`, and the body file is unchanged.
    - `gh pr list` already prints `1` after the first done task: no `gh pr create`.
    - a's child returns failed: `git push` runs, `gh pr list` does not.
    - A body with no `# ` line: `--title` is the task id.
    - `[queue] pr = false`, `[queue] push = true`, `MARESTAIL_QUEUE_PR=true`, list prints `0`: `gh pr create` runs.
    - `[queue] push = true`, `[queue] pr = true`, `MARESTAIL_QUEUE_PUSH` empty: exit 2, stderr `[queue] pr needs [queue] push`.
    - `gh pr create` exits 1, last stderr line `auth required`: log `pr-failed a marestail/a: auth required`, a stays done, exit 0.

14. Signals.
    - Child still running, reader role coder. Send SIGTERM to the queue. Expected: exit 143. The child's process group has no live process (a child that ignores SIGTERM is SIGKILLed after 30 seconds and is still dead). a's state entry is unchanged and is not `marestail run ended without finishing`. The log line is `interrupted a by signal 15; the next run resumes from coder`. Nothing was pushed. HEAD was not switched back. The lock is gone.
    - a has been recorded done and b has not started. Send SIGINT. Expected: exit 130, no argv for b, nothing pushed, HEAD still on a's branch, lock gone.

15. Dirt after the child. a's fake returns done. Only after that process has exited, create `leak.txt`. Expected: no child for b, log `stopped before b: the working tree has uncommitted changes` and `left on marestail/a: the working tree has uncommitted changes`, b has no key, exit 1, HEAD stays `marestail/a`.

16. Defaults and the commands that already exist.
    - `tasks/bad.md` has no front matter and `other/bad.md` does too. `bin/marestail queue` with no path: stdout `tasks/bad.md: no front matter: line 1 must be +++`, no `other/bad.md`, exit 2.
    - `plain.md` is `# plain`. `bin/marestail run plain.md --to not-a-role`: exit 1, stderr `unknown role not-a-role; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa`, and neither stream contains `no front matter`.
    - `bin/marestail tasks check plain.md`: exit 1, stdout `plain.md: no front matter: line 1 must be +++`.
    - `bin/marestail status --help`: exit 0, and the text contains `--json`, `--all`, and `--queued`.

17. Run `.venv/bin/pytest tests/test_queue.py tests/test_cli.py tests/test_green_repo.py tests/test_events.py tests/test_timeline.py -q --tb=line`. Expected: all pass. `tests/test_queue.py` is in-process, uses the fake child and the real 021 reader, and covers every branch of `marestail/queue.py`.

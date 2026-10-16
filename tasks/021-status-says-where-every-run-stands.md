+++
depends = ["019-tasks-say-what-they-depend-on", "020-runs-write-events"]
stack = false
+++
# 021 — `marestail status` says where every run stands, and `marestail abandon` gives one up

After this task, `marestail status` prints one table of every marestail run under the current directory: running, dead (killed mid-step, usually by a shutdown), stopped for a human, never started, complete, abandoned, or a leftover copy of old handoffs. `marestail status --json` prints the same facts for a program to read, and `marestail abandon <repo>/<task>` gives up on a run without deleting anything. Today this lives outside marestail, in the marestail-runs skill's script. A scheduler that runs marestail on several machines needs it as a command with a fixed JSON shape, and 022's `marestail queue` decides whether a dependency is done through the same code.

`.marestail/phases/sources/marestail-runs.py` is that script (524 lines). It is untracked input: read it, port its behaviour into `marestail/status.py`, and never copy it into the repo whole. It knows only what a run leaves on disk: pipeline.log, handoffs and per-step files. This task puts 020's `events.jsonl` first and keeps the script's logic as the fallback for runs that have no events file.

## Commands

```
marestail status [--json] [--all] [--queued] [PATH ...]
marestail abandon TARGET [TARGET ...] [--reason TEXT]
```

- `--all` also shows `complete`, `abandoned` and `leftover` runs, which the table hides by default.
- `--queued` adds the task files that never ran.
- `--json` prints the machine-readable form described below. It always carries every run, whatever `--all` says, as the script's `--json` does.
- `status` always exits 0 once it has scanned. A PATH that is not a directory exits 2 with `marestail status: not a directory: <PATH>` before anything is printed.
- Add both subcommands to `build_parser` in `marestail/cli.py` straight after `watch` (line 92), each with a handler that imports `marestail.status` inside the function, as `run_command` imports the runner.

## Where it looks

Each PATH is either a repo or a folder to scan. A folder is a repo when it holds a `.marestail/` directory. The scan is the script's `discover`/`walk`, rule for rule:

- If PATH holds `.marestail/`, it is the only repo found under it. The scan does not look inside a repo for more.
- Otherwise it walks child folders, sorted by name, at most two levels below PATH (`MAX_DEPTH = 2`). A child that holds `.marestail/` is a repo and the walk stops there.
- It skips symlinked folders, any folder whose name starts with `.`, and these names: `.git`, `.marestail`, `node_modules`, `deps`, `_build`, `target`, `vendor`, `.next`, `dist`, `build`, `mutants`, `tmp`.
- A folder is visited once, by its resolved path, so two PATHs that overlap do not list a repo twice. A folder that cannot be read is skipped quietly.

With no PATH the default is the current directory. When the current directory or one of its parents holds `.marestail/`, the nearest such folder is the one PATH instead, so `marestail status` works from any folder inside a repo. The script's default was `~/workspace`, and `marestail watch` still reads `~/workspace` first (`default_watch_roots`, `marestail/cli.py:421`). `status` starts from where you are; `marestail status ~/workspace` gives the fleet view.

In one repo, the tasks are the names of the directories in `.marestail/handoffs/` and `.marestail/runs/`, except names starting with `handoffs-`. Loose files there, such as `overnight-*.log`, are not tasks. The runner keys both folders by the task file's stem (`Run.folder` and `Run.handoffs`, `marestail/runner.py:163-169`), so the task id is the file name without `.md`, as 019 defines it.

## State from events

When `.marestail/runs/<task>/events.jsonl` exists and holds at least one valid `run-start`, the state comes from it alone.

- A valid line is a JSON object with `"v": 1` and a string `event`. Any other line is skipped: a line cut short when the runner was killed, a blank line, or a later version. Event names status does not know are also skipped.
- The file is appended by every run of the task. The latest run is the last `run-start` and every valid line after it. Earlier runs are ignored.
- A file with no valid `run-start` counts as no file, and the task falls through to the legacy rules below.

The latest run's state:

| latest run | state |
|---|---|
| `run-end` with `result` `complete` | `complete` |
| `run-end` with `result` `stopped` | `stopped` |
| `run-end` with `result` `failed` | `stopped` (it still needs a human; `result` says `failed`) |
| `run-end` with `result` `interrupted` | `dead` |
| no `run-end`, and the run-start pid is this run | `running` |
| no `run-end`, and it is not | `dead` |

The pid in `run-start` is "this run" only when all of these hold:

1. `/proc/<pid>/cmdline` exists. Split it on NUL bytes into tokens.
2. Some token's basename is `cli.py` or `marestail`, and the next token is `run`. This is the script's `is_pipeline`. `bin/marestail` execs `python3 -u .../marestail/cli.py run ...`, so the runner's own pid carries these tokens.
3. Some token after that `run` has `Path(token).stem` equal to the task id.
4. `/proc/<pid>/cwd` resolves to the repo or a folder inside it.

A pid reused by an editor, a shell, or another task's run fails 2, 3 or 4, so a reused pid is never taken for the run. Every `/proc` read that raises `OSError` counts as "not this run".

An `abandoned` marker (below) turns `dead` or `stopped` into `abandoned` when the marker's mtime is at or after the latest `run-start`'s `at`. A marker older than the latest run-start belongs to an earlier run and is ignored. A `running` or `complete` run is never shown as abandoned.

## State without events (legacy)

Every other task is read exactly as the script reads it, from `collect` and `classify` down, with the same regexes, texts and order of checks:

- `pipeline.log` when it exists: `== role (NN-role) attempt N`, `finished in`, `verdict`, `rate limited; waiting ... retrying`, `pipeline complete`, `pipeline stopped at`, `stopping for a human`.
- Otherwise the per-step files `NN-role.prompt.md` and `NN-role.json` in `runs/<task>/`, the live handoffs in `handoffs/<task>/`, and the archived `runs/<task>/handoffs-*/` folders.
- `leftover`: every live handoff is tracked by git (`git ls-files -z -- .marestail/handoffs`), or every one is byte-identical to a file of the same name in an archive.
- `abandoned`: the `runs/<task>/abandoned` marker, or a `handoffs-abandoned-*` or `handoffs-aborted-*` archive, with no fresh live handoffs and a state other than `complete`.
- `running`: when a marestail run process is alive in the repo, the newest `dead` or `never-started` record in that repo becomes `running`. The processes used here are those that pass checks 1, 2 and 4 above and whose pid is not the pid of an events-era `running` run in the same repo. Without that exclusion, a live events-era run would also mark some old legacy task as running.

Green's own runner writes no pipeline.log: there is no `PipelineLog` in green's `marestail/runner.py`. It prints to stdout, and the runs in this repo's `.marestail/runs/` got their pipeline.log from main's runner (main's `marestail/runner.py:134`). The legacy rules stay for those runs and for every repo whose runs predate 020. Adding pipeline.log to green is not part of this task.

## Shutdowns

For a `dead` run, status runs `last -x -F shutdown` once per invocation, and only when at least one run is dead. It parses the dates as the script's `shutdowns()` does. A shutdown at or up to 3600 seconds after the run's last activity means the machine went down under it: the table adds `; machine shut down HH:MM` (local time), and the JSON `reason` becomes `machine shut down <UTC ISO-8601 with Z>`. A missing `last` or a failed parse means no shutdowns. The last activity is `since` below.

## The table

It is the script's output, line for line:

- A header line: `marestail runs: N known, N running, N shown`, followed by `, N complete/abandoned/leftover hidden (use --all)` when any are hidden. Then a blank line.
- Then the table, or `(nothing to show)` when there are no rows. The columns are `Repo | Task | State | Where it stands | Last activity | Branch`, drawn by the script's `table()`.
- Rows are sorted by state in the order `running`, `dead`, `stopped`, `never-started`, `complete`, `abandoned`, `leftover`, then newest activity first.
- `Repo` is the repo's path relative to the PATH it was found under, or the repo folder's own name when it is the PATH.
- `Last activity` is `%m-%d %H:%M (Nm ago | Nh ago | Nd ago)` in local time, or `?` when unknown.
- `Branch` is the JSON `branch`, or `(detached)` when that is null.
- With `--queued` and at least one queued task, a blank line, `Tasks in tasks/ that never ran:`, a blank line, and a `Repo | Task` table follow.

The only wording change is the state `never started`, which is written `never-started` in the table too, so the table and the JSON share one set of names.

`Where it stands` for legacy runs is the script's `detail`, unchanged. For events-era runs it is:

| state | text |
|---|---|
| `running` | `in <step>`, or `starting` before the first `step-start`; when the latest event is `wait`, `waiting for a rate limit until <until>` |
| `dead`, mid-step | `mid <step>`, where the latest `step-start` has no `step-end` |
| `dead`, between steps | `after <step> (<outcome>)`, from the latest `step-end` |
| `dead`, no steps | `before the first step` |
| `dead`, `interrupted` | `interrupted` + ` in <step>` when there is a step |
| `stopped` | `at <stopped_at>` + `: <reason>` when there is a reason |
| `stopped`, `failed` | `failed at <stopped_at>` + `: <reason>` when there is a reason |
| `complete` | `<N> steps`, where N is the number of `step-end` lines in the run |
| `abandoned` | the marker's first line |

A shutdown note is added to either kind of `dead` text as described above.

## JSON

`{"v": 1, "runs": [...], "queued": [...]}`, printed with `json.dumps(..., indent=2)`. `runs` is sorted by `repo`, then `task`. `queued` is `[]` without `--queued`.

Each run record has exactly these keys, in this order. "Events" means the state came from `events.jsonl`, and "legacy" means it came from the fallback.

| key | events | legacy |
|---|---|---|
| `repo` | the repo's resolved absolute path | same |
| `task` | task id | same |
| `state` | from the table above | from the script's rules |
| `step` | `step` of the latest `step-start` or `step-end` in the run; null when there is none | the script's `last_step` (for `running`, its `current_step` when set); null when empty |
| `role` | `role` of that same event; null when there is none | the text after the first `-` in `step`; null when `step` is null |
| `since` | `at` of the run's latest valid line, copied as written | the script's `last_activity` as UTC `YYYY-MM-DDTHH:MM:SSZ`; null when 0 |
| `branch` | `branch` from `run-start` | `git branch --show-current` in the repo; null when empty or git fails |
| `head` | `head` of `run-end`, else of the latest `step-end`, else `start_commit` of `run-start` | null |
| `pid` | the latest `run-start` pid, whatever the state (022 matches its child by it after the child exits) | when `running`, the lowest pid among the repo's unattributed run processes; else null |
| `result` | `result` of `run-end`; null when there is none | `complete` or `stopped` when pipeline.log ends that way; else null |
| `exit_code` | `exit_code` of `run-end`; null when there is none | null |
| `stopped_at` | `stopped_at` of `run-end`; null when there is none | null |
| `reason` | `reason` of `run-end` when present; for `dead`, the shutdown text or null; for `abandoned`, the marker's `--reason` text or null | `stopped`: `worker repeated the same problems 3x` or `runner asked for a human`; `dead`: the shutdown text or null; `abandoned`: the marker's `--reason` text or null; else null |
| `events` | absolute path of `events.jsonl` | null |
| `log` | absolute path of `runs/<task>/pipeline.log` when it exists, else null | same |

For `abandoned`, `since` is the marker's mtime as UTC `YYYY-MM-DDTHH:MM:SSZ`, in both eras.

A `queued` entry is `{"repo": <absolute path>, "task": <task id>, "path": <absolute path of the .md file>}`.

Branch for legacy runs is the repo's current checkout, not the run's branch. A caller that needs the run's own branch, as 022 does, must use a record whose `events` is not null.

## Queued tasks

`--queued` is the script's rule. A queued task is a `tasks/*.md` file whose stem has no task folder in any worktree of the same repo. `README.md` (in any case) and names matching the script's `ROLE_FILE_RE` (`NN-<role>.md`) are not tasks. Worktrees are the same repo when `repo_identity` matches: `git config --get remote.origin.url`, else `git rev-parse --git-common-dir`, else the path. The first worktree scanned that holds the file is the one reported. So a task queued in two worktrees of one clone is listed once, and one that ran in either is not listed.

## `marestail abandon`

It ports the script's `--abandon`:

- `TARGET` is `REPO/TASK`, split on the last `/`. `REPO` is a path, absolute or relative to the current directory, that must hold `.marestail/`. A bare `TASK` with no `/` means the repo found as with `status` and no PATH. When no such repo exists: `<TARGET>: no .marestail folder at <path>`.
- It refuses when the task reads as `running`, or when a run process in the repo is not attributed to an events-era run: `<TARGET>: a marestail pipeline is running in <repo>, not touching it`. The second rule is the script's own: a legacy run cannot be tied to a task, so any live pipeline in the repo blocks it.
- When neither `runs/<task>/` nor `handoffs/<task>/` exists: `<TARGET>: no run or handoffs folder for that task`.
- Otherwise it creates `runs/<task>/` if needed. When `handoffs/<task>/` holds any `.md` file, it renames that folder to `runs/<task>/handoffs-abandoned-<stamp>/`, where the stamp is `time.strftime("%Y%m%dT%H%M%S")`. It then writes `runs/<task>/abandoned` with the script's two lines, word for word: `abandoned YYYY-MM-DD HH:MM` + `: <reason>` when a reason is given, then `Move the handoffs-abandoned-* folder back to .marestail/handoffs/<task>/ and delete this file to revive it.` It prints `<TARGET>: abandoned` + `, N handoffs moved to <path relative to the repo>` when it moved any.
- Errors go to stderr. Each target is handled on its own. The exit code is 1 when any target failed, else 0.
- It deletes nothing, runs no git command that writes, and appends nothing to `events.jsonl`. Events are the runner's record alone (020).

## Module

- `marestail/status.py` holds all of it. It uses `marestail.shell.run` for `git` and `last`, so tests stub them with the `fake_run` fixture. It reads `/proc` through one function that lists the machine's processes as `(pid, tokens, cwd)`, called once per invocation, and through no other path. It does not use `ps`.
- Public surface, for the CLI and for 022: `read_run(repo: Path, task: str)` returns one run's record (the same record the table and JSON are built from). `scan(paths: list[Path])` returns the records and the queued tasks. `abandon(targets: list[str], reason: str) -> int` does the above. 022 decides "done" by calling `read_run` and checking `state == "complete"`. For a record whose `events` is null, 022 treats `branch` and `head` as unknown. Name the record type as you see fit, but keep these three functions.
- Name the events file once. If 020 exposes the name `events.jsonl` from a module at or below status's layer, import it from there. Otherwise define it in `status.py`.
- `.importlinter`: add `marestail.status` to the layer that holds `marestail.timeline : marestail.ran_against`, and to the `source_modules` of `foundations-import-nothing-above`. It imports nothing from marestail but `marestail.shell`.

### Why not share code with `marestail watch`

`marestail/tui/collect.py` overlaps in places: its own `discover` (line 124), a `ps -eo pid,ppid,etimes,args` parser (lines 773-799), `is_pipeline` (line 806, `cli.py` only), `cwd_of` (line 1128) and git helpers. status does not import it, and watch does not move onto status in this task:

- `marestail.tui` is the top layer in `.importlinter` (line 8), so nothing below the CLI may import it.
- `marestail/tui/*` is excluded from coverage (`pyproject.toml:14`), mypy (`pyproject.toml:24`), ruff (`pyproject.toml:29`) and Sonar (`sonar-project.properties:6`). Code moved out of it into a gated module must meet every gate, and that is a rewrite of the TUI's collector.
- watch answers a different question: what one bed is doing now, read from `.marestail/runs/overnight-*.log` (`latest_log`, `collect.py:327-338`) and agent transcripts. It does not ask which runs are unfinished.

Moving watch onto `status.read_run` is a later task. This one leaves `marestail/tui/` untouched.

## README

- Add a `## Status` section after `## Watch`. It gives the two command lines, the seven states with a one-line meaning each, the scan rule (a repo, or two levels deep, with the default being the current directory or the repo around it), that `--json` carries `"v": 1` and the keys above, and what `abandon` moves and writes and how to undo it.
- No new environment variables.

## Must not break

- `marestail run` on a task file with no front matter; its stdout; the existing `pipeline.log`, `timeline.json` and handoff layout. status only reads, and abandon touches only the folders named above.
- `marestail watch`, byte for byte: no file under `marestail/tui/` changes.
- The queue driver scripts in `.marestail/phases/`: never edit, move or delete anything under `.marestail/phases/`, because a live driver runs from there. `sources/marestail-runs.py` is read, not moved.
- Every existing CLI test. Update `test_help_lists_commands_in_order` so `status,abandon` follows `watch` in the command list.

## Tests

`tests/test_status.py` and additions to `tests/test_cli.py`. They are in-process and hermetic. Run folders are fixtures built under `tmp_path`, and no real process is started or signalled. Stub the process list function with `monkeypatch` so each test says which pids exist, with what tokens and cwd. Stub `last` through `fake_run`, or by patching the shutdown reader. Use `git` only in tmp repos, as `conftest.git_repo` does, or stub it. Patch `time.time` and `time.strftime` where a test asserts ages or stamps. Use a helper that writes event lines, so each test states only the events it needs.

- Events:
  - `running`, with `step`, `role`, `pid` and `since` from the latest `step-start`, and the text `in 03-coder`.
  - The same file is `dead` when the pid is missing, when its cmdline is `sleep 100`, when its cmdline is a `run` of another task, and when its cwd is another repo.
  - `dead` texts: mid-step, between steps, before the first step. A shutdown 10 minutes after `since` adds the note and `reason`; one 2 hours after does not.
  - `complete` with `head` from `run-end` and `pid` null. `stopped` with `reason`. `failed` reads as `stopped` with `result` `failed`. `interrupted` reads as `dead`.
  - Two runs in one file: a complete first run and a dead second run give `dead`, and the reverse gives `complete`.
  - A torn last line, a `"v": 2` line, a blank line and an unknown event are all skipped.
  - A `wait` as the latest event gives the rate-limit text.
  - A file with no `run-start` falls through to legacy, with `events` null.
  - An abandoned marker newer than `run-start` gives `abandoned`; one older does not; one beside a `complete` run gives `complete`.
- Legacy: one fixture per `classify` branch of the script, asserting state and text:
  - log complete, with and without archives; stopped, by a human repeat and by the runner; rate-limit wait; mid step, with and without its handoff written; perf sampling; after a step with a verdict.
  - Archive only gives `complete`. Leftovers: tracked by git, identical to an archive, and with no run folder.
  - `never-started`, and each of the five per-step-file cases.
  - `abandoned` by marker and by an aborted archive.
  - A live process in the repo makes the newest dead record `running`. A live process that is an events-era run's pid does not.
- Scan: a PATH that is a repo; repos one and two levels down are found, and three levels down are not; skipped names, dot folders and symlinks; overlapping PATHs give each repo once; no PATH inside a repo subfolder finds that repo; a PATH that is not a directory exits 2 with the message.
- Output:
  - The exact table text for a fixture of mixed states, including the header counts and hidden count.
  - `(nothing to show)`.
  - `--all`.
  - `--queued` across two worktrees of one clone (a task that ran in one is not queued in the other; `README.md` and `03-coder.md` are never queued).
  - `--json` with every key for one record of each state, including nulls; `queued` is `[]` without the flag; `--json` includes hidden states without `--all`.
- Abandon:
  - It moves handoffs to `handoffs-abandoned-<stamp>`, writes the exact marker text with and without a reason, and prints the message. `status` then shows `abandoned`.
  - It refuses a running events-era task and a repo with an unattributed live process.
  - It gives each error message.
  - A bare `TASK` works from inside the repo.
  - Several targets where one fails exit 1, and the others are still done.
- CLI: `status` and `abandon` parse their options and call the module. The help test lists them after `watch`.

## Done when

`marestail status`, `--json`, `--all` and `--queued` and `marestail abandon` behave as above. `tests/test_status.py` covers every branch of `marestail/status.py`. The existing tests pass. `.importlinter` lists `marestail.status`. The README has the `## Status` section. Nothing under `marestail/tui/` or `.marestail/phases/` has changed.

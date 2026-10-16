# QA: 021 — status says where every run stands, and abandon gives one up

Run from the marestail-green checkout with the venv active. Build the fixture under `/tmp/qa-021`, run the real CLI as child processes with `TZ=UTC` and every `MARESTAIL_*` variable dropped, and compare. The proof is the terminal text, the JSON, and the files `abandon` writes. The exact table below is the layout; the in-process tests pin the `(Nm ago)` ages with a patched clock, so at the UI assert the `%m-%d %H:%M` part exactly and the age as `(\d+m ago|\d+h ago|\d+d ago)`.

The fixture fails to produce the expected text until `marestail/status.py`, the two CLI subcommands, the `.importlinter` entries and the README section exist. That is the expected result of this procedure after the coder lands.

## The fleet fixture

Two repos under `/tmp/qa-021/fleet`. Each repo is `git init -b main` with the listed files committed (so `git ls-files` sees them) except the paths marked untracked.

`alpha/` holds `.marestail/` with:

- `runs/011-live/events.jsonl` — four lines, written after the shim below is started so `pid` is its real pid:
  ```
  {"v": 1, "at": "2026-10-16T11:30:00.000Z", "event": "run-start", "pid": <shim pid>, "task": "011-live", "task_path": "tasks/011-live.md", "branch": "main", "base": "origin/main", "start_commit": "aaa111", "from": "specifier", "to": "qa", "scope": "all", "agent": "claude", "model": "m", "effort": null}
  {"v": 1, "at": "2026-10-16T11:31:00.000Z", "event": "step-start", "pid": <shim pid>, "step": "01-specifier", "role": "specifier", "kind": "worker", "attempt": 1}
  {"v": 1, "at": "2026-10-16T11:40:00.000Z", "event": "step-end", "pid": <shim pid>, "step": "01-specifier", "role": "specifier", "attempt": 1, "outcome": "done", "bounce_to": null, "head": "bbb222", "handoff": ".marestail/handoffs/011-live/01-specifier.md", "seconds": 600.0}
  {"v": 1, "at": "2026-10-16T11:58:00.000Z", "event": "step-start", "pid": <shim pid>, "step": "03-coder", "role": "coder", "kind": "worker", "attempt": 1}
  ```
  The shim: a file `shim/cli.py` containing `import time; time.sleep(600)`, started as `python3 -u shim/cli.py run tasks/011-live.md --auto` with cwd `alpha`, left running.
- `runs/010-killed/events.jsonl` — a run-start at `2026-10-16T11:00:00.000Z` with pid 999999, a `01-specifier` step-start/step-end `done`, and a `02-critic` step-start at `2026-10-16T11:10:00.000Z` with nothing after it.
- `runs/009-paused/events.jsonl` — run-start pid 5555, `01-critic` step-start and step-end `pass`, then run-end at `2026-10-16T10:20:00.000Z` with result `stopped`, exit_code 1, head `ccc333`, stopped_at `critic`, reason `not approved`, archive null.
- `runs/008-fresh/` — an empty folder, mtime set with `touch -d '2026-10-16 10:50 UTC'`.
- `runs/007-done/events.jsonl` — run-start pid 6666, two step-start/step-end pairs, run-end at `2026-10-16T10:00:00.000Z` with result `complete`, exit_code 0, head `ddd444`, stopped_at null, reason null, archive `.marestail/runs/007-done/handoffs-20261016T100000`.
- `runs/006-given-up/events.jsonl` — run-start at `2026-10-16T08:00:00.000Z` pid 7777, then run-end result `stopped`, stopped_at `coder`, reason `coder used all 1 attempts`; plus `runs/006-given-up/abandoned` whose bytes are `abandoned 2026-10-16 09:00: superseded by 007-done\nMove the handoffs-abandoned-* folder back to .marestail/handoffs/<task>/ and delete this file to revive it.\n`, mtime set to `2026-10-16 09:00 UTC`.
- `handoffs/005-restored/01-specifier.md` — any text, committed and tracked, with no `runs/005-restored/`.

`beta/` holds `.marestail/runs/004-old/pipeline.log`, mtime `2026-10-15 08:00 UTC`, containing exactly:
```
== specifier (01-specifier) attempt 1
   01-specifier finished in 2.0 min: ok
== coder (02-coder) attempt 1
```

## Procedure

1. `help ok`. `bin/marestail --help` exits 0 and contains `{gate,run,tasks,install,sonar,watch,status,abandon,dash,perf,visual,route,remote,graph,depth}`. `status --help` and `abandon --help` each exit 0; the status one mentions `--json`, `--all`, `--queued` and `PATH`; the abandon one mentions `TARGET` and `--reason`. `bin/marestail queue` exits 2 with `invalid choice: 'queue'` on stderr.

2. `table ok`. `bin/marestail status /tmp/qa-021/fleet` exits 0, stderr empty, and stdout is the header `marestail runs: 8 known, 1 running, 5 shown, 3 complete/abandoned/leftover hidden (use --all)`, a blank line, and this table, with only the age parentheticals free to differ as described at the top:

   ```
   | Repo  | Task       | State         | Where it stands               | Last activity         | Branch |
   |-------|------------|---------------|-------------------------------|-----------------------|--------|
   | alpha | 011-live   | running       | in 03-coder                   | 10-16 11:58 (2m ago)  | main   |
   | alpha | 010-killed | dead          | mid 02-critic                 | 10-16 11:10 (50m ago) | main   |
   | beta  | 004-old    | dead          | mid 02-coder                  | 10-15 08:00 (28h ago) | main   |
   | alpha | 009-paused | stopped       | at critic: not approved       | 10-16 10:20 (1h ago)  | main   |
   | alpha | 008-fresh  | never-started | no handoffs, no steps, no log | 10-16 10:50 (1h ago)  | main   |
   ```

   No row contains `; machine shut down` (no real shutdown sits within an hour of those fixture times). `bin/marestail status /tmp/qa-021/fleet/no-such` exits 2, stdout empty, stderr exactly `marestail status: not a directory: /tmp/qa-021/fleet/no-such\n`. In an empty folder, `bin/marestail status .` prints `marestail runs: 0 known, 0 running, 0 shown`, a blank line and `(nothing to show)`, exit 0.

3. `all ok`. `--all` prints the header `marestail runs: 8 known, 1 running, 8 shown` with no hidden note, then this table. At the UI only the age parentheticals may differ, as at the top. This sample is the 12:00 UTC rendering: last activity is width 21, its separator is `|-----------------------|`, and `009-paused` is `10-16 10:20 (1h ago)`.

   ```
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
   ```

4. `json ok`. `--json` exits 0 and stdout parses as one object with keys `v`, `runs`, `queued`; `v` is 1 and `queued` is `[]` without the flag. `runs` has all 8 records, sorted by repo then task, and each has exactly the keys `repo`, `task`, `state`, `step`, `role`, `since`, `branch`, `head`, `pid`, `result`, `exit_code`, `stopped_at`, `reason`, `events`, `log` in that order. The `011-live` record: `repo` the absolute alpha path, `state` `running`, `step` `03-coder`, `role` `coder`, `since` `2026-10-16T11:58:00.000Z`, `branch` `main`, `head` `bbb222`, `pid` the shim's pid, `result`/`exit_code`/`stopped_at`/`reason` null, `events` the absolute events.jsonl path, `log` null. `009-paused`: `result` `stopped`, `exit_code` 1, `stopped_at` `critic`, `reason` `not approved`, `pid` 5555, `head` `ccc333`. `007-done`: `head` `ddd444`, `result` `complete`, `exit_code` 0, `pid` 6666. `004-old`: `step` `02-coder`, `role` `coder`, `since` `2026-10-15T08:00:00Z`, `head` null, `pid` null, `result` null, `events` null, `log` the absolute pipeline.log path. `008-fresh`: `step` null, `role` null, `since` `2026-10-16T10:50:00Z`. `005-restored`: `state` `leftover`, `since` null. `006-given-up`: `since` `2026-10-16T09:00:00Z`, `reason` `superseded by 007-done`. `bin/marestail status --json --queued /tmp/qa-021/fleet` still has `queued` `[]`: the fleet has no `tasks/*.md`. The non-empty queued entry is step 7 only.

5. `pid ok`. Write a fifth events run for a new task `012-dead` whose run-start pid is, in turn: a `sleep 100` process's pid; the pid of a shim started as `... cli.py run tasks/other.md` in alpha; the pid of a shim started for `012-dead` but with cwd `/tmp`; and a pid no process holds. Every time, `status` shows `012-dead` `dead`. While that `other.md` shim is up in alpha, legacy `008-fresh` reads `running` with text `starting`, `010-killed` stays `dead` with `mid 02-critic`, and `011-live` stays `running` with `in 03-coder`, even though both of those are newer than `008-fresh`. Stop the `other.md` shim. Separately, in a scratch repo with no events files: task `020-halted` has a log ending `pipeline stopped at 04-coder` and no `stopping for a human` line, and task `019-cut` has a log `== coder (03-coder) attempt 1` with no finished line and an older mtime. With two live `cli.py run tasks/other.md` processes in that repo, `019-cut` reads `running` with text `in 03-coder` (not `mid 03-coder`) and JSON `pid` the lower of the two pids, and `020-halted` stays `stopped` with `at 04-coder, runner asked for a human`. While the 011-live shim is the only live process, `008-fresh` stays `never-started`. After that shim is killed, `011-live` reads `dead` with `mid 03-coder`.

6. `scan ok`. Under `/tmp/qa-021/deep`: `a/` is a repo, `b/c/` is a repo, `b/c/d/` is a repo, `node_modules/x/` and `.hidden/y/` and `tmp/z/` each hold a `.marestail`, and `link` is a symlink to `a`. `bin/marestail status /tmp/qa-021/deep` lists `a` and `b/c` and nothing else; running it twice with overlapping paths (`deep deep/b`) still lists `b/c` once. `cd /tmp/qa-021/deep/a/sub` (a plain subfolder) and `bin/marestail status` with no PATH scans `a` and shows Repo `a`. `bin/marestail status /tmp/qa-021/deep/a` shows Repo `a`.

7. `queued ok`. `git clone` a scratch repo twice as `w1` and `w2` (or `git worktree add`), so both share one identity. Put `tasks/012-next.md` in both, `tasks/013-done.md` in `w1`, plus `tasks/README.md` and `tasks/03-coder.md` in `w1`; give `w2` a run folder `.marestail/runs/013-done/`. `bin/marestail status --queued` over a folder holding both lists `Tasks in tasks/ that never ran:` and a `Repo | Task` table whose only data row is `012-next` under the first-scanned worktree. `--json --queued` gives one queued entry whose `path` is that worktree's absolute `tasks/012-next.md`. Without `--queued`, `queued` is `[]` and no queued section prints.

8. `abandon ok`. In a scratch repo with a stopped events-era task `009-paused` whose `handoffs/009-paused/` holds two `.md` files: `bin/marestail abandon repo/009-paused --reason superseded` exits 0 and prints `repo/009-paused: abandoned, 2 handoffs moved to .marestail/runs/009-paused/handoffs-abandoned-<stamp>` with `<stamp>` matching `\d{8}T\d{6}`. The handoffs folder is gone, the archive holds the two files, and `runs/009-paused/abandoned` is exactly `abandoned <YYYY-MM-DD HH:MM>: superseded\n` then `Move the handoffs-abandoned-* folder back to .marestail/handoffs/<task>/ and delete this file to revive it.\n`. `events.jsonl` is byte-identical to before. `status` now shows the task `abandoned` with the marker's first line as its text. Undoing it — moving the folder back to `.marestail/handoffs/009-paused/` and deleting the marker — restores the `stopped` row. A second abandon with no reason and no `.md` in the handoffs folder prints `repo/009-paused: abandoned` and the marker's first line has no `: ...` suffix. From inside the repo, `bin/marestail abandon 008-fresh` (a bare task id) writes the marker and prints `008-fresh: abandoned`.

9. `refuse ok`. While the 011-live shim runs and no other pipeline is alive, `bin/marestail abandon alpha/011-live` exits 1, stdout empty, stderr `alpha/011-live: a marestail pipeline is running in <alpha absolute>, not touching it\n`, and no marker is written. Stop any extra shim, then start one whose task token matches no events-era run in the repo: abandoning `010-killed` is refused with `alpha/010-killed: a marestail pipeline is running in <alpha absolute>, not touching it\n` and nothing is written. Stop that unattributed shim before the next command. `bin/marestail abandon alpha/099-nope` exits 1 with stderr `alpha/099-nope: no run or handoffs folder for that task\n`. `bin/marestail abandon nowhere/009-paused` where `nowhere` holds no `.marestail` exits 1 with stderr `nowhere/009-paused: no .marestail folder at nowhere\n`. `mkdir -p /tmp/qa-021/bare` so that neither it nor `/tmp/qa-021`, `/tmp` or `/` holds `.marestail`, `cd` there, and `bin/marestail abandon 099-nope` exits 1, stdout empty, stderr exactly `099-nope: no .marestail folder at /tmp/qa-021/bare\n`. Back at the fleet, with only the attributed 011-live shim alive, `bin/marestail abandon alpha/099-nope alpha/009-paused` prints `alpha/099-nope: no run or handoffs folder for that task\n` on stderr, prints `alpha/009-paused: abandoned` on stdout, writes `runs/009-paused/abandoned`, and exits 1. The attributed shim does not block `009-paused`.

10. `shutdown ok`. With the fixture's dead rows far from any real shutdown, no `machine shut down` note appears. Run the status child with a `PATH` that has python3 and git but no `last` (a scratch dir of symlinks): the exit code is still 0 and no note appears. The within-hour note, the 3600-second cutoff, the UTC reason and the once-per-invocation rule are covered in `tests/test_status.py` with `last` stubbed through `fake_run`.

11. `this-repo ok`. From the checkout root, `bin/marestail status` exits 0, shows Repo `marestail-green`, and lists no repo under `mutants/` or `tools/`. `bin/marestail status --all` shows `019-tasks-say-what-they-depend-on` as `complete` with text matching `^\d+ steps, archived$` (a real legacy pipeline.log). The row for `021-status-says-where-every-run-stands` is events-era (`--json` shows `events` non-null): `running` while this pipeline is still alive, `complete` with `<N> steps` after it. `--queued` lists every `tasks/*.md` stem with no run or handoffs folder — at minimum `022-queue-runs-tasks-in-dependency-order` — and never `README`. From the `marestail/` subfolder, `bin/marestail status` with no PATH still scans this repo. `bin/marestail status --json` parses, `v` is 1, and every record has the 15 keys in order.

12. `untouched ok`. This task's commits change no file under `marestail/tui/` or `.marestail/phases/`, and `.marestail/phases/sources/marestail-runs.py` is still on disk, unmodified. `marestail run`, `marestail watch`, `marestail tasks check` and `marestail gate --tier fast` behave as before. The README has a `## Status` section between `## Watch` and `## Dashboard` naming both command lines, the seven states, the scan rule, the `"v": 1` JSON shape and what `abandon` moves and writes and how to undo it; no new `MARESTAIL_` variable appears. `.importlinter` lists `marestail.status` on the layer line that holds `marestail.timeline : marestail.ran_against` and in `foundations-import-nothing-above`.

13. `pytest ok`. `.venv/bin/pytest tests/test_status.py tests/test_cli.py tests/test_green_repo.py::test_help_lists_every_subcommand tests/test_events.py::test_existing_commands_keep_text -q --tb=line` exits 0. Those two help tests, plus `test_help_lists_commands_in_order`, expect `status,abandon` after `watch`. `test_help_lists_remote_and_status_and_queue_stay_absent` keeps only `queue` absent. The status tests are in-process and hermetic: fixtures under `tmp_path`, the process list stubbed with `monkeypatch`, `git` and `last` stubbed through `fake_run` or run only in tmp repos, `time.time` and `time.strftime` patched where ages or stamps are asserted, and no real process started or signalled.

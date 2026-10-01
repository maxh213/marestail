# 020 — runs write events: one line at every step boundary in `.marestail/runs/<task>/events.jsonl`

After this task, every `marestail run` appends one JSON line to `.marestail/runs/<task>/events.jsonl` when it starts, when each worker or judge attempt starts and ends, when it waits out a rate limit, when it records a frozen-file proposal, and when it ends. A script can then say where a run stands, which commit each step left, why it stopped and whether it is still going, without parsing stdout or guessing from handoff file names. A scheduler that runs marestail on several machines needs exactly this; so does `marestail status` (021) and `marestail queue` (022), which are built on it.

Today a run leaves no machine-readable record of its own progress. `timeline.json` (003) holds one object per finished attempt, but has no run start, no run end, no stop reason, no pid, and nothing while an attempt is still running. The marestail-runs skill (`.marestail/phases/sources/marestail-runs.py:40-47`) works it out from stdout lines with regexes.

Line numbers below are green's at `5e9911d`. Phases 018 and 019 land first and may shift them; the function names hold.

## Where the file lives

- `state.folder / "events.jsonl"`, where `Run.folder` is `config.work / "runs" / self.task_name` (`marestail/runner.py:163-165`), `config.work` is `<repo>/.marestail` (`marestail/config.py:22-24`) and `task_name` is `self.task.stem` (`runner.py:143-144`). The folder is already keyed by task id, the file name without `.md`, which is 019's task id.
- The runner only ever appends. Nothing truncates, moves or archives it. `archive_handoffs` (`runner.py:1138-1143`) moves the handoffs and `start-commit` on completion, and leaves `events.jsonl` where it is, as it leaves `timeline.json`.
- Add `".marestail/runs/**/events.jsonl"` to `GATE_CONFIG` in `marestail/freeze.py`, next to the two timeline entries at lines 81-82, so no worker may keep an edit to it.

## Line format

One JSON object per line, `json.dumps` with default separators, then `\n`. Keys in this order: `v`, `at`, `event`, `pid`, then the event's own fields in the order the tables below list them. Every field is always present; a field with no value is `null`, never left out.

| key | value |
|---|---|
| `v` | `1` |
| `at` | UTC ISO-8601 with milliseconds and `Z`, from `timeline.utc_now()` (`marestail/timeline.py:16-17`), e.g. `2026-09-28T14:03:07.412Z` |
| `event` | one of `run-start`, `step-start`, `step-end`, `wait`, `proposal`, `run-end` |
| `pid` | `os.getpid()` of the runner. `bin/marestail` `exec`s python (`bin/marestail:4`), so this is the pid a caller started. |

Paths in events are repo-relative with `/`, as `git` prints them, unless the file is outside the repo, in which case they are absolute.

## Events

### `run-start`

Written once, in `run_pipeline` (`runner.py:190-213`), after `make_run` and `perf_trees.record_start` (line 208) and before `run_steps` (line 209). A run that dies earlier, for example on a bad `--model`/`--agent` pair (`pick_model`, line 250), a bad scope (lines 268-280) or 018's bad nice value, writes no event at all.

| field | value |
|---|---|
| `task` | `state.task_name`, e.g. `020-runs-write-events` |
| `task_path` | the task file, e.g. `tasks/020-runs-write-events.md` |
| `branch` | `git branch --show-current`; `null` on a detached HEAD or outside a repository |
| `base` | `config.get("git", "base", "origin/master")`, the same default `prompts.py:172` and `perf/trees.py:88` use |
| `start_commit` | `HEAD` when this run started. On a resume it is the resume point. The task's first start stays in `runs/<task>/start-commit`, which `record_start` (`perf/trees.py:76-81`) writes once per task. |
| `from` | the first role in the window `run_steps` is given, whether or not `--from` was passed: `specifier` for a plain run, `hardener` for `--from hardener` |
| `to` | the last role in that window: `qa` for a plain run, `hardener` for `--to hardener`. Under `--scope hyper` the window is the reduced pipeline (`pipeline.py:39-47`), so `from` and `to` are among its roles. |
| `scope` | `hyper` when `state.hyper`, `hard` when `state.hard`, `changed` when `state.scope_changed`, otherwise `all`. Do not use `state.scope_name`: it says `changed` for an unscoped run (`runner.py:331-334`). |
| `agent` | `resolve_agent(state)` (`runner.py:1245-1254`); `null` when the run is routed (`state.route` set), because then the backend is picked before every session |
| `model` | `state.route` when routed (e.g. `dandelion/route`), else `state.model`; `null` when neither `--model` nor `[agent] model` gave one |
| `effort` | `state.effort`; `null` when unset |

### `step-start` and `step-end`

One pair per attempt, for workers and judges alike. Every retry, every bounce's rework and every perf authoring request is a new attempt with its own pair.

`step` is the report stem, `report.stem`, e.g. `07-coder`. It is the text in brackets in the stdout line `== coder (07-coder) attempt 2` (`runner.py:483` for workers, `578` for judges), which is what main's `pipeline.log` captures, the handoff's file name without `.md` (`next_report`, `runner.py:171-174`), and the stem of `runs/<task>/07-coder.json` and `.prompt.md`. The number is not unique: a worker attempt that writes no handoff leaves the count unchanged, so its retry reuses the stem, and a run that completes archives the handoffs, so the next run of that task starts again at `01`.

`step-start`: `step`, `role`, `kind` (`worker` or `judge`), `attempt` (1-based, as printed).

- Workers: in `worker_attempt` (`runner.py:478-503`), with `at` set to the `started_at` it already takes at line 480.
- Judges: in `judged` (`runner.py:573-586`), with `at` set to the `started_at` from line 575.

So `step-start.at` equals the attempt's `started_at` in `timeline.json`, and a reader joins an attempt's events to its timeline entry, gate results and agent minutes included, on `step`, `attempt` and that timestamp.

`step-end`: `step`, `role`, `attempt`, `outcome`, `bounce_to`, `head`, `handoff`, `seconds`.

- `at` is the attempt's `ended_at` in `timeline.json`. Today `timeline.record` makes that stamp inside itself (`timeline.py:44`) and returns nothing. Make `record` return the step it built, and take `ended_at` from it. `seconds` is `ended_at - started_at`, rounded to 3 places. There is no second clock.
- `head` is `head(state.config)` (`runner.py:1146-1148`), read after `record_attempt`, so after every commit the attempt makes:
  - a worker's accepted attempt ends in `fold_handoff` (`runner.py:1072-1081`), which either makes an empty `<role> handoff` commit when the worker committed nothing (line 1075, `--allow-empty` at 1093) or amends the worker's last commit, after `stamp_history` may have rewritten the attempt's commits and reset to them (line 1113) and `untrack_ignored` may have amended once already (line 1056). The SHA before `fold_handoff` is gone; only the one after is real.
  - a judge's verdict is always a commit, `--allow-empty`, made by `commit_verdict` (`runner.py:708-714`), so a PASS that changed nothing still moves `head`.
  - a rejected worker attempt keeps whatever it committed; a refused frozen-file change adds the runner's revert commit (`runner.py:908` or `911-916`).
  - a judge attempt with no verdict, or a rejected perf verdict, commits nothing; `head` is unchanged. Perf authoring rounds commit `perf-author-N benches` (`runner.py:744`) inside the perf attempt, so they are in its `head`.
- `handoff` is the report path, e.g. `.marestail/handoffs/<task>/07-coder.md`, when the file exists at step end, else `null`. It is where the file was then; a completed run moves it (see `archive` in `run-end`).
- `bounce_to` is set only when `outcome` is `bounce`, else `null`.

`outcome`, by kind:

| kind | outcome | when |
|---|---|---|
| worker | `done` | `worker_attempt` found no problems (`runner.py:498`) |
| worker | `failed` | the attempt had problems and `run_worker` will try the same role again |
| worker | `stopped` | the attempt had problems and `run_worker` returns `False` after it: the repeat guard tripped (`runner.py:470-474`), or it was the last attempt `--retries N` allows (`runner.py:475`) |
| judge | `pass` | `commit_verdict` committed `PASS` |
| judge | `bounce` | `commit_verdict` committed `BOUNCE`; `bounce_to` is `target or judge.bounce_to`, the role `rework` runs (`runner.py:434-436`) |
| judge | `failed` | no verdict (`runner.py:628-630`), a perf verdict the measurements rejected (`runner.py:686-688`), or perf asking to author (`runner.py:625-626`, `583-585`); the judge runs again |

Consequences worth stating, because they are easy to get wrong:

- Worker `step-end` is written where `run_worker` (`runner.py:460-475`) knows what follows, not inside `worker_attempt`, since only `run_worker` knows whether the problems end the run.
- A judge's gate failing is a `bounce` to `judge.bounce_to`: `gated_verdict` drops the target (`runner.py:677-678`). A worker's gate failing is `failed` (`gate_problems`, `runner.py:890-894`). Which gate failed, and how long it took, is in the matching `timeline.json` entry; events do not copy it.
- A judge that stops for a human ends its last attempt `bounce`, because that verdict was committed. The stop is the `run-end` that follows. The same holds for a bounce `rework` cannot run because the target is not a worker (`runner.py:436`).
- When `--retries N` runs out on a judge with no verdict, `run_judge` makes up a bounce without a commit (`runner.py:569-570`). That writes no event. The last attempt's `failed` is followed by the rework's `step-start`.
- The judge's gate runs once in `run_judge` (`runner.py:563`), before the first attempt's `step-start`. A long hardener gate therefore shows as time after the previous `step-end`. Do not move the gate or the timeline's `started_at` to change that.
- The perf judge's two phases share one attempt. Authoring rounds (`author_phase`, `runner.py:725-733`) and sample filling run inside `measuring` after the perf `step-start`, and `perf-author-N` is not a step: it writes no `step-start` or `step-end` of its own, only `wait` lines if it is rate limited.
- A skipped judge writes nothing. `run_step` prints `practices disabled in marestail.toml; skipping` or `practices: no guidance files; skipping` and returns (`runner.py:340-343`, `385-398`); `perf` the same when disabled. No handoff number is taken and no agent runs, so there is no step to report. Roles outside the window, and the three `--scope hyper` drops (`pipeline.py:40`), write nothing either.

### `wait`

`reason`, `until`.

Written in `note_wait` (`runner.py:1173-1174`), the one place both waits already pass through: the rate limit in `run_session` (`runner.py:1197-1198`) and a dandelion route that picked nothing in `invoke_once` (`runner.py:1165-1167`). `reason` is the same string `timeline.json` records in `waits`: `rate-limit` or `dandelion-unrouted`. `until` is `at` plus `LIMIT_WAIT_SECONDS` (`runner.py:54`, `MARESTAIL_LIMIT_WAIT_SECONDS`), in the same format. It falls between the attempt's `step-start` and `step-end`, so it needs no `step`. `   <label>: still rate limited after 12 waits` (`runner.py:1159`) writes nothing more.

### `proposal`

`step`, `path`, `files`.

Written in `reject_config_change` (`runner.py:902-922`) right after `proposal.write_text(body)` (line 918), only on the branch that records a proposal: the worker changed frozen files, left the tree clean and wrote a `## Config change` section. `step` is the worker attempt's `report.stem`, `path` the proposal file (`.marestail/handoffs/<task>/NN-proposal.md`, from `next_report("proposal")`), `files` the list `frozen`. It comes between that attempt's `step-start` and its `step-end`, which is `failed` or `stopped`. A frozen change reverted without a justification (line 908) is not a proposal and writes no `proposal` line.

### `run-end`

`result`, `exit_code`, `head`, `stopped_at`, `reason`, `archive`.

`head` is `head(state.config)` at the time. `archive` is the repo-relative folder `archive_handoffs` moved the handoffs to (`.marestail/runs/<task>/handoffs-<stamp>`) when the result is `complete` and there were handoffs to move, else `null`; make `archive_handoffs` return it. A reader finds a completed step's handoff at `<archive>/<step>.md`.

`stopped_at` is always a role from the run's window, the step `run_steps` (`runner.py:283-290`) was on, so `marestail run <task> --from <stopped_at>` is a valid resume. When a hardener's rework coder stops, `stopped_at` is `hardener`, matching today's `pipeline stopped at hardener`, and `reason` names the coder. Keep the current window step on the `Run` so the exception paths can read it.

| how the run ends | where | `result` | `exit_code` | `stopped_at` | `reason` |
|---|---|---|---|---|---|
| every step passed, QA not in the window or QA said `app` | `complete_pipeline`, `ending_for` (`runner.py:293-317`) | `complete` | `0` | `null` | `null` |
| QA said `harness` or `nothing` (005) | the same | `complete` | `3` | `null` | the last line printed, e.g. `pipeline complete, NOT verified against the running app (qa ran against a harness)` |
| a step returned `False` | `run_steps`, `runner.py:285-287` | `stopped` | `1` | that step | see below |
| the approval after the critic was declined | `paused`, `runner.py:288-289`, `320-321` | `stopped` | `1` | `critic` | `not approved` |
| an exception escaped `run_steps` | `run_pipeline` | `failed` | `1`, or the integer code of a `SystemExit` | the current step, or `null` | `<ExceptionType>: <first line of the message>` |
| SIGINT | `run_pipeline` | `interrupted` | `130` | the current step, or `null` | `SIGINT` |
| SIGTERM | `run_pipeline` | `interrupted` | `143` | the current step, or `null` | `SIGTERM` |

`reason` for `stopped` is the stop line the runner printed, word for word, kept on the `Run` at the moment it prints it:

| cause | reason |
|---|---|
| worker repeat guard, `runner.py:471-473` | `coder got the same problems back 3 times in a row; the worker is not making progress, stopping for a human` |
| judge repeated findings, `runner.py:428` | `hardener repeated the same findings twice; the worker is not making progress, stopping for a human` |
| judge bounce limit, `runner.py:430` | `<judge> still bouncing after <n> rounds; stopping for a human` |
| worker out of `--retries N` (prints nothing today) | `<role> used all <N> attempts` |
| bounce target is not a worker (prints nothing today) | `<judge> bounced to <target>, which is not a worker` |
| judge loop hit `MAX_JUDGE_ROUNDS` (`runner.py:401-410`, prints nothing today) | `<judge> ran 1000 rounds without a pass` |

The last three are new strings in the event only. Stdout does not change.

`run-end` is written after the ending line is printed (`pipeline complete…` or `pipeline stopped at …`) and before `proposals_summary` is printed (`runner.py:210`).

## Signals

Today green installs no signal handler anywhere in the runner; the only `signal` use in `marestail/` is the qa gate stopping the app's process group (`marestail/gates/_serve.py:3`, `109`, `117`).

- SIGINT raises `KeyboardInterrupt` in the main thread. `subprocess.run`, which `shell.run` wraps (`marestail/shell.py:24-33`), kills the agent child and re-raises; nothing in the runner catches it, Python prints a traceback, and the shell sees 130.
- SIGTERM takes the default action: the runner dies at once, no `finally` runs, and a running agent child is left orphaned and keeps working and committing on the branch.
- SIGKILL, an OOM kill or power loss cannot be caught.

Change: `run_pipeline` writes a best-effort `run-end` with `result` `interrupted` for SIGINT and SIGTERM.

- Just before `run-start`, install a SIGTERM handler that raises a `BaseException` subclass carrying the signal number, and restore the previous handler in a `finally` when `run_pipeline` returns or raises. A `BaseException` so no `except Exception` swallows it.
- On `KeyboardInterrupt`: write `run-end` (`130`, `SIGINT`) and re-raise, so the exit is as today.
- On the SIGTERM exception: write `run-end` (`143`, `SIGTERM`), then `raise SystemExit(143)`. The exception unwinds through `subprocess.run`, which kills the agent child, and through `measuring`'s `finally`, which releases the perf database (`runner.py:612-617`). This is a deliberate change: an agent no longer outlives a terminated runner.
- Any other exception: write `run-end` `failed` and re-raise it unchanged.

A killed runner leaves no `run-end`. Readers infer a dead run from the pid: the latest `run-start` has no `run-end` and its `pid` is not alive, or is alive but is not a `marestail run` of that task (pids are reused). The last `step-start` without a `step-end` names the step that was running.

## Writing

- A new module, `marestail/events.py`, imports only the standard library and `marestail.shell` (for `ensure_dir`). It takes `at` from the caller and has no clock of its own; the runner passes `timeline.utc_now()`. Put it in the `.importlinter` layer with `marestail.report : marestail.config : marestail.changes : marestail._location`, add it to `foundations-import-nothing-above`, and add a forbidden contract for it modelled on `timeline-stays-below-runner`.
- The writer lives on the `Run`, so tests can read what it wrote and the warn-once flag is per run.
- Each line is one `os.write` of the encoded line to a descriptor opened with `O_WRONLY | O_APPEND | O_CREAT` (mode `0o644`), then `os.fsync`, then close. The folder is created if missing. One write call per line with `O_APPEND` keeps lines whole even if two runners of the same task share the file.
- A failed write (`OSError`) prints once per run, on stderr, `warning: cannot write run events to <path>: <error>`, and the run carries on. Later events are still attempted and still silent on failure. Nothing an events write does may change stdout, the exit code or the pipeline's path.
- marestail only writes this file. It pushes nothing, copies nothing and reads nothing back during a run.

## Resumed runs

The file is appended across resumes and reruns. A run is the `run-start` line and every later line with the same `pid`. The latest run of a task is the one that begins at the last `run-start` in the file. A `run-start` followed by another `run-start` from a different pid with no `run-end` between them is a run that died. Readers must ignore unknown events and unknown fields, so later phases can add them without bumping `v`.

## README

In `## Overnight`, after the sentence about `timeline.md` and `timeline.json` (`README.md:87`), or in a new paragraph under `## Pipeline`: `.marestail/runs/<task>/events.jsonl` gets one JSON line per run start, attempt start, attempt end, wait, proposal and run end; it is appended across resumes; the latest run starts at the last `run-start`; and a run with no `run-end` whose pid is gone was killed. List the six events and their fields in a short table. No environment variable is added, so the env table does not change.

## Must not break

- `marestail run` on a task file with no front matter.
- Every stdout line and its wording, including `== <role> (<step>) attempt N`, `finished in`, `verdict`, `rate limited; waiting`, `GATE FAILED`, `pipeline complete`, `pipeline stopped at`, the skip lines and the proposals summary. Green's runner does not write `pipeline.log` itself: main's does (`PipelineLog`, main's `marestail/runner.py:134` and `163-186`), and the `pipeline.log` files under green's `.marestail/runs/` were written by main's marestail, which the queue drivers run. Nothing here ports or changes that; keeping stdout identical keeps it working when it is ported.
- `timeline.md` and `timeline.json`: same keys, same order, same values. `record` gains a return value and nothing else.
- The handoff layout, the numbering from `next_report`, `*.json` and `*.prompt.md`, `start-commit`, and `archive_handoffs`' destination.
- Exit codes 0, 1 and 3 keep their meanings. SIGINT still exits as a `KeyboardInterrupt`.
- Every existing runner test. `pipeline_env` in `tests/test_runner_flow.py:140-168` builds a `Config` on a `tmp_path` that is not a git repository; `run-start` there must not raise (`branch` is `null`, `start_commit` and `head` are whatever `head()` returns). Tests that do not look at events may leave the file in `tmp_path`.
- The queue driver scripts in `.marestail/phases/`. Never edit, move or delete anything under `.marestail/phases/`: a live driver runs from there.

## Tests

`tests/test_events.py` for the module, and pipeline tests in the same file or `tests/test_runner_flow.py`. In-process and hermetic: no agent CLI, no real `marestail run`. Build the repo with conftest's `git_repo` and `commit_all`, as `tests/test_timeline.py:27-33` does, and call `run_pipeline` with `config_module.load` patched to return `Config(root=repo, raw={"git": {"base": "main"}})`, `--model m --agent claude`, `auto=True`, and 018's `nice.apply` stubbed. Replace `runner.invoke` with a scripted stub, as `invoke_write` does in `tests/test_timeline.py:337-351`, that per call writes the handoff or verdict file, commits what the scenario needs, and sets `state.attempt_agent`. Run the window `--from specifier --to critic`, with `retries` 0: both roles have no gate tier (`pipeline.py:27-28`), so no gate runs. Where a test's window includes a role with a tier or the coder's audit, patch `runner.run_gates` and `audit.problems` to return `[]`.

Assert the exact list of `(event, step, outcome or result)` tuples, then the fields that matter:

- **pass**: specifier writes `features/x.feature` and its handoff; critic writes `VERDICT: PASS`. Sequence: `run-start` (`from` `specifier`, `to` `critic`, `scope` `all`, `agent` `claude`, `model` `m`, `branch` `main`, `start_commit` = HEAD before the run), `step-start 01-specifier worker`, `step-end 01-specifier done`, `step-start 02-critic judge`, `step-end 02-critic pass`, `run-end complete` with `exit_code` 0, `stopped_at` and `reason` `null`, and `archive` = the moved folder. Each `step-end.head` equals `git rev-parse HEAD` read inside the stub of the next attempt (and the final HEAD for the last); the critic's `head` is a new empty verdict commit; `01-specifier`'s `handoff` is `.marestail/handoffs/<task>/01-specifier.md`.
- **bounce**: critic writes `VERDICT: BOUNCE` with finding `1. scenario missing`, then `VERDICT: PASS`. Sequence: specifier done, `02-critic bounce` with `bounce_to` `specifier`, `03-specifier done`, `04-critic pass`, `run-end complete`.
- **stop**: critic bounces twice with the same numbered findings. Sequence ends `04-critic bounce`, then `run-end stopped`, `exit_code` 1, `stopped_at` `critic`, `reason` exactly the line `runner.py:428` prints.
- **worker stop**: specifier never writes a handoff. Three `step-start 01-specifier` (attempts 1, 2, 3), `step-end` outcomes `failed`, `failed`, `stopped`, then `run-end stopped` with the repeat-guard line as `reason`.
- **proposal**: the specifier's first attempt commits a change to `marestail.toml` and a handoff with a `## Config change` section; its second attempt behaves. Sequence: `step-start 01-specifier`, `proposal` (`step` `01-specifier`, `path` `.marestail/handoffs/<task>/02-proposal.md`, `files` `["marestail.toml"]`), `step-end 01-specifier failed` whose `head` is the runner's revert commit, `step-start 03-specifier`, `step-end 03-specifier done`, then the critic's pair and `run-end complete`.

And, each in one test:

- every line has `v` 1, `at` matching `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z`, the listed `event`, `pid` = `os.getpid()`, and exactly the listed keys in the listed order.
- `step-start.at` and `step-end.at` equal the matching `timeline.json` step's `started_at` and `ended_at`; `seconds` is their difference.
- a rate limit (`run_backend` returning `(1, "rate limit exceeded")` once, `LIMIT_WAIT_SECONDS` 0) writes `wait` with `reason` `rate-limit` and `until` = `at` between that attempt's `step-start` and `step-end`; a dandelion miss writes `dandelion-unrouted`.
- a disabled `practices` writes no event, and the skip line is still printed.
- a perf attempt that asks to author ends `failed`; the authoring round writes no `step-start`.
- a hardener's rework coder hitting the repeat guard ends the run `stopped` with `stopped_at` `hardener`.
- a declined approval (stdin a tty, `input` returning `n`, `auto` false) ends `stopped` at `critic` with `not approved`.
- QA ending on `ran-against: harness` ends `complete` with `exit_code` 3 and the `NOT verified` line as `reason`.
- `KeyboardInterrupt` raised from the stub writes `run-end interrupted`, 130, `SIGINT`, with `stopped_at` the current step, and the `KeyboardInterrupt` still propagates.
- SIGTERM: the stub calls `signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)`; `run-end interrupted`, 143, `SIGTERM`; `run_pipeline` raises `SystemExit(143)`; afterwards `signal.getsignal(signal.SIGTERM)` is what it was before. Never send a real signal.
- a `RuntimeError("boom")` from the stub writes `run-end failed`, 1, `RuntimeError: boom`, and re-raises.
- a bad `--model`/`--agent` pair raises its own message and leaves no `events.jsonl`.
- two `run_pipeline` calls on one task append: the file has two `run-start` lines and the second run's lines follow the second one.
- `events.jsonl` being a directory: the run returns the same exit code and prints the same stdout as with events working, and stderr has the warning exactly once.
- `freeze.frozen_paths` freezes `.marestail/runs/t/events.jsonl` for the coder, next to `test_coder_cannot_keep_timeline_artefacts` (`tests/test_timeline.py:545-554`).

## Done when

`tests/test_events.py` and the extended runner tests pass with every branch of `events.py` and the new runner code covered, the existing tests pass unchanged apart from `pipeline_env` if it needs a stub, `.importlinter` lists `marestail.events`, `freeze.py` freezes `events.jsonl`, and the README documents the file.

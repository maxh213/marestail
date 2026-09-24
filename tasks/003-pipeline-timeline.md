# 003 — a durable per-step timeline: how long each role took and what it did

After this task, every `marestail run` writes `.marestail/runs/<task>/timeline.md` and `.marestail/runs/<task>/timeline.json` that a stranger can read after the process is gone and answer: which role ran, which attempt, how long the gate took, how long the agent took, which model, the verdict, the commits, and a one-line what-was-done. `marestail watch` and the existing stdout lines (`== role (NN-role) attempt N`, `finished in X.Y min: …`, `verdict PASS`) stay exactly as they are; this is an extra artefact, not a rewrite of the live log.

Today that answer is reconstructable only by grepping `pipeline.log` / `overnight-*.log` (agent minutes only, truncated JSON blurb, no gate timings, no commits) and by reading handoffs and `git log`. `tools/overnight.sh` writes `.marestail/runs/overnight-<stamp>.md` with one **task** total in minutes plus the last 40 `==`/`finished`/`verdict` lines — not a full step timeline, and only when the run was started by overnight.sh.

## What to record, in order, for every worker and judge attempt

One section (markdown) / one object (JSON) per attempt, appended as the attempt finishes (so a crash still leaves the previous steps):

- `id` — the report stem (`19-cleaner`)
- `role` — specifier, critic, coder, cleaner, architect, practices, perf, hardener, qa
- `attempt` — 1-based
- `started_at` / `ended_at` — UTC ISO-8601
- `gate` — list of `{name, seconds, ok}` for every gate `gate_for` actually ran in that attempt (empty when the role has no gate). Sonar at 41s FAIL must show up here; it must not be invisible because it printed before `== cleaner`.
- `waits` — list of `{reason, seconds}` for rate-limit / dandelion-unrouted sleeps during the attempt
- `agent` — `{backend, model, effort, account, minutes, summary}` when a session ran; `summary` is the same one-line `describe(output)` already printed. Omit `agent` when the attempt never invoked (unlimited-retry exhaustion with no session).
- `verdict` — `PASS` / `BOUNCE` / `AUTHOR` and bounce target when that is what the role writes; omit for workers
- `commits` — `{hash, subject}` for every commit this attempt made on the task branch (empty if none)
- `files` — paths those commits touched
- `done` — one line: first paragraph of the handoff if it exists, else the first commit subject, else the agent summary. Not an essay.

`timeline.md` is human-readable sections in that order. `timeline.json` is `{"task": "<stem>", "steps": [ … ]}` with the same fields. Both are created on the first event and appended/rewritten as a whole JSON document after each attempt so they never disagree.

## Where it is written

- Path: `state.folder / "timeline.md"` and `timeline.json` (already `.marestail/runs/<task>/`).
- `tools/overnight.sh` appends the task's `timeline.md` under that task's `###` section in `overnight-<stamp>.md`, after the existing exit/HEAD/grep lines, so overnight summaries stop being a 40-line tail.
- Frozen like other run artefacts: workers may not edit `timeline.md` / `timeline.json`; the runner writes them. Add the names to freeze if a worker could otherwise clobber them.

## What must not change

- Every existing stdout line and its wording, including `==`, `finished in`, `verdict`, `rate limited; waiting`, `dandelion/route:`, `GATE FAILED`, `pipeline complete`, `pipeline stopped at`.
- Handoff and `*.json` / `*.prompt.md` layout.
- Gate behaviour, freeze rules, prompts, flags, backends.
- `marestail watch` parsing of `==` / `finished in` (it may *also* read `timeline.json` later; that is out of scope for this task unless it is a two-line change to prefer timeline for elapsed time).

## Tests

`tools/test-timeline.py` (or an extension of `tools/test-watch.py`) in a temp repo with the stub agent:

- a coder attempt that commits one file writes a timeline step with `role=coder`, `attempt=1`, non-empty `commits`/`files`/`done`, and `agent.minutes >= 0`
- a judge whose gate fails still records `gate` with `ok=false` and the gate name, then the agent step
- a rate-limit then success records a `waits` entry
- stdout still contains `== coder (` and `finished in`
- after two attempts, `timeline.json` has two steps and `timeline.md` has two `##` sections
- overnight.sh's summary file contains a `timeline.md` heading or the step `id` from the run

## Done when

`python3 tools/test-timeline.py` passes, `python3 tools/test-watch.py` still passes, a `marestail run` of a one-role window in the temp repo leaves `timeline.md` and `timeline.json` that match the schema above, and README documents the two files next to the `pipeline.log` paragraph.

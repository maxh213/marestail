# 005 — a run that was not checked against the running app does not end as "pipeline complete"

After this task, someone reading the last line of a `marestail run` knows whether QA exercised the real app. Today they cannot. On otwarteklatki/next-boilerplate#537 the QA step wrote in its handoff "Live CARE careconf.eu/donate was not opened… A human should still do steps 1 and 3", built a fake page instead, and the run printed `pipeline complete` and exited 0. The change was merged. On the real page it rendered a 425px widget 1408px wide over the page text.

QA is a worker, so it cannot bounce, and nothing reads its handoff. This task makes QA state one fact in a form the runner can read, and makes the runner act on it.

## What changes

- The QA handoff must contain one line, exactly `ran-against: app`, `ran-against: harness` or `ran-against: nothing`.
  - `app` means the QA procedure in `qa/<task>.md` was exercised against the project's running application.
  - `harness` means it was exercised against a stand-in the QA role built or reused (a static page, a stub server, a component rendered alone).
  - `nothing` means no step of the procedure was exercised.
- `roles/qa.md` says so, in one sentence, and says QA must not claim `app` for a stand-in.
- The runner reads that line after QA's handoff is accepted. A missing or unreadable line counts as `nothing`, and QA is retried once with that as the finding before the run settles on `nothing`.
- The last line of the run and the exit code:
  - `app`: `pipeline complete` and exit 0, exactly as now.
  - `harness`: `pipeline complete, NOT verified against the running app (qa ran against a harness)` and exit 3.
  - `nothing`: `pipeline complete, NOT verified against the running app (qa ran against nothing)` and exit 3.
- `tools/overnight.sh` treats exit 3 as a stop, like any non-zero exit, and its summary file shows the `NOT verified` line under that task.
- A run that stops before QA (`--to hardener`, the overnight default) is unchanged: it never claimed QA ran.
- `marestail watch` shows the `NOT verified` ending on the bed's row instead of a plain finished state.

## What must not change

- QA stays a worker. It still fixes only its own test harness and still reports product defects in the handoff without fixing them.
- Every other stdout line and its wording, including `== qa (`, `finished in`, `pipeline stopped at`.
- Exit codes 0, 1 and 2 keep their current meanings. 3 is new and means only this.
- The `qa` tier and the `qa` gate.
- A target with no `[qa] cmd` behaves as it does today up to the last line; QA still has to write the `ran-against` line.

## Tests

`tools/test-qa-ran-against.py`, in a temp repo with the stub agent:

- a QA handoff with `ran-against: app` ends `pipeline complete`, exit 0
- `ran-against: harness` ends with the `NOT verified … harness` line, exit 3
- `ran-against: nothing` ends with the `NOT verified … nothing` line, exit 3
- a handoff with no such line retries QA once, then ends as `nothing`
- a handoff that says `ran-against: app` in a sentence, not on its own line, counts as missing
- `--to hardener` prints the same last line as today and exits 0
- `overnight.sh` stops after a task that exits 3 and its summary contains `NOT verified`

## Done when

The new test passes, the existing `tools/test-*.py` still pass, `roles/qa.md` carries the sentence, and README's pipeline section says what the three values mean and that exit 3 exists.

# QA procedure: Durable per-step pipeline timeline

1. `cd` to the marestail-green repo root with `.venv` active.
2. `python3 tools/test-timeline.py`
   Expected: exit `0`, last line contains `ok`.
3. `python3 tools/test-watch.py`
   Expected: exit `0`, last line contains `ok`.
4. In a temp repo (same layout as `tools/test-audit.py` / `tools/dryrun.sh`: stub agent via `MARESTAIL_CLAUDE`, git user set, `.marestail/` gitignored), run:
   `marestail run tasks/t.md --from coder --to coder --auto --retries 1`
   with a stub that commits `src.py` and writes handoff first paragraph `coded the adder`.
   Expected: stdout has `== coder (` and `finished in`; `.marestail/runs/t/timeline.json` has `task` `"t"` and one step with `role` `"coder"`, `attempt` `1`, non-empty `commits`/`files`/`done`, `agent.minutes >= 0`; `.marestail/runs/t/timeline.md` has one `##` section for that step id.
5. Force a judge attempt whose `gate_for` includes `sonar` at 41.0s with `ok` false (stub or test harness).
   Expected: that step's `gate` lists `{name: "sonar", seconds: 41.0, ok: false}`.
6. With `MARESTAIL_LIMIT_WAIT_SECONDS=0`, run a worker whose first session is rate-limited then succeeds.
   Expected: step `waits` includes `{reason: "rate-limit", seconds: 0}`; stdout still has `rate limited; waiting`.
7. Cause two attempts of the same role.
   Expected: `timeline.json` has two steps; `timeline.md` has two `##` sections; ids match.
8. From a temp repo that already has `.marestail/runs/t/timeline.md`, run `tools/overnight.sh tasks/t.md` (stub agent, `STOP_AT` short).
   Expected: `overnight-*.md` still has exit/HEAD/grep lines under `### tasks/t.md`, and also embeds that timeline (heading `timeline.md` or a step id).
9. `grep -E 'timeline\.(md|json)' README.md` near the `pipeline.log` mention.
   Expected: both `timeline.md` and `timeline.json` named next to `.marestail/runs/<task>/pipeline.log`.
10. Confirm freeze lists the timeline artefacts (e.g. `grep -E 'timeline\.(md|json)' marestail/freeze.py` or equivalent config).
    Expected: a worker cannot keep a commit that only clobbers those files.
